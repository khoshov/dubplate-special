from __future__ import annotations

"""
RecordService — фасад-оркестратор операций с записями:
  - импорт из Discogs (по штрих-коду или каталожному номеру);
  - импорт из Redeye (по каталожному номеру);
  - загрузка обложки;
  - upsert источника RecordSource;
  - привязка аудио-превью к трекам через AudioService;
  - парсинг карточки Redeye по прямому URL (без создания записи).

Сборка записи из нормализованных данных вынесена в:
  - records.services.record_assembly.build_record_from_payload

Адаптация «сырого» payload провайдера к нашему контракту:
  - records.services.provider_payload_adapter.adapt_redeye_payload
"""

import logging
from typing import Optional, Tuple, List

from django.db import transaction

from records.models import (
    Artist,
    Format,
    Genre,
    Label,
    Record,
    RecordConditions,
    Style,
    Track,
    RecordSource,
)

from records.services.audio.audio_service import AudioService
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.image.image_service import ImageService
from records.services.providers.redeye.redeye_service import RedeyeService
from records.services.providers.redeye.helpers import validate_redeye_product_url

from records.services.record_assembly import build_record_from_payload
from records.services.provider_payload_adapter import adapt_redeye_payload

logger = logging.getLogger(__name__)

DEFAULT_NAME = "not specified"


def _get_or_create_default(model_cls):
    """Хелпер для «дефолтного» значения в словарях (жанры/стили и т.д.)."""
    obj = model_cls.objects.find_by_name(DEFAULT_NAME)
    if not obj:
        obj = model_cls.objects.create(name=DEFAULT_NAME)
    return obj


