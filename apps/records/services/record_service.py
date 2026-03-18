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
from discogs_client.exceptions import HTTPError
from config.logging import NOTICE_LEVEL, log_event

from records.models import (
    AudioEnrichmentJob,
    FormatChoices,
    Record,
    RecordSource,
    Track,
)
from records.services.audio.audio_service import AudioService
from records.services.image.image_service import ImageService
from records.services.provider_payload_adapter import (
    adapt_redeye_payload,
    adapt_discogs_release,
)
from records.services.providers.discogs.discogs_service import (
    DiscogsApiError,
    DiscogsAuthError,
    DiscogsConfigError,
    DiscogsNotFoundError,
    DiscogsService,
)
from records.services.providers.redeye.helpers import (
    normalize_abs_url,
    validate_redeye_product_url,
)
from records.services.providers.redeye.redeye_service import RedeyeService
from records.services.record_assembly import (
    build_record_from_payload,
    ensure_legacy_formats,
    update_record_from_payload,
)
from records.services.tasks import (
    process_youtube_enrichment_track,
    run_youtube_enrichment_job,
)

logger = logging.getLogger(__name__)
_RECORD_SERVICE_COMPONENT = "record_service"

DEFAULT_NAME = "not specified"


def _log_record_service_event(
    level: int,
    event: str,
    message: str,
    **context,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_RECORD_SERVICE_COMPONENT,
        event=event,
        **context,
    )


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
        discogs_id: Optional[int] = None,
        barcode: Optional[str] = None,
        catalog_number: Optional[str] = None,
        save_image: bool = True,
        requested_by_user_id: int | None = None,
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
        _log_record_service_event(
            logging.INFO,
            "discogs_import_start",
            "Запущен импорт записи из Discogs.",
            discogs_id=discogs_id,
            barcode=barcode or "—",
            catalog_number=catalog_number or "—",
            requested_by_user_id=requested_by_user_id,
        )
        normalized_barcode = self._normalize_barcode(barcode)
        normalized_catalog_number = self._normalize_catalog_number(catalog_number)

        existing = self._find_existing_record(
            discogs_id=discogs_id,
            barcode=normalized_barcode,
            catalog_number=normalized_catalog_number,
        )
        if existing:
            _log_record_service_event(
                NOTICE_LEVEL,
                "discogs_import_existing_record",
                "Импорт из Discogs остановлен: найдена существующая запись.",
                record_id=existing.id,
                discogs_id=discogs_id,
                barcode=normalized_barcode or "—",
                catalog_number=normalized_catalog_number or "—",
            )
            self._update_missing_identifiers(
                existing,
                barcode=normalized_barcode,
                catalog_number=normalized_catalog_number,
            )
            ensure_legacy_formats(existing)
            return existing, False

        try:
            if discogs_id:
                release = self.discogs_service.get_release(discogs_id)
            elif normalized_barcode:
                release = self.discogs_service.search_by_barcode(normalized_barcode)
            elif normalized_catalog_number:
                release = self.discogs_service.search_by_catalog_number(
                    normalized_catalog_number
                )
            else:
                raise ValueError(
                    "Нужно указать discogs_id, barecode или catalog_number для импорта из Discogs."
                )
        except DiscogsConfigError as exc:
            _log_record_service_event(
                logging.WARNING,
                "discogs_import_failed",
                "Импорт из Discogs не выполнен: ошибка конфигурации.",
                discogs_id=discogs_id,
                barcode=normalized_barcode or "—",
                catalog_number=normalized_catalog_number or "—",
                error=str(exc),
            )
            raise ValueError(str(exc)) from exc
        except DiscogsAuthError as exc:
            _log_record_service_event(
                logging.WARNING,
                "discogs_import_failed",
                "Импорт из Discogs не выполнен: ошибка авторизации.",
                discogs_id=discogs_id,
                barcode=normalized_barcode or "—",
                catalog_number=normalized_catalog_number or "—",
                error=str(exc),
            )
            raise ValueError(
                "Не удалось авторизоваться в Discogs. Проверьте API-ключ."
            ) from exc
        except DiscogsNotFoundError as exc:
            _log_record_service_event(
                NOTICE_LEVEL,
                "discogs_import_failed",
                "Импорт из Discogs не выполнен: релиз не найден.",
                discogs_id=discogs_id,
                barcode=normalized_barcode or "—",
                catalog_number=normalized_catalog_number or "—",
                error=str(exc),
            )
            if discogs_id:
                raise ValueError(
                    "Релиз с таким Discogs ID не найден. Попробуйте добавить по barecode."
                ) from exc
            raise ValueError(str(exc) or "Релиз не найден в Discogs.") from exc
        except DiscogsApiError as exc:
            _log_record_service_event(
                logging.ERROR,
                "discogs_import_failed",
                "Импорт из Discogs не выполнен: ошибка Discogs API.",
                discogs_id=discogs_id,
                barcode=normalized_barcode or "—",
                catalog_number=normalized_catalog_number or "—",
                error=str(exc),
            )
            if normalized_catalog_number:
                raise ValueError(
                    "Ошибка при импорте по каталожному номеру. Попробуйте добавить по barecode или по Discogs ID."
                ) from exc
            if discogs_id:
                raise ValueError(
                    "Ошибка при импорте по Discogs ID. Попробуйте добавить по barecode."
                ) from exc
            raise ValueError(
                "Ошибка обращения к Discogs API. Попробуйте позже."
            ) from exc
        except HTTPError as exc:
            _log_record_service_event(
                logging.ERROR,
                "discogs_import_failed",
                "Импорт из Discogs не выполнен: HTTP-ошибка провайдера.",
                discogs_id=discogs_id,
                barcode=normalized_barcode or "—",
                catalog_number=normalized_catalog_number or "—",
                error=str(exc),
            )
            if normalized_catalog_number:
                raise ValueError(
                    "Ошибка при импорте по каталожному номеру. Попробуйте добавить по barecode или по Discogs ID."
                ) from exc
            if discogs_id:
                raise ValueError(
                    "Релиз с таким Discogs ID не найден. Попробуйте добавить по barecode."
                ) from exc
            raise ValueError(
                "Ошибка обращения к Discogs API. Попробуйте позже."
            ) from exc

        payload = self._prepare_discogs_payload(adapt_discogs_release(release))
        if normalized_barcode and not payload.get("barcode"):
            payload["barcode"] = normalized_barcode
        if normalized_catalog_number and not payload.get("catalog_number"):
            payload["catalog_number"] = normalized_catalog_number
        if discogs_id and not payload.get("discogs_id"):
            payload["discogs_id"] = discogs_id

        payload_discogs_id = self._to_int_or_none(payload.get("discogs_id"))
        payload_barcode = self._normalize_barcode(payload.get("barcode"))
        payload_catalog_number = self._normalize_catalog_number(
            payload.get("catalog_number")
        )

        existing = self._find_existing_record(
            discogs_id=payload_discogs_id,
            barcode=payload_barcode,
            catalog_number=payload_catalog_number,
        )
        if existing:
            _log_record_service_event(
                NOTICE_LEVEL,
                "discogs_import_existing_record",
                "Импорт из Discogs остановлен: запись уже существует по данным payload.",
                record_id=existing.id,
                discogs_id=payload_discogs_id,
                barcode=payload_barcode or "—",
                catalog_number=payload_catalog_number or "—",
            )
            self._update_missing_identifiers(
                existing,
                discogs_id=payload_discogs_id,
                barcode=payload_barcode,
                catalog_number=payload_catalog_number,
            )
            ensure_legacy_formats(existing)
            return existing, False

        try:
            with transaction.atomic():
                record = build_record_from_payload(payload)

                self._upsert_discogs_source(record=record, release=release)

                if save_image and getattr(release, "images", None):
                    first = release.images[0]
                    uri = first.get("uri") if isinstance(first, dict) else None
                    if uri and self.image_service.download_cover(record, uri):
                        _log_record_service_event(
                            logging.INFO,
                            "discogs_cover_downloaded",
                            "Обложка записи скачана из Discogs.",
                            record_id=record.id,
                            discogs_id=record.discogs_id,
                            image_url=uri,
                        )
                enrichment_job = self.enqueue_discogs_audio_enrichment(
                    record=record,
                    requested_by_user_id=requested_by_user_id,
                )
                setattr(record, "_discogs_enrichment_job_id", str(enrichment_job.id))
        except IntegrityError as exc:
            existing = self._find_existing_record(
                discogs_id=payload_discogs_id,
                barcode=payload_barcode,
                catalog_number=payload_catalog_number,
            )
            if existing:
                _log_record_service_event(
                    NOTICE_LEVEL,
                    "discogs_import_duplicate",
                    "Импорт из Discogs завершён возвратом существующей записи после duplicate key.",
                    record_id=existing.id,
                    discogs_id=payload_discogs_id,
                    barcode=payload_barcode or "—",
                    catalog_number=payload_catalog_number or "—",
                    error=str(exc),
                )
                self._update_missing_identifiers(
                    existing,
                    discogs_id=payload_discogs_id,
                    barcode=payload_barcode,
                    catalog_number=payload_catalog_number,
                )
                ensure_legacy_formats(existing)
                return existing, False
            raise

        _log_record_service_event(
            logging.INFO,
            "discogs_import_success",
            "Импорт записи из Discogs завершён успешно.",
            record_id=record.id,
            discogs_id=record.discogs_id,
            barcode=record.barcode or "—",
            catalog_number=record.catalog_number or "—",
        )
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

        _log_record_service_event(
            logging.INFO,
            "discogs_update_start",
            "Запущено обновление записи из Discogs.",
            record_id=record.id,
            discogs_id=record.discogs_id,
            barcode=record.barcode or "—",
            catalog_number=record.catalog_number or "—",
        )

        try:
            release = self.discogs_service.get_release(record.discogs_id)
        except DiscogsConfigError as exc:
            _log_record_service_event(
                logging.WARNING,
                "discogs_update_failed",
                "Обновление из Discogs не выполнено: ошибка конфигурации.",
                record_id=record.id,
                discogs_id=record.discogs_id,
                error=str(exc),
            )
            raise ValueError(str(exc)) from exc
        except DiscogsAuthError as exc:
            _log_record_service_event(
                logging.WARNING,
                "discogs_update_failed",
                "Обновление из Discogs не выполнено: ошибка авторизации.",
                record_id=record.id,
                discogs_id=record.discogs_id,
                error=str(exc),
            )
            raise ValueError(
                "Не удалось авторизоваться в Discogs. Проверьте API-ключ."
            ) from exc
        except DiscogsNotFoundError as exc:
            _log_record_service_event(
                NOTICE_LEVEL,
                "discogs_update_failed",
                "Обновление из Discogs не выполнено: релиз не найден.",
                record_id=record.id,
                discogs_id=record.discogs_id,
                error=str(exc),
            )
            raise ValueError(f"Релиз {record.discogs_id} не найден в Discogs.") from exc
        except DiscogsApiError as exc:
            _log_record_service_event(
                logging.ERROR,
                "discogs_update_failed",
                "Обновление из Discogs не выполнено: ошибка Discogs API.",
                record_id=record.id,
                discogs_id=record.discogs_id,
                error=str(exc),
            )
            raise ValueError(
                "Ошибка обращения к Discogs API. Попробуйте позже."
            ) from exc

        payload = self._prepare_discogs_payload(
            adapt_discogs_release(release),
            record=record,
        )

        with transaction.atomic():
            update_record_from_payload(record, payload)
            self._upsert_discogs_source(record=record, release=release)

            if (
                update_image
                and not record.cover_image
                and getattr(release, "images", None)
            ):
                first = release.images[0]
                uri = first.get("uri") if isinstance(first, dict) else None
                if uri and self.image_service.download_cover(record, uri):
                    _log_record_service_event(
                        logging.INFO,
                        "discogs_cover_downloaded",
                        "Обложка записи обновлена из Discogs.",
                        record_id=record.id,
                        discogs_id=record.discogs_id,
                        image_url=uri,
                    )

        _log_record_service_event(
            logging.INFO,
            "discogs_update_success",
            "Обновление записи из Discogs завершено.",
            record_id=record.id,
            discogs_id=record.discogs_id,
            barcode=record.barcode or "—",
            catalog_number=record.catalog_number or "—",
        )
        return record

    def enqueue_discogs_audio_enrichment(
        self,
        *,
        record: Record,
        requested_by_user_id: int | None = None,
    ) -> AudioEnrichmentJob:
        """Ставит в очередь YouTube enrichment для новой Discogs-записи."""
        return self.enqueue_youtube_audio_enrichment(
            record_ids=[record.id],
            source=AudioEnrichmentJob.Source.DISCOGS_IMPORT,
            overwrite_existing=False,
            requested_by_user_id=requested_by_user_id,
        )

    def enqueue_manual_youtube_audio_enrichment(
        self,
        *,
        record_ids: list[int],
        requested_by_user_id: int | None = None,
    ) -> AudioEnrichmentJob:
        """Ставит массовый ручной YouTube enrichment в очередь."""
        return self.enqueue_youtube_audio_enrichment(
            record_ids=record_ids,
            source=AudioEnrichmentJob.Source.MANUAL_LIST,
            overwrite_existing=True,
            requested_by_user_id=requested_by_user_id,
        )

    def enqueue_record_youtube_audio_enrichment(
        self,
        *,
        record: Record,
        requested_by_user_id: int | None = None,
    ) -> AudioEnrichmentJob:
        """Ставит в очередь single-record ручной YouTube enrichment."""
        return self.enqueue_youtube_audio_enrichment(
            record_ids=[record.id],
            source=AudioEnrichmentJob.Source.MANUAL_RECORD,
            overwrite_existing=True,
            requested_by_user_id=requested_by_user_id,
        )

    def enqueue_track_youtube_audio_enrichment(
        self,
        *,
        track: Track,
        requested_by_user_id: int | None = None,
        overwrite_existing: bool = False,
    ) -> AudioEnrichmentJob:
        """Ставит в очередь обработку одного трека из YouTube."""
        job = AudioEnrichmentJob.objects.create(
            source=AudioEnrichmentJob.Source.MANUAL_RECORD,
            status=AudioEnrichmentJob.Status.QUEUED,
            requested_by_user_id=requested_by_user_id,
            overwrite_existing=overwrite_existing,
            total_records=1,
            total_tracks=1,
        )
        payload = {
            "job_id": str(job.id),
            "track_id": track.pk,
            "overwrite_existing": overwrite_existing,
        }
        transaction.on_commit(
            lambda: process_youtube_enrichment_track.delay(payload)  # noqa: B023
        )
        _log_record_service_event(
            logging.INFO,
            "youtube_audio_track_enqueued",
            "Поставлена в очередь задача обновления mp3 для трека.",
            job_id=job.id,
            record_id=track.record_id,
            track_id=track.pk,
            overwrite=overwrite_existing,
            requested_by_user_id=requested_by_user_id,
        )
        return job

    def enqueue_youtube_audio_enrichment(
        self,
        *,
        record_ids: list[int],
        source: str,
        overwrite_existing: bool,
        requested_by_user_id: int | None = None,
    ) -> AudioEnrichmentJob:
        """Создаёт job report и enqueue-ит background task в Celery."""
        normalized_record_ids = sorted({int(record_id) for record_id in record_ids})
        if not normalized_record_ids:
            raise ValueError(
                "Список record_ids для YouTube enrichment не должен быть пустым."
            )

        job = AudioEnrichmentJob.objects.create(
            source=source,
            status=AudioEnrichmentJob.Status.QUEUED,
            requested_by_user_id=requested_by_user_id,
            overwrite_existing=overwrite_existing,
            total_records=len(normalized_record_ids),
        )
        payload = {
            "job_id": str(job.id),
            "record_ids": normalized_record_ids,
            "overwrite_existing": overwrite_existing,
            "requested_by_user_id": requested_by_user_id,
            "source": source,
        }
        transaction.on_commit(
            lambda: run_youtube_enrichment_job.delay(payload)  # noqa: B023
        )
        _log_record_service_event(
            logging.INFO,
            "youtube_audio_job_enqueued",
            "Поставлена в очередь задача обновления аудио из YouTube.",
            job_id=job.id,
            source=source,
            overwrite=overwrite_existing,
            records_total=len(normalized_record_ids),
            record_ids=",".join(str(record_id) for record_id in normalized_record_ids),
            requested_by_user_id=requested_by_user_id,
        )
        return job

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
        normalized_catalog_number = self._normalize_redeye_catalog_number(
            catalog_number
        )
        if not normalized_catalog_number:
            raise ValueError(
                "Не указан каталожный номер (catalog_number) для импорта из Redeye."
            )

        existing = Record.objects.filter(
            catalog_number__iexact=normalized_catalog_number
        ).first()
        if existing:
            _log_record_service_event(
                NOTICE_LEVEL,
                "redeye_import_existing_record",
                "Импорт из Redeye остановлен: найдена существующая запись по каталожному номеру.",
                record_id=existing.id,
                catalog_number=normalized_catalog_number,
            )
            ensure_legacy_formats(existing)
            if download_audio_decision:
                try:
                    resolved_page_url = self._ensure_redeye_source_for_record(
                        existing, normalized_catalog_number
                    )
                    self.attach_audio_from_redeye(
                        existing, force=False, page_url=resolved_page_url
                    )
                except Exception as err:  # noqa: BLE001 — логируем любую ошибку привязки
                    _log_record_service_event(
                        logging.WARNING,
                        "redeye_audio_attach_failed",
                        "Докачка аудио из Redeye для существующей записи завершилась с ошибкой.",
                        record_id=existing.id,
                        catalog_number=normalized_catalog_number,
                        error=str(err),
                    )
            return existing, False

        result = None
        if raw_payload is None:
            result = self.redeye_service.fetch_by_catalog_number(
                normalized_catalog_number
            )
            raw_payload = result.payload or {}

        payload = self._prepare_non_discogs_payload(
            adapt_redeye_payload(raw_payload),
            source_name="Redeye import",
            catalog_number=normalized_catalog_number,
        )
        payload["catalog_number"] = normalized_catalog_number

        try:
            with transaction.atomic():
                record = build_record_from_payload(payload)

                cover_url = raw_payload.get("image_url")
                if save_image_decision and cover_url:
                    if self.image_service.download_cover(record, cover_url):
                        _log_record_service_event(
                            logging.INFO,
                            "redeye_cover_downloaded",
                            "Обложка записи скачана из Redeye.",
                            record_id=record.id,
                            catalog_number=normalized_catalog_number,
                            image_url=str(cover_url),
                        )

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
                effective_source_url = normalize_abs_url(effective_source_url or "")

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
                        _log_record_service_event(
                            logging.WARNING,
                            "redeye_source_invalid",
                            "URL источника Redeye не прошёл валидацию.",
                            record_id=record.id,
                            catalog_number=normalized_catalog_number,
                            error=str(ve),
                        )
        except IntegrityError:
            existing = Record.objects.filter(
                catalog_number__iexact=normalized_catalog_number
            ).first()
            if existing:
                _log_record_service_event(
                    NOTICE_LEVEL,
                    "redeye_import_duplicate",
                    "Импорт из Redeye завершён возвратом существующей записи после duplicate key.",
                    record_id=existing.id,
                    catalog_number=normalized_catalog_number,
                )
                ensure_legacy_formats(existing)
                return existing, False
            raise

        if download_audio_decision:
            try:
                self.attach_audio_from_redeye(record, force=False)
            except Exception as err:  # noqa: BLE001
                _log_record_service_event(
                    logging.WARNING,
                    "redeye_audio_attach_failed",
                    "Докачка аудио из Redeye завершилась с ошибкой.",
                    record_id=record.pk,
                    catalog_number=normalized_catalog_number,
                    error=str(err),
                )

        _log_record_service_event(
            logging.INFO,
            "redeye_import_success",
            "Импорт записи из Redeye завершён успешно.",
            record_id=record.id,
            catalog_number=record.catalog_number or normalized_catalog_number,
        )
        return record, True

    def attach_audio_from_redeye(
        self,
        record: "Record",
        *,
        force: bool = False,
        require_source: bool = False,
        page_url: Optional[str] = None,
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

        resolved_page_url: str | None = normalize_abs_url(page_url or "") or None
        if resolved_page_url:
            try:
                validate_redeye_product_url(resolved_page_url)
            except ValueError:
                _log_record_service_event(
                    logging.WARNING,
                    "redeye_source_invalid",
                    "Переданный URL Redeye не прошёл валидацию и будет проигнорирован.",
                    record_id=record.pk,
                    source_url=resolved_page_url,
                )
                resolved_page_url = None

        if not resolved_page_url:
            source_obj = (
                record.sources.filter(
                    provider=RecordSource.Provider.REDEYE,
                    role=RecordSource.Role.PRODUCT_PAGE,
                )
                .only("url")
                .first()
            )
            existing_source_url = source_obj.url if source_obj else None
            normalized_source_url = normalize_abs_url(existing_source_url or "") or None
            if normalized_source_url:
                try:
                    validate_redeye_product_url(normalized_source_url)
                    if (
                        existing_source_url
                        and normalized_source_url != existing_source_url
                    ):
                        self._upsert_record_source(
                            record=record,
                            provider=RecordSource.Provider.REDEYE,
                            role=RecordSource.Role.PRODUCT_PAGE,
                            url=normalized_source_url,
                            can_fetch_audio=True,
                        )
                except ValueError:
                    _log_record_service_event(
                        logging.WARNING,
                        "redeye_source_invalid",
                        "Сохранённый URL Redeye не прошёл валидацию и будет проигнорирован.",
                        record_id=record.pk,
                        source_url=existing_source_url or "—",
                    )
                    normalized_source_url = None
            resolved_page_url = normalized_source_url

        if not resolved_page_url:
            resolved_page_url = self._ensure_redeye_source_for_record(
                record, getattr(record, "catalog_number", None)
            )

        if require_source and not resolved_page_url:
            normalized_catalog_number = self._normalize_redeye_catalog_number(
                getattr(record, "catalog_number", None)
            )
            if normalized_catalog_number:
                reason = (
                    "Обновление из Redeye невозможно: не найден релиз с точным "
                    f"совпадением каталожного номера '{normalized_catalog_number}'."
                )
            else:
                reason = (
                    "Обновление из Redeye невозможно: у записи отсутствует "
                    "каталожный номер."
                )
            _log_record_service_event(
                NOTICE_LEVEL,
                "redeye_update_aborted",
                "Обновление аудио из Redeye остановлено: источник не найден.",
                record_id=record.pk,
                catalog_number=normalized_catalog_number or "—",
                reason=reason,
            )
            raise ValueError(reason)

        updated = self.audio_service.attach_audio_from_redeye(
            record=record,
            page_url=resolved_page_url,
            force=force,
            per_click_timeout_sec=per_click_timeout_sec,
            browser=browser,
        )
        _log_record_service_event(
            logging.INFO,
            "redeye_audio_attach_finished",
            "Завершено прикрепление аудио из Redeye.",
            record_id=record.pk,
            updated_tracks=updated,
            overwrite=force,
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

    def _ensure_redeye_source_for_record(
        self, record: Record, catalog_number: Optional[str]
    ) -> Optional[str]:
        """
        Гарантирует наличие валидного Redeye PRODUCT_PAGE source для записи.

        Если валидный источник уже есть — возвращает его URL.
        Иначе пытается найти карточку по catalog_number и upsert-ит RecordSource.
        """
        existing_source_url = (
            record.sources.filter(
                provider=RecordSource.Provider.REDEYE,
                role=RecordSource.Role.PRODUCT_PAGE,
            )
            .values_list("url", flat=True)
            .first()
        ) or None
        normalized_existing = normalize_abs_url(existing_source_url or "") or None
        if normalized_existing:
            try:
                validate_redeye_product_url(normalized_existing)
                if normalized_existing != existing_source_url:
                    self._upsert_record_source(
                        record=record,
                        provider=RecordSource.Provider.REDEYE,
                        role=RecordSource.Role.PRODUCT_PAGE,
                        url=normalized_existing,
                        can_fetch_audio=True,
                    )
                return normalized_existing
            except ValueError:
                _log_record_service_event(
                    logging.INFO,
                    "redeye_source_url_invalid",
                    "Игнорирован невалидный сохранённый URL Redeye.",
                    record_id=record.pk,
                    source=existing_source_url,
                )

        normalized_catalog_number = self._normalize_redeye_catalog_number(
            catalog_number
        )
        if not normalized_catalog_number:
            return None

        try:
            result = self.redeye_service.fetch_by_catalog_number(
                normalized_catalog_number
            )
        except Exception as exc:  # noqa: BLE001
            _log_record_service_event(
                logging.INFO,
                "redeye_fetch_failed",
                "Не удалось получить карточку Redeye по каталожному номеру.",
                record_id=record.pk,
                catalog_number=normalized_catalog_number,
                error=str(exc),
            )
            return None

        source_url = normalize_abs_url(getattr(result, "source_url", "") or "")
        if not source_url:
            raw_payload = getattr(result, "payload", {}) or {}
            raw_source = raw_payload.get("source")
            raw_source_url = (
                raw_source.get("url") if isinstance(raw_source, dict) else None
            )
            source_url = normalize_abs_url(raw_source_url or "")
        if not source_url:
            return None

        try:
            validate_redeye_product_url(source_url)
        except ValueError as exc:
            _log_record_service_event(
                logging.INFO,
                "redeye_source_url_invalid",
                "URL Redeye после резолва невалиден.",
                record_id=record.pk,
                catalog_number=normalized_catalog_number,
                source=source_url,
                error=str(exc),
            )
            return None

        self._upsert_record_source(
            record=record,
            provider=RecordSource.Provider.REDEYE,
            role=RecordSource.Role.PRODUCT_PAGE,
            url=source_url,
            can_fetch_audio=True,
        )
        return source_url

    @staticmethod
    def _payload_list_length(payload: dict[str, object], key: str) -> int:
        value = payload.get(key)
        return len(value) if isinstance(value, list) else 0

    def _prepare_discogs_payload(
        self,
        payload: dict[str, object],
        *,
        record: Record | None = None,
    ) -> dict[str, object]:
        """
        Делает structured format contract Discogs явным.

        Для импорта/обновления из Discogs structured_formats всегда должны быть
        переданы дальше в сборщик, даже если список пустой.
        """
        normalized = dict(payload)
        normalized.setdefault("structured_formats", [])
        if record is not None:
            normalized["formats"] = self._record_legacy_formats_or_default(record)
        else:
            normalized["formats"] = [FormatChoices.NOT_SPECIFIED]
        _log_record_service_event(
            logging.DEBUG,
            "discogs_payload_prepared",
            "Подготовлен payload Discogs для сборки записи.",
            record_id=getattr(record, "pk", None) or "new",
            structured_formats=self._payload_list_length(
                normalized, "structured_formats"
            ),
            legacy_formats=self._payload_list_length(normalized, "formats"),
        )
        return normalized

    def _prepare_non_discogs_payload(
        self,
        payload: dict[str, object],
        *,
        source_name: str,
        catalog_number: str | None,
    ) -> dict[str, object]:
        """
        Гарантирует, что не-Discogs payload не активирует structured-mode пустым ключом.
        """
        normalized = dict(payload)
        normalized["formats"] = [FormatChoices.NOT_SPECIFIED]
        structured_count = self._payload_list_length(normalized, "structured_formats")
        if structured_count == 0:
            normalized.pop("structured_formats", None)
            _log_record_service_event(
                logging.DEBUG,
                "provider_payload_prepared",
                "Подготовлен payload провайдера без structured_formats.",
                source=source_name,
                catalog_number=catalog_number or "—",
                legacy_formats=self._payload_list_length(normalized, "formats"),
            )
            return normalized

        _log_record_service_event(
            logging.DEBUG,
            "provider_payload_prepared",
            "Подготовлен payload провайдера со structured_formats.",
            source=source_name,
            catalog_number=catalog_number or "—",
            structured_formats=structured_count,
        )
        return normalized

    @staticmethod
    def _record_legacy_formats_or_default(record: Record) -> list[str]:
        names = list(record.formats.values_list("name", flat=True))
        return names or [FormatChoices.NOT_SPECIFIED]

    @staticmethod
    def _is_empty_identifier(value: Optional[str]) -> bool:
        """Метод проверяет, является ли идентификатор пустым (None/''/пробелы)."""
        return value is None or (isinstance(value, str) and value.strip() == "")

    @staticmethod
    def _to_int_or_none(value: object) -> int | None:
        """Безопасно преобразует значение в int."""
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_barcode(value: object) -> str | None:
        """Нормализует barcode в цифры (если возможно)."""
        if not isinstance(value, str):
            return None
        raw = value.strip()
        if not raw:
            return None
        digits = "".join(ch for ch in raw if ch.isdigit())
        return digits or raw

    @staticmethod
    def _normalize_catalog_number(value: object) -> str | None:
        """Нормализует catalog_number: trim + upper."""
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        return normalized or None

    @staticmethod
    def _normalize_redeye_catalog_number(value: object) -> str | None:
        """
        Нормализует catalog_number для поиска в Redeye.

        Помимо trim+upper отбрасывает псевдо-пустые значения.
        """
        normalized = RecordService._normalize_catalog_number(value)
        if not normalized:
            return None
        if normalized in {"NONE", "NULL", "N/A", "N-A", "-", "—"}:
            return None
        return normalized

    def _find_existing_record(
        self,
        *,
        discogs_id: int | None = None,
        barcode: str | None = None,
        catalog_number: str | None = None,
    ) -> Record | None:
        """Ищет существующую запись по набору идентификаторов."""
        if discogs_id is not None:
            existing = Record.objects.find_by_discogs_id(discogs_id)
            if existing:
                return existing

        if barcode:
            existing = Record.objects.find_by_barcode(barcode)
            if existing:
                return existing

        if catalog_number:
            existing = Record.objects.filter(
                catalog_number__iexact=catalog_number
            ).first()
            if existing:
                return existing

        return None

    @staticmethod
    def _has_identifier_conflict(
        record: Record,
        *,
        discogs_id: int | None = None,
        barcode: str | None = None,
        catalog_number: str | None = None,
    ) -> bool:
        """Проверяет, занят ли идентификатор другой записью."""
        if (
            discogs_id is not None
            and Record.objects.filter(discogs_id=discogs_id)
            .exclude(pk=record.pk)
            .exists()
        ):
            return True
        if (
            barcode
            and Record.objects.filter(barcode=barcode).exclude(pk=record.pk).exists()
        ):
            return True
        if (
            catalog_number
            and Record.objects.filter(catalog_number__iexact=catalog_number)
            .exclude(pk=record.pk)
            .exists()
        ):
            return True
        return False

    def _update_missing_identifiers(
        self,
        record: Record,
        discogs_id: int | None = None,
        barcode: Optional[str] = None,
        catalog_number: Optional[str] = None,
    ) -> None:
        """Метод заполняет недостающие идентификаторы у записи (если они пусты)."""
        update_fields: list[str] = []

        if discogs_id is not None and record.discogs_id is None:
            if self._has_identifier_conflict(record, discogs_id=discogs_id):
                _log_record_service_event(
                    logging.INFO,
                    "discogs_id_conflict",
                    "Пропущено обновление discogs_id: значение уже занято.",
                    record_id=record.id,
                    discogs_id=discogs_id,
                )
            else:
                record.discogs_id = discogs_id
                update_fields.append("discogs_id")
                _log_record_service_event(
                    logging.INFO,
                    "discogs_id_filled",
                    "Добавлен недостающий discogs_id.",
                    record_id=record.id,
                    discogs_id=discogs_id,
                )

        if barcode and self._is_empty_identifier(record.barcode):
            if self._has_identifier_conflict(record, barcode=barcode):
                _log_record_service_event(
                    logging.INFO,
                    "barcode_conflict",
                    "Пропущено обновление barcode: значение уже занято.",
                    record_id=record.id,
                    barcode=barcode,
                )
            else:
                record.barcode = barcode
                update_fields.append("barcode")
                _log_record_service_event(
                    logging.INFO,
                    "barcode_filled",
                    "Добавлен недостающий barcode.",
                    record_id=record.id,
                    barcode=barcode,
                )

        if catalog_number and self._is_empty_identifier(record.catalog_number):
            if self._has_identifier_conflict(record, catalog_number=catalog_number):
                _log_record_service_event(
                    logging.INFO,
                    "catalog_number_conflict",
                    "Пропущено обновление catalog_number: значение уже занято.",
                    record_id=record.id,
                    catalog_number=catalog_number,
                )
            else:
                record.catalog_number = catalog_number
                update_fields.append("catalog_number")
                _log_record_service_event(
                    logging.INFO,
                    "catalog_number_filled",
                    "Добавлен недостающий catalog_number.",
                    record_id=record.id,
                    catalog_number=catalog_number,
                )

        if update_fields:
            record.save(update_fields=update_fields)

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
            _log_record_service_event(
                logging.INFO,
                "catalog_number_filled",
                "Добавлен недостающий catalog_number.",
                record_id=record.id,
                catalog_number=record.catalog_number,
            )

        if self._is_empty_identifier(record.barcode) and record_data.get("barcode"):
            record.barcode = record_data["barcode"]
            _log_record_service_event(
                logging.INFO,
                "barcode_filled",
                "Добавлен недостающий barcode.",
                record_id=record.id,
                barcode=record.barcode,
            )

        changes = []
        for field, old_value in old_values.items():
            new_value = getattr(record, field if field != "year" else "release_year")
            if old_value != new_value:
                changes.append(f"{field}: '{old_value}' → '{new_value}'")
        if changes:
            _log_record_service_event(
                logging.INFO,
                "record_fields_updated",
                "Обновлены поля записи.",
                record_id=record.id,
                changes=", ".join(changes),
            )

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
            _log_record_service_event(
                logging.INFO,
                "record_source_created",
                "Добавлен источник записи.",
                record_id=record.pk,
                source=f"{provider}/{role}",
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
            _log_record_service_event(
                logging.INFO,
                "record_source_updated",
                "Обновлён источник записи.",
                record_id=record.pk,
                source=f"{provider}/{role}",
            )
        return obj

    @staticmethod
    def _extract_discogs_release_id(
        release: object, fallback: int | None = None
    ) -> int | None:
        """Метод извлекает discogs release id из объекта релиза или fallback."""
        release_id = getattr(release, "id", None)
        if isinstance(release_id, int):
            return release_id

        data = getattr(release, "data", None)
        if isinstance(data, dict):
            raw_id = data.get("id")
            try:
                return int(raw_id)
            except (TypeError, ValueError):
                pass

        return fallback

    @staticmethod
    def _resolve_discogs_source_url(
        release: object, release_id: int | None = None
    ) -> str | None:
        """Метод выбирает URL источника Discogs (с fallback на API release endpoint)."""
        candidates: list[str] = []

        for attr in ("resource_url", "uri", "url"):
            value = getattr(release, attr, None)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

        data = getattr(release, "data", None)
        if isinstance(data, dict):
            for key in ("resource_url", "uri", "url"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())

        for url in candidates:
            if url.startswith("https://") or url.startswith("http://"):
                return url

        if release_id is not None:
            return f"https://api.discogs.com/releases/{release_id}"

        return None

    def _upsert_discogs_source(self, *, record: Record, release: object) -> None:
        """Метод создаёт/обновляет RecordSource для Discogs API."""
        release_id = self._extract_discogs_release_id(
            release=release, fallback=record.discogs_id
        )
        source_url = self._resolve_discogs_source_url(
            release=release, release_id=release_id
        )
        if not source_url:
            _log_record_service_event(
                logging.WARNING,
                "discogs_source_url_missing",
                "Не удалось определить URL источника Discogs.",
                record_id=record.pk,
                discogs_id=release_id,
            )
            return

        self._upsert_record_source(
            record=record,
            provider=RecordSource.Provider.DISCOGS,
            role=RecordSource.Role.API,
            url=source_url,
            can_fetch_audio=False,
        )
