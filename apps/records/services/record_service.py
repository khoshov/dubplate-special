import logging
from typing import Optional, Tuple, List
from .tracks import create_tracks_for_record
from django.db import transaction
from django.utils import timezone
from ..models import (
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

from ..services.discogs_service import DiscogsService
from ..services.image_service import ImageService
from ..services.providers.redeye.redeye_service import RedeyeService
from .providers.redeye.utils import normalize_redeye_url

# добавлено: импорт каркаса сервиса Redeye (реализуем отдельно)
# Важно: мы НЕ добавляем его в __init__, чтобы не ломать существующие вызовы RecordService.
DEFAULT_NAME = "not specified"

logger = logging.getLogger(__name__)


def _get_or_create_default(model_cls):
    obj = model_cls.objects.find_by_name(DEFAULT_NAME)
    if not obj:
        obj = model_cls.objects.create(name=DEFAULT_NAME)
    return obj


class RecordService:
    """Сервис для работы с записями.

    Инкапсулирует всю бизнес-логику работы с записями,
    включая импорт из Discogs, управление остатками и проверку дубликатов.

    Attributes:
        discogs_service: Сервис для работы с Discogs API.
        image_service: Сервис для работы с изображениями.
    """

    def __init__(self, discogs_service: DiscogsService, image_service: ImageService):
        """Инициализация сервиса.

        Args:
            discogs_service: Экземпляр сервиса Discogs.
            image_service: Экземпляр сервиса изображений.
        """
        self.discogs_service = discogs_service
        self.image_service = image_service

    def import_from_discogs(
            self,
            barcode: Optional[str] = None,
            catalog_number: Optional[str] = None,
            save_image: bool = True,
    ) -> Tuple[Record, bool]:
        """Импорт записи из Discogs.

        Ищет релиз в Discogs по штрих-коду или каталожному номеру
        и создаёт запись в базе данных.

        Args:
            barcode: Штрих-код для поиска.
            catalog_number: Каталожный номер для поиска.
            save_image: Флаг загрузки обложки.

        Returns:
            Кортеж из записи и флага был ли выполнен импорт.
            (record, True) - если запись импортирована из Discogs.
            (record, False) - если вернули существующую запись.

        Raises:
            ValueError: Если не указаны идентификаторы или запись не найдена.
        """
        # Проверяем существующие записи
        existing = self._find_existing_record(barcode, catalog_number)
        if existing:
            logger.info(f"Found existing record: {existing.id}")
            self._update_missing_identifiers(existing, barcode, catalog_number)
            return existing, False

        # Ищем в Discogs
        if barcode:
            discogs_release = self.discogs_service.search_by_barcode(barcode)
        elif catalog_number:
            discogs_release = self.discogs_service.search_by_catalog_number(
                catalog_number
            )
        else:
            raise ValueError("Either barcode or catalog_number is required")

        if not discogs_release:
            raise ValueError("Release not found in Discogs")

        # Создаём запись в транзакции
        with transaction.atomic():
            record = self._create_record_from_discogs(
                discogs_release,
                search_barcode=barcode,
                search_catalog_number=catalog_number,
            )

            # Загружаем обложку
            if save_image and discogs_release.images:
                success = self.image_service.download_cover(
                    record, discogs_release.images[0]["uri"]
                )
                if success:
                    logger.info(f"Cover downloaded for record {record.id}")

        logger.info(f"Record imported successfully: {record.id}")
        return record, True

    # добавлен: импорт из Redeye — ключевой метод для новой функциональности
    # --- изменено: добавлен параметр download_audio и сохранение vendor_source_url ---
    # --- изменено: после создания записи и треков опционально качаем mp3 через аудио-сервис ---
    # --- изменено: работаем через RecordSource вместо vendor_*; русский текст ошибок/логов; download_audio управляет докачкой ---
    def import_from_redeye(
            self,
            catalog_number: Optional[str] = None,
            save_image: bool = True,
            *,
            download_audio: bool = True,  # админка: True; массовые парсинги: False
    ) -> Tuple[Record, bool]:
        """
        Импорт записи по каталожному номеру с сайта Redeye.

        Поток:
          1) Ищем карточку на Redeye → парсим payload.
          2) Создаём Record + связи + треки.
          3) Сохраняем обложку (если есть).
          4) Создаём/обновляем RecordSource (redeye/product_page).
          5) (опционально) Докачиваем mp3-превью по порядку треков.

        Args:
            catalog_number: Каталожный номер для поиска на Redeye.
            save_image: Скачивать ли обложку.
            download_audio: Качать ли mp3-превью сразу после импорта (админка: True; массовые пайплайны: False).

        Returns:
            (record, created)

        Raises:
            ValueError: если не указан catalog_number.
        """
        if not catalog_number:
            raise ValueError("Не указан каталожный номер (catalog_number) для импорта из Redeye.")

        # 1) Локальный дубликат
        existing = Record.objects.find_by_catalog_number(catalog_number)
        if existing:
            logger.info("Найдена существующая запись по каталожному номеру (Redeye): %s", existing.id)
            if download_audio:
                try:
                    # попытка докачать превью через RecordSource (redeye/product_page)
                    self._maybe_attach_redeye_previews(existing, force=False)
                except Exception as e:
                    logger.warning("Докачка mp3 для существующей записи завершилась с ошибкой: %s", e)
            return existing, False

        # 2) Парсим карточку Redeye
        redeye = RedeyeService()
        res = redeye.fetch_by_catalog_number(catalog_number)  # RedeyeFetchResult
        data = res.payload  # dict: title, artists, label, catalog_number, tracks, image_url, release_*, source.url, ...
        # (структура формируется в redeye_service._parse_product_page)

        # подстрахуем CAT (то, что искали — то и запишем)
        wanted = (catalog_number or "").strip().upper()
        parsed = (data.get("catalog_number") or "").strip().upper()
        if parsed != wanted:
            logger.info("Каталожный номер в payload отличается: '%s' → перезаписываем на '%s'", parsed, wanted)
            data["catalog_number"] = wanted

        # 3) Создаём запись + связи + треки
        with transaction.atomic():
            record = self._create_record_from_vendor(data)  # создаёт Record и треки через ingest

            # 4) Обложка (если есть)
            cover_url = data.get("image_url")
            if save_image and cover_url:
                if self.image_service.download_cover(record, cover_url):
                    logger.info("Обложка скачана для записи %s (Redeye)", record.id)

            # 5) RecordSource (redeye/product_page)
            source_url = (data.get("source") or {}).get("url") or res.source_url
            if source_url:
                self._upsert_record_source(
                    record=record,
                    provider=RecordSource.Provider.REDEYE,
                    role=RecordSource.Role.PRODUCT_PAGE,
                    url=source_url,
                    can_fetch_audio=True,
                )

        # 6) (опционально) докачиваем mp3-превью — по порядку треков (1..N)
        if download_audio:
            try:
                self._maybe_attach_redeye_previews(record, force=False)
            except Exception as e:
                logger.warning("Докачка mp3 завершилась с ошибкой для записи %s: %s", record.pk, e)

        logger.info("Импорт из Redeye завершён успешно: %s", record.id)
        return record, True

    def update_from_discogs(self, record: Record, update_image: bool = True) -> Record:
        """Обновление существующей записи из Discogs.

        Обновляет следующие данные:
        1. Основные поля: title, year, country, notes
        2. Идентификаторы: barcode и catalog_number (если отсутствуют)
        3. Связи: артисты, лейбл, жанры, стили, форматы (полная замена)
        4. Треки: полная замена (удаляются старые, создаются новые)
        5. Обложка: загружается если отсутствует и update_image=True

        НЕ обновляются:
        - condition (состояние)
        - stock (остатки)
        - price (цена)
        - discogs_id (не меняется)

        Args:
            record: Запись для обновления.
            update_image: Флаг обновления обложки.

        Returns:
            Обновлённая запись.

        Raises:
            ValueError: Если у записи нет discogs_id или она не найдена в Discogs.
        """
        if not record.discogs_id:
            raise ValueError("Record must have discogs_id for update")

        logger.info(
            f"Starting update from Discogs for record {record.id} "
            f"(Discogs ID: {record.discogs_id}, "
            f"Barcode: '{record.barcode}', "
            f"Catalog: '{record.catalog_number}')"
        )

        discogs_release = self.discogs_service.get_release(record.discogs_id)
        if not discogs_release:
            raise ValueError(f"Release {record.discogs_id} not found in Discogs")

        with transaction.atomic():
            # 1. Обновляем основные поля
            self._update_record_fields(record, discogs_release)

            # 2. Обновляем связи (полная замена)
            self._update_record_relations(record, disccogs_release=discogs_release)

            # 3. Обновляем треки (полная замена)
            self._update_tracks(record, discogs_release)

            # 4. Обновляем обложку если нужно
            if update_image and not record.cover_image and discogs_release.images:
                if self.image_service.download_cover(
                        record, discogs_release.images[0]["uri"]
                ):
                    logger.info(f"Cover image updated for record {record.id}")

        # Финальное логирование
        logger.info(
            f"Record {record.id} successfully updated from Discogs. "
            f"Final state - Barcode: '{record.barcode}', "
            f"Catalog: '{record.catalog_number}'"
        )

        return record

    def check_duplicate(
            self,
            barcode: Optional[str] = None,
            catalog_number: Optional[str] = None,
            discogs_id: Optional[int] = None,
            exclude_pk: Optional[int] = None,
    ) -> Optional[Record]:
        """Проверка на дубликаты по идентификаторам.

        Проверяет существование записи с указанными идентификаторами
        и обновляет недостающие идентификаторы.

        Args:
            barcode: Штрих-код для проверки.
            catalog_number: Каталожный номер для проверки.
            discogs_id: ID в Discogs для проверки.
            exclude_pk: ID записи для исключения из поиска.

        Returns:
            Найденная запись-дубликат или None.
        """
        # Проверяем по прямым совпадениям
        if discogs_id:
            record = Record.objects.find_by_discogs_id(discogs_id)
            if record and record.pk != exclude_pk:
                self._update_missing_identifiers(record, barcode, catalog_number)
                return record

        if barcode:
            record = Record.objects.find_by_barcode(barcode)
            if record and record.pk != exclude_pk:
                self._update_missing_identifiers(record, barcode, catalog_number)
                return record

        if catalog_number:
            record = Record.objects.find_by_catalog_number(catalog_number)
            if record and record.pk != exclude_pk:
                self._update_missing_identifiers(record, barcode, catalog_number)
                return record

        # Дополнительная проверка через Discogs API
        if barcode or catalog_number:
            try:
                if barcode:
                    release = self.discogs_service.search_by_barcode(barcode)
                elif catalog_number:
                    release = self.discogs_service.search_by_catalog_number(
                        catalog_number
                    )

                if release:
                    record = Record.objects.find_by_discogs_id(release.id)
                    if record and record.pk != exclude_pk:
                        self._update_missing_identifiers(
                            record, barcode, catalog_number
                        )
                        return record
            except Exception as e:
                logger.debug(f"Failed to check discogs duplicate: {e}")

        return None

    def update_stock(
            self, record: Record, quantity: int, operation: str = "set"
    ) -> Record:
        """Обновление остатков записи.

        Поддерживает операции установки, добавления и вычитания остатков.

        Args:
            record: Запись для обновления остатков.
            quantity: Количество для операции.
            operation: Тип операции ('set', 'add', 'subtract').

        Returns:
            Обновлённая запись.

        Raises:
            ValueError: При недостаточном количестве для вычитания
                       или неизвестной операции.
        """
        if operation == "set":
            record.stock = quantity
        elif operation == "add":
            record.stock += quantity
        elif operation == "subtract":
            if record.stock < quantity:
                raise ValueError(f"Insufficient stock. Available: {record.stock}")
            record.stock -= quantity
        else:
            raise ValueError(f"Unknown operation: {operation}")

        record.save()
        logger.info(f"Stock updated for record {record.id}: {operation} {quantity}")
        return record

    # === Приватные методы ===

    def _find_existing_record(
            self, barcode: Optional[str], catalog_number: Optional[str]
    ) -> Optional[Record]:
        """Поиск существующей записи по идентификаторам.

        Args:
            barcode: Штрих-код для поиска.
            catalog_number: Каталожный номер для поиска.

        Returns:
            Найденная запись или None.
        """
        if barcode:
            if record := Record.objects.find_by_barcode(barcode):
                return record

        if catalog_number:
            if record := Record.objects.find_by_catalog_number(catalog_number):
                return record

        return None

    def _create_record_from_discogs(
            self,
            discogs_release,
            search_barcode: Optional[str] = None,
            search_catalog_number: Optional[str] = None,
    ) -> Record:
        """Создание записи из данных Discogs.

        Args:
            discogs_release: Объект релиза из Discogs API.
            search_barcode: Штрих-код, использованный для поиска.
            search_catalog_number: Каталожный номер, использованный для поиска.

        Returns:
            Созданная запись.
        """
        # Извлекаем данные
        record_data = self.discogs_service.extract_release_data(discogs_release)

        # Приоритет у идентификаторов из поиска
        if search_barcode:
            record_data["barcode"] = search_barcode
        if search_catalog_number:
            record_data["catalog_number"] = search_catalog_number

        logger.info(
            f"Creating record - "
            f"Discogs ID: {record_data['discogs_id']}, "
            f"Barcode: {record_data.get('barcode', 'None')}, "
            f"Catalog: {record_data.get('catalog_number', 'None')}"
        )

        # Создаём основную запись
        record = Record.objects.create(
            title=record_data["title"],
            discogs_id=record_data["discogs_id"],
            release_year=record_data.get("year"),
            catalog_number=record_data.get("catalog_number"),
            barcode=record_data.get("barcode"),
            country=record_data.get("country"),
            notes=record_data.get("notes"),
            condition=RecordConditions.M,
            stock=1,
        )

        # Создаём связи
        self._create_record_relations(record, discogs_release)

        # Создаём треки
        self._create_tracks(record, discogs_release)

        return record

    @staticmethod
    def _is_empty_identifier(value: Optional[str]) -> bool:
        """Проверка, является ли идентификатор пустым.

        В Django пустые CharField сохраняются как '', а не None.

        Args:
            value: Значение для проверки.

        Returns:
            True если значение пустое (None, пустая строка или только пробелы).
        """
        return value is None or (isinstance(value, str) and value.strip() == "")

    def _update_missing_identifiers(
            self,
            record: Record,
            barcode: Optional[str] = None,
            catalog_number: Optional[str] = None,
    ):
        """Обновляет недостающие идентификаторы в существующей записи.

        Args:
            record: Запись для обновления.
            barcode: Штрих-код для добавления.
            catalog_number: Каталожный номер для добавления.
        """
        updated = False

        if barcode and self._is_empty_identifier(record.barcode):
            record.barcode = barcode
            updated = True
            logger.info(f"Updated missing barcode for record {record.id}: {barcode}")

        if catalog_number and self._is_empty_identifier(record.catalog_number):
            record.catalog_number = catalog_number
            updated = True
            logger.info(
                f"Updated missing catalog_number for record {record.id}: {catalog_number}"
            )

        if updated:
            record.save()

    def _update_record_fields(self, record: Record, discogs_release):
        """Обновление основных полей записи.

        Обновляет:
        - title (всегда)
        - release_year (всегда)
        - country (всегда)
        - notes (всегда)
        - catalog_number (только если пустой)
        - barcode (только если пустой)

        Args:
            record: Запись для обновления.
            discogs_release: Объект релиза из Discogs API.
        """
        record_data = self.discogs_service.extract_release_data(discogs_release)

        # Сохраняем старые значения для логирования
        old_values = {
            "title": record.title,
            "year": record.release_year,
            "country": record.country,
            "catalog_number": record.catalog_number,
            "barcode": record.barcode,
        }

        # Обновляем поля
        record.title = record_data["title"]
        record.release_year = record_data.get("year")
        record.country = record_data.get("country")
        record.notes = record_data.get("notes")

        # Обновляем идентификаторы если они пустые
        if self._is_empty_identifier(record.catalog_number) and record_data.get(
                "catalog_number"
        ):
            record.catalog_number = record_data["catalog_number"]
            logger.info(f"Added missing catalog_number: {record.catalog_number}")

        if self._is_empty_identifier(record.barcode) and record_data.get("barcode"):
            record.barcode = record_data["barcode"]
            logger.info(f"Added missing barcode: {record.barcode}")

        # Логируем изменения
        changes = []
        for field, old_value in old_values.items():
            new_value = getattr(record, field if field != "year" else "release_year")
            if old_value != new_value:
                changes.append(f"{field}: '{old_value}' → '{new_value}'")

        if changes:
            logger.info(f"Updated fields for record {record.id}: {', '.join(changes)}")

        record.save()

    def _create_record_relations(self, record: Record, discogs_release):
        """Создание связей записи (артисты, лейбл, жанры и т.д.).

        Args:
            record: Запись для установки связей.
            discogs_release: Объект релиза из Discogs API.
        """
        # Артисты
        artists = []
        for artist_data in discogs_release.artists:
            artist = self._get_or_create_artist(artist_data)
            artists.append(artist)
        record.artists.set(artists)

        # Лейбл
        if discogs_release.labels:
            label = self._get_or_create_label(discogs_release.labels[0])
            record.label = label
            record.save()

        # Жанры
        genres = []
        for genre_name in getattr(discogs_release, "genres", []):
            genre = self._get_or_create_genre(genre_name)
            genres.append(genre)
        record.genres.set(genres)

        # Стили
        styles = []
        for style_name in getattr(discogs_release, "styles", []):
            style = self._get_or_create_style(style_name)
            styles.append(style)
        record.styles.set(styles)

        # Форматы
        formats = self._create_formats(getattr(discogs_release, "formats", []))
        record.formats.set(formats)

    # добавлено: создание записи/связей из "вендорских" данных (Redeye)
    # --- изменено: после создания записи сохраняем vendor_source_url, если поле существует ---
    # --- изменено: убран устаревший записывающий код vendor_*; остальное без изменений ---
    def _create_record_from_vendor(self, data: dict) -> Record:
        """Создание записи из словаря, полученного от внешнего провайдера (Redeye/и др.).

        Поддерживает ключи:
          title, artists[], label, catalog_number, barcode, country, notes,
          formats[], tracks[], image_url, price_gbp, availability,
          release_year, release_month, release_day, source={name,url}.

        Если запись с таким catalog_number уже существует — бросим ValueError.
        """
        catalog_number = (data.get("catalog_number") or "").strip() or None
        if catalog_number:
            dupe = Record.objects.filter(catalog_number=catalog_number).first()
            if dupe:
                raise ValueError(f'Запись с каталожным номером "{catalog_number}" уже существует (ID {dupe.pk}).')

        release_year = data.get("release_year", data.get("year"))
        release_month = data.get("release_month")
        release_day = data.get("release_day")

        # 1) Создаём сам объект Record
        record = Record.objects.create(
            title=data["title"],
            discogs_id=None,
            release_year=release_year,
            release_month=release_month,
            release_day=release_day,
            catalog_number=catalog_number,
            barcode=data.get("barcode"),
            country=data.get("country"),
            notes=data.get("notes"),
            condition=RecordConditions.M,
            stock=1,
        )

        # 2) Связи (artists/label/genres/styles/formats)
        self._create_vendor_relations(record, data)

        # 3) Треки — через единый ingest (индексация 1..N).
        self._create_vendor_tracks(record, data)

        return record

    # --- добавлено: единая точка создания/обновления RecordSource ---
    def _upsert_record_source(
            self,
            *,
            record: Record,
            provider: RecordSource.Provider,
            role: RecordSource.Role,
            url: str,
            can_fetch_audio: bool,
    ) -> RecordSource:
        """
        Создаёт или обновляет RecordSource для заданной записи/провайдера/роли.

        Идемпотентно: при повторном вызове обновит url/can_fetch_audio, не создавая дубликаты.
        """
        url = normalize_redeye_url(url)  # --- добавлено ---
        obj, created = RecordSource.objects.get_or_create(
            record=record,
            provider=provider,
            role=role,
            defaults={"url": url, "can_fetch_audio": can_fetch_audio},
        )
        if not created:
            updated = False
            if obj.url != url:
                obj.url = url
                updated = True
            if obj.can_fetch_audio != can_fetch_audio:
                obj.can_fetch_audio = can_fetch_audio
                updated = True
            if updated:
                obj.save(update_fields=["url", "can_fetch_audio", "updated_at"])
        return obj

    # --- добавлено: обёртка для аудио-превью (аналог download_cover), без прямых зависимостей снаружи ---
    # --- изменено: новое имя и логика через RecordSource; русские сообщения ---
    def _maybe_attach_redeye_previews(self, record, *, force: bool = False) -> int:
        """
        Докачка/перекачка mp3-превью с Redeye для заданной записи.

        Алгоритм:
          1) Ищем RecordSource(provider=REDEYE, role=PRODUCT_PAGE, can_fetch_audio=True).
          2) Нормализуем URL карточки (фикс дублированного домена, лишние слэши).
          3) Если у записи нет треков, но на странице есть плеер — создаём плейсхолдеры (Untitled 1..N).
          4) Привязываем превью через плеерный модуль (сопоставление по position_index).
          5) Обновляем телеметрию RecordSource.
        """
        import logging

        # относительные импорты (важно: одна точка, не две!)
        from ..models import RecordSource, Track  # up: records
        from .providers.redeye.utils import normalize_redeye_url  # внутри services
        from .tracks.audio.redeye_player import ensure_previews_from_redeye_player  # внутри services.tracks
        from .tracks.audio.capture import collect_redeye_media_urls  # внутри services.tracks

        logger = getattr(self, "logger", logging.getLogger(__name__))
        logger.info("[DEBUG] _maybe_attach_redeye_previews(): record=%s force=%s", getattr(record, "pk", record), force)

        # 1) источник Redeye product_page
        source = (
            record.sources
            .filter(
                provider=RecordSource.Provider.REDEYE,
                role=RecordSource.Role.PRODUCT_PAGE,
                can_fetch_audio=True,
            )
            .first()
        )
        if not source:
            logger.info("У записи id=%s нет подходящего источника Redeye (product_page / can_fetch_audio=True).",
                        record.id)
            return 0

        # 2) нормализуем URL
        page_url = normalize_redeye_url(source.url or getattr(record, "source_url", "") or "")
        if not page_url:
            logger.info("У записи id=%s отсутствует URL карточки Redeye — пропуск докачки.", record.id)
            return 0

        logger.info("[audio] Redeye page URL (normalized): %s", page_url)
        try:
            src_dump = list(record.sources.values_list("role", "url"))
            logger.info("[DEBUG] record.sources(filter=REDEYE): %s", src_dump)
        except Exception:
            pass

        updated_count = 0

        try:
            # 3) Подстраховка: если нет треков — попробуем создать плейсхолдеры из плеера
            if not record.tracks.exists():
                media_urls = []
                try:
                    media_urls = list(collect_redeye_media_urls(page_url, per_click_timeout_sec=20))
                except Exception as e:
                    logger.warning("[audio] ошибка при сборе аудио-ссылок (record=%s): %s", record.id, e)

                if media_urls:
                    created_ids = []
                    for i in range(1, len(media_urls) + 1):
                        t = Track.objects.create(
                            record=record,
                            title=f"Untitled {i}",
                            position="",
                            position_index=i,
                        )
                        created_ids.append(t.id)
                    logger.info("[audio] создано плейсхолдеров треков: %s (всего %s)", created_ids, len(created_ids))
                else:
                    logger.info("[audio] на странице не обнаружены аудио-кнопки — плейсхолдеры не созданы.")

            # 4) Привязка превью через модуль плеера
            updated_count = ensure_previews_from_redeye_player(
                record=record,
                page_url=page_url,
                force=force,
                per_click_timeout_sec=20,
            )

        finally:
            # 5) Телеметрия
            try:
                source.last_audio_scrape_at = timezone.now()
                source.audio_urls_count = int(updated_count)
                source.save(update_fields=["last_audio_scrape_at", "audio_urls_count", "updated_at"])
            except Exception as e:
                logger.debug("Не удалось обновить телеметрию RecordSource(id=%s): %s", getattr(source, "id", None), e)

        return updated_count

    def _create_vendor_relations(self, record, data: dict) -> None:
        """
        Создаёт и привязывает связанные объекты (artists, label, genres, styles, formats)
        по данным вендора. Для жанров/стилей используется CI-поиск и нормализация
        'not specified' -> 'Not specified', чтобы не нарушать CI-unique ограничение.
        """

        def _canon_vocab(name: str) -> str:
            if not name:
                return ""
            n = name.strip()
            if n.lower() == "not specified":
                return "Not specified"
            return n

        def _ci_get_or_create(model, name: str):
            """Case-insensitive get_or_create по полю name + нормализация."""
            canon = _canon_vocab(name)
            if not canon:
                return None
            obj = model.objects.filter(name__iexact=canon).first()
            if obj:
                # выравниваем регистр до канонического
                if obj.name != canon:
                    model.objects.filter(pk=obj.pk).update(name=canon)
                    obj.name = canon
                return obj
            return model.objects.create(name=canon)

        # --- Artists ---
        for a in (data.get("artists") or []):
            name = (a or "").strip()
            if not name:
                continue
            artist = Artist.objects.filter(name__iexact=name).first() or Artist.objects.create(name=name)
            record.artists.add(artist)

        # --- Label ---
        label_name = (data.get("label") or "").strip()
        if label_name:
            label = Label.objects.filter(name__iexact=label_name).first() or Label.objects.create(name=label_name)
            record.label = label
            record.save(update_fields=["label"])

        # --- Genres (CI + нормализация) ---
        for g in (data.get("genres") or []):
            obj = _ci_get_or_create(Genre, g)
            if obj:
                record.genres.add(obj)

        # --- Styles (CI + нормализация) ---
        for s in (data.get("styles") or []):
            obj = _ci_get_or_create(Style, s)
            if obj:
                record.styles.add(obj)

        # --- Formats (если модель есть) ---
        if Format:
            for f in (data.get("formats") or []):
                name = (f or "").strip()
                if not name:
                    continue
                fmt = Format.objects.filter(name__iexact=name).first() or Format.objects.create(name=name)
                record.formats.add(fmt)

    def _create_vendor_tracks(self, record: Record, data: dict) -> None:
        """Создание треков из словаря (позиция/название/длительность)."""
        create_tracks_for_record(record, data.get("tracks") or [])

    def _update_record_relations(self, record: Record, disccogs_release):
        """Обновление связей записи.

        ПОЛНОСТЬЮ ЗАМЕНЯЕТ:
        - Артистов
        - Жанры
        - Стили
        - Форматы
        - Лейбл

        Args:
            record: Запись для обновления связей.
            disccogs_release: Объект релиза из Discogs API.
        """
        logger.info(f"Updating relations for record {record.id}")

        # Сохраняем старые значения для логирования
        old_artists = list(record.artists.values_list("name", flat=True))
        old_genres = list(record.genres.values_list("name", flat=True))
        old_styles = list(record.styles.values_list("name", flat=True))
        old_formats = list(record.formats.values_list("name", flat=True))
        old_label = record.label.name if record.label else None

        # Обновляем все связи
        self._create_record_relations(record, disccogs_release)

        # Логируем изменения
        new_artists = list(record.artists.values_list("name", flat=True))
        new_genres = list(record.genres.values_list("name", flat=True))
        new_styles = list(record.styles.values_list("name", flat=True))
        new_formats = list(record.formats.values_list("name", flat=True))
        new_label = record.label.name if record.label else None

        if old_artists != new_artists:
            logger.info(f"Artists updated: {old_artists} → {new_artists}")
        if old_genres != new_genres:
            logger.info(f"Genres updated: {old_genres} → {new_genres}")
        if old_styles != new_styles:
            logger.info(f"Styles updated: {old_styles} → {new_styles}")
        if old_formats != new_formats:
            logger.info(f"Formats updated: {old_formats} → {new_formats}")
        if old_label != new_label:
            logger.info(f"Label updated: '{old_label}' → '{new_label}'")

    def _get_or_create_artist(self, artist_data) -> Artist:
        """Получение или создание артиста.

        Args:
            artist_data: Данные артиста из Discogs API.

        Returns:
            Экземпляр модели Artist.
        """
        artist = Artist.objects.find_by_discogs_id(artist_data.id)
        if not artist:
            artist = Artist.objects.create(
                discogs_id=artist_data.id, name=artist_data.name
            )
        return artist

    def _get_or_create_label(self, label_data) -> Label:
        """Получение или создание лейбла.

        Args:
            label_data: Данные лейбла из Discogs API.

        Returns:
            Экземпляр модели Label.
        """
        label = Label.objects.find_by_discogs_id(label_data.id)
        if not label:
            label = Label.objects.create(
                discogs_id=label_data.id,
                name=label_data.name,
                description=f"Discogs ID: {label_data.id}",
            )
        return label

    def _get_or_create_genre(self, genre_name: str) -> Genre:
        """Получение или создание жанра.

        Args:
            genre_name: Название жанра.

        Returns:
            Экземпляр модели Genre.
        """
        genre = Genre.objects.find_by_name(genre_name)
        if not genre:
            genre = Genre.objects.create(name=genre_name)
        return genre

    def _get_or_create_style(self, style_name: str) -> Style:
        """Получение или создание стиля.

        Args:
            style_name: Название стиля.

        Returns:
            Экземпляр модели Style.
        """
        style = Style.objects.find_by_name(style_name)
        if not style:
            style = Style.objects.create(name=style_name)
        return style

    def _create_formats(self, formats_data) -> List[Format]:
        """Создание форматов записи.

        Обрабатывает специальные случаи для LP и других форматов.

        Args:
            formats_data: Данные о форматах из Discogs API.

        Returns:
            Список экземпляров модели Format.
        """
        if not formats_data:
            return []

        formats = []
        for format_info in formats_data:
            qty = int(format_info.get("qty", 1))
            descriptions = [d.upper() for d in format_info.get("descriptions", [])]

            # Специальная обработка для LP
            if "LP" in descriptions:
                format_name = f"{qty}LP" if qty > 1 else "LP"
                fmt = Format.objects.find_by_name(format_name)
                if not fmt:
                    fmt = Format.objects.create(name=format_name)
                formats.append(fmt)

            # Остальные форматы
            for desc in descriptions:
                if desc not in ["LP", "2LP", "3LP", "4LP", "5LP", "6LP"]:
                    fmt = Format.objects.find_by_name(desc)
                    if not fmt:
                        fmt = Format.objects.create(name=desc)
                    formats.append(fmt)

        return formats

    def _create_tracks(self, record: Record, discogs_release):
        """Создание треков для записи.

        Создаёт треки и пытается найти соответствующие видео на YouTube.

        Args:
            record: Запись для добавления треков.
            discogs_release: Объект релиза из Discogs API.
        """
        # Получаем видео если есть
        videos = self.discogs_service.get_release_videos(record.discogs_id) or []

        for i, track in enumerate(getattr(discogs_release, "tracklist", []), start=1):
            # Ищем видео для трека
            track_url = None
            for video in videos:
                if track.title.lower() in video["title"].lower():
                    track_url = video["url"]
                    break

            Track.objects.create(
                record=record,
                position=(track.position or ""),
                position_index=i,
                title=track.title,
                duration=track.duration,
                youtube_url=track_url,
            )

    def _update_tracks(self, record: Record, discogs_release):
        """Обновление треков записи.

        ПОЛНОСТЬЮ УДАЛЯЕТ старые треки и создаёт новые.
        Также пытается найти YouTube видео для каждого трека.

        Args:
            record: Запись для обновления треков.
            discogs_release: Объект релиза из Discogs API.
        """
        old_tracks_count = record.tracks.count()

        # Удаляем старые треки
        record.tracks.all().delete()
        logger.info(f"Deleted {old_tracks_count} old tracks for record {record.id}")

        # Создаём новые
        self._create_tracks(record, discogs_release)

        new_tracks_count = record.tracks.count()
        logger.info(f"Created {new_tracks_count} new tracks for record {record.id}")

    def _canon_vocab(self, name: str) -> str:
        """Приводим к каноническому виду.
        Пока нормализуем только 'not specified' -> 'Not specified'.
        Остальные значения возвращаем как есть (обрезаем пробелы).
        """
        if not name:
            return ""
        n = name.strip()
        if n.lower() == "not specified":
            return "Not specified"
        return n

    def _get_or_create_genre_ci(self, name: str):
        """CI-lookup + нормализация имени для Genre."""
        canon = self._canon_vocab(name)
        if not canon:
            return None
        obj = Genre.objects.filter(name__iexact=canon).first()
        if obj:
            # если хранили 'not specified' — обновим до канона
            if obj.name != canon:
                Genre.objects.filter(pk=obj.pk).update(name=canon)
                obj.name = canon
            return obj
        return Genre.objects.create(name=canon)

    def _get_or_create_style_ci(self, name: str):
        """CI-lookup + нормализация имени для Style."""
        canon = self._canon_vocab(name)
        if not canon:
            return None
        obj = Style.objects.filter(name__iexact=canon).first()
        if obj:
            if obj.name != canon:
                Style.objects.filter(pk=obj.pk).update(name=canon)
                obj.name = canon
            return obj
        return Style.objects.create(name=canon)

    def parse_product_by_url(self, url: str) -> dict:
        """скачивает HTML и возвращает dict полей так же, как это делает парсинг по каталожному номеру"""