class RecordService:
    """
    Сервис реализует высокоуровневую оркестрацию операций над записью.

    В конструктор передаются зависимости (Discogs, Image, Redeye, Audio),
    чтобы упростить тестирование и конфигурирование.
    """

    def __init__(
        self,
        discogs_service: DiscogsService,
        image_service: ImageService,
        *,
        redeye_service: RedeyeService,
        audio_service: Optional[AudioService] = None,
    ) -> None:
        self.discogs_service = discogs_service
        self.image_service = image_service
        self.redeye_service = redeye_service
        # --- добавлено ---
        self.audio_service: AudioService = audio_service or AudioService()  # --- добавлено ---

    def import_from_discogs(
        self,
        barcode: Optional[str] = None,
        catalog_number: Optional[str] = None,
        save_image: bool = True,
    ) -> Tuple[Record, bool]:
        """
        Метод выполняет импорт записи из Discogs.

        Логика:
          1) Проверяет существующую запись по barcode/catalog_number;
          2) Ищет релиз в Discogs и парсит данные;
          3) Создаёт запись и связи; при необходимости — загружает обложку.

        Args:
            barcode: Штрих-код для поиска.
            catalog_number: Каталожный номер для поиска.
            save_image: Флаг загрузки обложки.

        Returns:
            (record, created): created=True если создана новая запись,
                               False — если возвращена существующая.
        """
        # 0) Ищем уже существующую запись
        existing = self._find_existing_record(barcode, catalog_number)
        if existing:
            logger.info("Найдена существующая запись: %s", existing.id)
            self._update_missing_identifiers(existing, barcode, catalog_number)
            return existing, False

        if barcode:
            discogs_release = self.discogs_service.search_by_barcode(barcode)
        elif catalog_number:
            discogs_release = self.discogs_service.search_by_catalog_number(catalog_number)
        else:
            raise ValueError("Нужно указать barcode или catalog_number для импорта из Discogs.")

        if not discogs_release:
            raise ValueError("Релиз не найден в Discogs.")

        with transaction.atomic():
            record = self._create_record_from_discogs(
                discogs_release,
                search_barcode=barcode,
                search_catalog_number=catalog_number,
            )

            if save_image and discogs_release.images:
                success = self.image_service.download_cover(record, discogs_release.images[0]["uri"])
                if success:
                    logger.info("Обложка скачана для записи %s", record.id)

        logger.info("Импорт из Discogs выполнен: %s", record.id)
        return record, True

    def update_from_discogs(self, record: Record, update_image: bool = True) -> Record:
        """
        Метод обновляет существующую запись из Discogs.

        Обновляются:
          - основные поля (title, год, страна, заметки);
          - связи (артисты/лейбл/жанры/стили/форматы) — полная замена;
          - треки — полная замена;
          - обложка (если отсутствует и update_image=True).
        """
        if not record.discogs_id:
            raise ValueError("Для обновления из Discogs у записи должен быть discogs_id.")

        logger.info(
            "Начато обновление из Discogs для записи %s (Discogs ID: %s, Barcode: '%s', Catalog: '%s')",
            record.id,
            record.discogs_id,
            record.barcode,
            record.catalog_number,
        )

        discogs_release = self.discogs_service.get_release(record.discogs_id)
        if not discogs_release:
            raise ValueError(f"Релиз {record.discogs_id} не найден в Discogs.")

        with transaction.atomic():
            self._update_record_fields(record, discogs_release)
            self._update_record_relations(record, discogs_release=discogs_release)
            self._update_tracks(record, discogs_release)

            if update_image and not record.cover_image and discogs_release.images:
                if self.image_service.download_cover(record, discogs_release.images[0]["uri"]):
                    logger.info("Обновлена обложка для записи %s", record.id)

        logger.info("Обновление из Discogs завершено: %s", record.id)
        return record

    def import_from_redeye(
        self,
        catalog_number: Optional[str] = None,
        save_image: bool = True,
        *,
        download_audio: bool = True,
    ) -> Tuple[Record, bool]:
        """
        Метод выполняет импорт записи по каталожному номеру с сайта Redeye.

        Поток выполнения:
          1) Ищет карточку на Redeye и парсит «сырой» payload.
          2) Адаптирует payload к внутреннему контракту и собирает Record.
          3) Загружает обложку (опционально).
          4) Создаёт/обновляет RecordSource (provider=REDEYE, role=PRODUCT_PAGE).
          5) При необходимости — привязывает аудио-превью по порядку треков.

        Returns:
            (record, created): created=True при создании, False — если запись уже существовала.
        """
        if not catalog_number:
            raise ValueError("Не указан каталожный номер (catalog_number) для импорта из Redeye.")

        existing = Record.objects.find_by_catalog_number(catalog_number)
        if existing:
            logger.info("Найдена существующая запись по каталожному номеру (Redeye): %s", existing.id)
            if download_audio:
                try:
                    self.attach_audio_from_redeye(existing, force=False)
                except Exception as err:  # noqa: BLE001 — логируем любую ошибку привязки
                    logger.warning("Докачка аудио для существующей записи завершилась с ошибкой: %s", err)
            return existing, False

        result = self.redeye_service.fetch_by_catalog_number(catalog_number)
        raw_payload: dict = result.payload or {}

        # 2) Адаптируем вход к нашему контракту сборки
        payload = adapt_redeye_payload(raw_payload)
        payload["catalog_number"] = (catalog_number or "").strip().upper()

        with transaction.atomic():
            record = build_record_from_payload(payload)

            cover_url = raw_payload.get("image_url")
            if save_image and cover_url:
                if self.image_service.download_cover(record, cover_url):
                    logger.info("Обложка скачана для записи %s (Redeye)", record.id)

            source_url = (raw_payload.get("source") or {}).get("url") or result.source_url
            if source_url:
                try:
                    validate_redeye_product_url(source_url)
                    self._upsert_record_source(
                        record=record,
                        provider=RecordSource.Provider.REDEYE,
                        role=RecordSource.Role.PRODUCT_PAGE,
                        url=source_url,
                        can_fetch_audio=True,
                    )
                except ValueError as ve:
                    logger.warning("Валидация URL источника Redeye не пройдена: %s", ve)

        if download_audio:
            try:
                self.attach_audio_from_redeye(record, force=False)
            except Exception as err:  # noqa: BLE001
                logger.warning("Докачка аудио завершилась с ошибкой для записи %s: %s", record.pk, err)

        logger.info("Импорт из Redeye завершён успешно: %s", record.id)
        return record, True

    def attach_audio_from_redeye(
        self,
        record: Record,
        *,
        force: bool = False,
        per_click_timeout_sec: int = 20,
        page_url: Optional[str] = None,
    ) -> int:
        """
        Метод инициирует прикрепление аудио-превью из Redeye к трекам записи.

        Делегирует выполнение в `AudioService.attach_audio_from_redeye(...)`.

        Args:
            record: Запись для обновления треков.
            force: Если True — перезаписывает существующие превью.
            per_click_timeout_sec: Таймаут ожидания после клика по каждой кнопке плеера.
            page_url: Необязательная явная ссылка на карточку Redeye (валидируется).

        Returns:
            Количество треков, у которых появилось/обновилось превью.
        """
        if page_url:
            validate_redeye_product_url(page_url)

        logger.info("Запуск прикрепления аудио из Redeye для записи %s", record.pk)
        # --- изменено: используем инстанс сервиса, а не класс (для униформности DI) ---
        updated = self.audio_service.attach_audio_from_redeye(  # --- изменено ---
            record,
            force=force,
            per_click_timeout_sec=per_click_timeout_sec,
            page_url=page_url,
        )
        logger.info("Завершено прикрепление аудио из Redeye: обновлено %d треков (record=%s)", updated, record.pk)
        return updated

    def parse_product_by_url(self, url: str) -> dict:
        """
        Метод получает HTML карточки Redeye по прямому URL и возвращает распарсенные поля.

        Делегирует скачивание/разбор в `RedeyeService.parse_product_by_url`.

        Args:
            url: Абсолютный URL карточки Redeye.

        Returns:
            Словарь полей (title, artists, tracks, image_url, availability, ...).

        Raises:
            ValueError: Если URL пуст или не проходит валидацию.
        """
        clean = (url or "").strip()
        if not clean:
            raise ValueError("URL карточки Redeye не указан.")
        validate_redeye_product_url(clean)
        result = self.redeye_service.parse_product_by_url(clean)
        return result.payload


    def _find_existing_record(
        self, barcode: Optional[str], catalog_number: Optional[str]
    ) -> Optional[Record]:
        """Метод ищет существующую запись по идентификаторам."""
        if barcode:
            if record := Record.objects.find_by_barcode(barcode):
                return record
        if catalog_number:
            if record := Record.objects.find_by_catalog_number(catalog_number):
                return record
        return None

    @staticmethod
    def _is_empty_identifier(value: Optional[str]) -> bool:
        """Метод проверяет, является ли идентификатор пустым (None/''/пробелы)."""
        return value is None or (isinstance(value, str) and value.strip() == "")

    def _update_missing_identifiers(
        self,
        record: Record,
        barcode: Optional[str] = None,
        catalog_number: Optional[str] = None,
    ) -> None:
        """Метод заполняет недостающие barcode/catalog_number у записи (если были пусты)."""
        updated = False
        if barcode and self._is_empty_identifier(record.barcode):
            record.barcode = barcode
            updated = True
            logger.info("Добавлен недостающий barcode для записи %s: %s", record.id, barcode)

        if catalog_number and self._is_empty_identifier(record.catalog_number):
            record.catalog_number = catalog_number
            updated = True
            logger.info("Добавлен недостающий catalog_number для записи %s: %s", record.id, catalog_number)

        if updated:
            record.save()


    def _create_record_from_discogs(self, discogs_release, *, search_barcode: Optional[str], search_catalog_number: Optional[str]) -> Record:
        """Метод создаёт запись из объекта релиза Discogs."""
        record_data = self.discogs_service.extract_release_data(discogs_release)

        record = Record.objects.create(
            title=record_data["title"],
            discogs_id=discogs_release.id,
            release_year=record_data.get("year"),
            country=record_data.get("country"),
            notes=record_data.get("notes"),
            barcode=record_data.get("barcode") or search_barcode,
            catalog_number=record_data.get("catalog_number") or search_catalog_number,
            condition=RecordConditions.M,
            stock=1,
        )

        self._create_record_relations(record, discogs_release)
        self._create_tracks(record, discogs_release)
        return record

    def _update_record_fields(self, record: Record, discogs_release) -> None:
        """Метод обновляет основные поля записи по данным из Discogs."""
        record_data = self.discogs_service.extract_release_data(discogs_release)

        old_values = {
            "title": record.title,
            "year": record.release_year,
            "country": record.country,
            "catalog_number": record.catalog_number,
            "barcode": record.barcode,
        }

        record.title = record_data["title"]
        record.release_year = record_data.get("year")
        record.country = record_data.get("country")
        record.notes = record_data.get("notes")

        if self._is_empty_identifier(record.catalog_number) and record_data.get("catalog_number"):
            record.catalog_number = record_data["catalog_number"]
            logger.info("Добавлен недостающий catalog_number: %s", record.catalog_number)

        if self._is_empty_identifier(record.barcode) and record_data.get("barcode"):
            record.barcode = record_data["barcode"]
            logger.info("Добавлен недостающий barcode: %s", record.barcode)

        changes = []
        for field, old_value in old_values.items():
            new_value = getattr(record, field if field != "year" else "release_year")
            if old_value != new_value:
                changes.append(f"{field}: '{old_value}' → '{new_value}'")
        if changes:
            logger.info("Обновлены поля записи %s: %s", record.id, ", ".join(changes))

        record.save()

    def _create_record_relations(self, record: Record, discogs_release) -> None:
        """Метод создаёт/обновляет связи записи (артисты, лейбл, жанры, стили, форматы) на основе Discogs."""
        # Артисты
        artists: List[Artist] = []
        for artist_data in discogs_release.artists:
            artists.append(self._get_or_create_artist(artist_data))
        record.artists.set(artists)

        # Лейбл
        if discogs_release.labels:
            label = self._get_or_create_label(discogs_release.labels[0])
            record.label = label
            record.save(update_fields=["label"])

        # Жанры
        genres: List[Genre] = []
        for genre_name in getattr(discogs_release, "genres", []):
            genres.append(self._get_or_create_genre(genre_name))
        record.genres.set(genres)

        # Стили
        styles: List[Style] = []
        for style_name in getattr(discogs_release, "styles", []):
            styles.append(self._get_or_create_style(style_name))
        record.styles.set(styles)

        # Форматы
        formats = self._create_formats(getattr(discogs_release, "formats", []))
        record.formats.set(formats)

    def _create_tracks(self, record: Record, discogs_release) -> None:
        """Метод создаёт треки записи на основе Discogs и пытается подобрать YouTube-видео."""
        videos = self.discogs_service.get_release_videos(record.discogs_id) or []

        for i, track in enumerate(getattr(discogs_release, "tracklist", []), start=1):
            # подбираем YouTube по вхождению названия трека
            track_url = None
            for video in videos:
                if track.title and track.title.lower() in (video.get("title") or "").lower():
                    track_url = video.get("url")
                    break

            Track.objects.create(
                record=record,
                position=(track.position or ""),
                position_index=i,
                title=track.title,
                duration=track.duration,
                youtube_url=track_url,
            )

    def _update_tracks(self, record: Record, discogs_release) -> None:
        """Метод полностью пересоздаёт треки записи на основе Discogs."""
        old_count = record.tracks.count()
        record.tracks.all().delete()
        logger.info("Удалены старые треки (%d) для записи %s", old_count, record.id)

        self._create_tracks(record, discogs_release)
        new_count = record.tracks.count()
        logger.info("Созданы новые треки (%d) для записи %s", new_count, record.id)


    def _get_or_create_artist(self, artist_data) -> Artist:
        """Метод получает или создаёт артиста по данным Discogs."""
        artist = Artist.objects.find_by_discogs_id(artist_data.id)
        if not artist:
            artist = Artist.objects.create(discogs_id=artist_data.id, name=artist_data.name)
        return artist

    def _get_or_create_label(self, label_data) -> Label:
        """Метод получает или создаёт лейбл по данным Discogs."""
        label = Label.objects.find_by_discogs_id(label_data.id)
        if not label:
            label = Label.objects.create(
                discogs_id=label_data.id,
                name=label_data.name,
                description=f"Discogs ID: {label_data.id}",
            )
        return label

    def _get_or_create_genre(self, genre_name: str) -> Genre:
        """Метод получает или создаёт жанр."""
        genre = Genre.objects.find_by_name(genre_name)
        if not genre:
            genre = Genre.objects.create(name=genre_name)
        return genre

    def _get_or_create_style(self, style_name: str) -> Style:
        """Метод получает или создаёт стиль."""
        style = Style.objects.find_by_name(style_name)
        if not style:
            style = Style.objects.create(name=style_name)
        return style

    def _create_formats(self, formats_data) -> List[Format]:
        """Метод создаёт (или находит) форматы записи по данным Discogs."""
        if not formats_data:
            return []

        formats: List[Format] = []
        for fmt in formats_data:
            qty = int(fmt.get("qty", 1))
            descriptions = [d.upper() for d in fmt.get("descriptions", [])]

            # Специальная обработка LP (1LP / 2LP / ...)
            if "LP" in descriptions:
                format_name = f"{qty}LP" if qty > 1 else "LP"
                f = Format.objects.find_by_name(format_name)
                if not f:
                    f = Format.objects.create(name=format_name)
                formats.append(f)

            # Остальные описания (кроме предопределённых LP-вариантов)
            for desc in descriptions:
                if desc not in {"LP", "2LP", "3LP", "4LP", "5LP", "6LP"}:
                    f = Format.objects.find_by_name(desc)
                    if not f:
                        f = Format.objects.create(name=desc)
                    formats.append(f)

        return formats
