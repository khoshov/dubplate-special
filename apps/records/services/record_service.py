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

from __future__ import annotations

import logging
from typing import Optional, Tuple

from django.db import IntegrityError, transaction
from playwright.sync_api import Browser

from records.models import (
    Record,
    RecordSource,
)
from records.services.audio.audio_service import AudioService
from records.services.image.image_service import ImageService
from records.services.provider_payload_adapter import (
    adapt_redeye_payload,
    adapt_discogs_release,
)
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.providers.redeye.helpers import validate_redeye_product_url
from records.services.providers.redeye.redeye_service import RedeyeService
from records.services.record_assembly import build_record_from_payload
from records.services.record_assembly import update_record_from_payload

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
        redeye_service: RedeyeService,
        image_service: ImageService,
        audio_service: "AudioService | None" = None,
    ) -> None:
        """
        Метод инициализирует сервис с зависимостями.

        Args:
            discogs_service: Сервис интеграции с Discogs.
            redeye_service:  Сервис интеграции с Redeye.
            image_service:   Сервис работы с обложками.
            audio_service:   Сервис работы с аудио (опционально). Если не передан, создаётся стандартный.
        """
        self.discogs_service = discogs_service
        self.redeye_service = redeye_service
        self.image_service = image_service
        self.audio_service = audio_service or AudioService()

    def import_from_discogs(
        self,
        barcode: Optional[str] = None,
        catalog_number: Optional[str] = None,
        save_image: bool = True,
    ) -> Tuple[Record, bool]:
        """
        Метод реализует импорт записи из Discogs (по barcode или catalog_number).

        Поток выполнения:
          1) Проверяет наличие существующей записи (barcode, затем catalog_number).
          2) Ищет релиз в Discogs и получает объект Release.
          3) Нормализует Release в единую структуру данных через адаптер.
          4) Собирает Record в сборщике `build_record_from_payload(...)`.
          5) Скачивает обложку (опционально).

        Args:
            barcode: Штрих-код для поиска релиза в Discogs.
            catalog_number: Каталожный номер для поиска.
            save_image: Если True — сохраняет обложку из Discogs.

        Returns:
            (record, created): created=True — создана новая запись; False — возвращена существующая.
        """
        existing: Optional[Record] = None
        if barcode:
            existing = Record.objects.find_by_barcode(barcode)
        if not existing and catalog_number:
            existing = Record.objects.find_by_catalog_number(catalog_number)
        if existing:
            logger.info("Найдена существующая запись: %s", existing.id)
            self._update_missing_identifiers(existing, barcode, catalog_number)
            return existing, False

        if barcode:
            release = self.discogs_service.search_by_barcode(barcode)
        elif catalog_number:
            release = self.discogs_service.search_by_catalog_number(catalog_number)
        else:
            raise ValueError(
                "Нужно указать barcode или catalog_number для импорта из Discogs."
            )
        if not release:
            raise ValueError("Релиз не найден в Discogs.")

        payload = adapt_discogs_release(release)
        if barcode and not payload.get("barcode"):
            payload["barcode"] = barcode
        if catalog_number and not payload.get("catalog_number"):
            payload["catalog_number"] = catalog_number

        with transaction.atomic():
            record = build_record_from_payload(payload)

            if save_image and getattr(release, "images", None):
                first = release.images[0]
                uri = first.get("uri") if isinstance(first, dict) else None
                if uri and self.image_service.download_cover(record, uri):
                    logger.info("Обложка скачана для записи %s (Discogs)", record.id)

        logger.info("Импорт из Discogs выполнен: %s", record.id)
        return record, True

    def update_from_discogs(self, record: Record, update_image: bool = True) -> Record:
        """
        Метод обновляет существующую запись данными из Discogs.
        Обновляются: базовые поля, связи и треклист (полная замена). Обложка — опционально.
        """
        if not record.discogs_id:
            raise ValueError(
                "Для обновления из Discogs у записи должен быть discogs_id."
            )

        logger.info(
            "Начато обновление из Discogs для записи %s (Discogs ID: %s, Barcode: '%s', Catalog: '%s')",
            record.id,
            record.discogs_id,
            record.barcode,
            record.catalog_number,
        )

        release = self.discogs_service.get_release(record.discogs_id)
        if not release:
            raise ValueError(f"Релиз {record.discogs_id} не найден в Discogs.")

        payload = adapt_discogs_release(release)

        with transaction.atomic():
            update_record_from_payload(record, payload)

            if (
                update_image
                and not record.cover_image
                and getattr(release, "images", None)
            ):
                first = release.images[0]
                uri = first.get("uri") if isinstance(first, dict) else None
                if uri and self.image_service.download_cover(record, uri):
                    logger.info("Обновлена обложка для записи %s", record.id)

        logger.info("Обновление из Discogs завершено: %s", record.id)
        return record

    def import_from_redeye(
        self,
        catalog_number: Optional[str] = None,
        save_image_decision: bool = True,
        *,
        download_audio_decision: bool = True,
        raw_payload: dict | None = None,
        source_url: str | None = None,
    ) -> Tuple[Record, bool]:
        """
        Метод выполняет импорт записи по каталожному номеру с сайта Redeye.

        Поддерживает два сценария:
          1) catalog_number -> fetch_by_catalog_number(...) -> raw_payload (сетевой путь, для ручного импорта)
          2) catalog_number + raw_payload (+source_url) -> без сетевых запросов (для bulk-импорта по URL)

        Поток выполнения:
          1) Проверяет наличие существующей записи по catalog_number (case-insensitive). Если есть — возвращает её.
          2) Получает «сырой» payload (либо из аргумента raw_payload, либо через RedeyeService.fetch_by_catalog_number).
          3) Адаптирует payload к внутреннему формату и собирает Record.
          4) Загружает обложку (опционально).
          5) Создаёт/обновляет RecordSource (provider=REDEYE, role=PRODUCT_PAGE).
          6) При необходимости — привязывает аудио-превью по порядку треков.

        Returns:
            (record, created): created=True при создании, False — если запись уже существовала.
        """
        normalized_catalog_number = (catalog_number or "").strip().upper()
        if not normalized_catalog_number:
            raise ValueError(
                "Не указан каталожный номер (catalog_number) для импорта из Redeye."
            )

        existing = Record.objects.filter(
            catalog_number__iexact=normalized_catalog_number
        ).first()
        if existing:
            logger.info(
                "Найдена существующая запись по каталожному номеру (Redeye): %s",
                existing.id,
            )
            if download_audio_decision:
                try:
                    self.attach_audio_from_redeye(existing, force=False)
                except Exception as err:  # noqa: BLE001 — логируем любую ошибку привязки
                    logger.warning(
                        "Докачка аудио для существующей записи завершилась с ошибкой: %s",
                        err,
                    )
            return existing, False

        result = None
        if raw_payload is None:
            result = self.redeye_service.fetch_by_catalog_number(
                normalized_catalog_number
            )
            raw_payload = result.payload or {}

        payload = adapt_redeye_payload(raw_payload)
        payload["catalog_number"] = normalized_catalog_number

        try:
            with transaction.atomic():
                record = build_record_from_payload(payload)

                cover_url = raw_payload.get("image_url")
                if save_image_decision and cover_url:
                    if self.image_service.download_cover(record, cover_url):
                        logger.info("Обложка скачана для записи %s (Redeye)", record.id)

                # URL источника: приоритет у явно переданного source_url (bulk), затем payload, затем result.source_url
                raw_source = raw_payload.get("source")
                raw_source_url = (
                    raw_source.get("url") if isinstance(raw_source, dict) else None
                )
                effective_source_url = (
                    source_url
                    or raw_source_url
                    or (result.source_url if result else None)
                )

                if effective_source_url:
                    try:
                        validate_redeye_product_url(effective_source_url)
                        self._upsert_record_source(
                            record=record,
                            provider=RecordSource.Provider.REDEYE,
                            role=RecordSource.Role.PRODUCT_PAGE,
                            url=effective_source_url,
                            can_fetch_audio=True,
                        )
                    except ValueError as ve:
                        logger.warning(
                            "Валидация URL источника Redeye не пройдена: %s", ve
                        )
        except IntegrityError:
            existing = Record.objects.filter(
                catalog_number__iexact=normalized_catalog_number
            ).first()
            if existing:
                logger.warning(
                    "Запись с каталожным номером уже существует (Redeye): %s",
                    existing.id,
                )
                return existing, False
            raise

        if download_audio_decision:
            try:
                self.attach_audio_from_redeye(record, force=False)
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    "Докачка аудио завершилась с ошибкой для записи %s: %s",
                    record.pk,
                    err,
                )

        logger.info("Импорт из Redeye завершён успешно: %s", record.id)
        return record, True

    def attach_audio_from_redeye(
        self,
        record: "Record",
        *,
        force: bool = False,
        per_click_timeout_sec: Optional[int] = None,
        browser: Optional[Browser] = None,
    ) -> int:
        """
        Метод прикрепляет аудио-превью для записи через Redeye.

        Предполагается, что URL карточки уже сохранён в RecordSource (роль PRODUCT_PAGE).
        Здесь нет парсинга/резолва — только вызов AudioService.

        Args:
            record: Целевая запись.
            force: Перезаписывать существующие файлы превью.
            per_click_timeout_sec: Таймаут ожидания ссылок после клика (сек). Если None — используется дефолт скрапера.
            browser: Внешний экземпляр Playwright Browser (для потоковой обработки одной инстанцией).

        Returns:
            Количество обновлённых треков.
        """
        from records.models import RecordSource

        page_url: str | None = (
            record.sources.filter(
                provider=RecordSource.Provider.REDEYE,
                role=RecordSource.Role.PRODUCT_PAGE,
            )
            .values_list("url", flat=True)
            .first()
        ) or None

        updated = self.audio_service.attach_audio_from_redeye(
            record=record,
            page_url=page_url,
            force=force,
            per_click_timeout_sec=per_click_timeout_sec,
            browser=browser,
        )
        logger.info(
            "Завершено прикрепление аудио из Redeye: обновлено %d треков (record=%s)",
            updated,
            record.pk,
        )
        return updated

    def parse_redeye_product_by_url(self, url: str) -> dict:
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
        result = self.redeye_service.parse_redeye_product_by_url(clean)
        return result.payload

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
            logger.info(
                "Добавлен недостающий barcode для записи %s: %s", record.id, barcode
            )

        if catalog_number and self._is_empty_identifier(record.catalog_number):
            record.catalog_number = catalog_number
            updated = True
            logger.info(
                "Добавлен недостающий catalog_number для записи %s: %s",
                record.id,
                catalog_number,
            )

        if updated:
            record.save()

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

        if self._is_empty_identifier(record.catalog_number) and record_data.get(
            "catalog_number"
        ):
            record.catalog_number = record_data["catalog_number"]
            logger.info(
                "Добавлен недостающий catalog_number: %s", record.catalog_number
            )

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

    @staticmethod
    def _upsert_record_source(
        *,
        record: Record,
        provider: RecordSource.Provider,
        role: RecordSource.Role,
        url: str,
        can_fetch_audio: bool,
    ) -> RecordSource:
        """
        Метод создаёт или обновляет RecordSource для заданной записи/провайдера/роли.

        Идемпотентен:
          - при первом вызове создаст объект;
          - при последующих — обновит url/can_fetch_audio без дубликатов.

        Args:
            record: Запись-владелец источника.
            provider: Провайдер (например, RecordSource.Provider.REDEYE).
            role: Роль источника (например, PRODUCT_PAGE).
            url: Абсолютный URL источника (предварительно провалидирован).
            can_fetch_audio: Признак, что с этой страницы можно забирать аудио.

        Returns:
            Объект RecordSource.
        """
        obj, created = RecordSource.objects.get_or_create(
            record=record,
            provider=provider,
            role=role,
            defaults={"url": url, "can_fetch_audio": can_fetch_audio},
        )
        if created:
            logger.info(
                "Добавлен источник %s/%s для записи %s", provider, role, record.pk
            )
            return obj

        updated = False
        if obj.url != url:
            obj.url = url
            updated = True
        if obj.can_fetch_audio != can_fetch_audio:
            obj.can_fetch_audio = can_fetch_audio
            updated = True
        if updated:
            obj.save(update_fields=["url", "can_fetch_audio", "updated_at"])
            logger.info(
                "Обновлён источник %s/%s для записи %s", provider, role, record.pk
            )
        return obj
