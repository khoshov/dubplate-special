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

from records.models import (
    FormatChoices,
    Record,
    RecordSource,
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
        discogs_id: Optional[int] = None,
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
        logger.info(
            "Discogs import started: discogs_id=%s, barcode=%s, catalog_number=%s",
            discogs_id,
            barcode,
            catalog_number,
        )
        normalized_barcode = self._normalize_barcode(barcode)
        normalized_catalog_number = self._normalize_catalog_number(catalog_number)

        existing = self._find_existing_record(
            discogs_id=discogs_id,
            barcode=normalized_barcode,
            catalog_number=normalized_catalog_number,
        )
        if existing:
            logger.info("Discogs import: найдена существующая запись: %s", existing.id)
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
            logger.info("Discogs import failed: config error: %s", exc)
            raise ValueError(str(exc)) from exc
        except DiscogsAuthError as exc:
            logger.info("Discogs import failed: auth error: %s", exc)
            raise ValueError(
                "Не удалось авторизоваться в Discogs. Проверьте API-ключ."
            ) from exc
        except DiscogsNotFoundError as exc:
            logger.info("Discogs import failed: release not found: %s", exc)
            if discogs_id:
                raise ValueError(
                    "Релиз с таким Discogs ID не найден. Попробуйте добавить по barecode."
                ) from exc
            raise ValueError(str(exc) or "Релиз не найден в Discogs.") from exc
        except DiscogsApiError as exc:
            logger.info("Discogs import failed: API error: %s", exc)
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
            logger.info("Discogs import failed: raw HTTPError: %s", exc)
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
            logger.info(
                "Discogs import: найдена существующая запись по payload (record_id=%s).",
                existing.id,
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
                        logger.info(
                            "Обложка скачана для записи %s (Discogs)", record.id
                        )
        except IntegrityError as exc:
            existing = self._find_existing_record(
                discogs_id=payload_discogs_id,
                barcode=payload_barcode,
                catalog_number=payload_catalog_number,
            )
            if existing:
                logger.info(
                    "Discogs import: duplicate key на сохранении, возвращаю существующую запись %s: %s",
                    existing.id,
                    exc,
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

        logger.info(
            "Discogs import succeeded: record_id=%s, discogs_id=%s",
            record.id,
            record.discogs_id,
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

        logger.info(
            "Начато обновление из Discogs для записи %s (Discogs ID: %s, Barcode: '%s', Catalog: '%s')",
            record.id,
            record.discogs_id,
            record.barcode,
            record.catalog_number,
        )

        try:
            release = self.discogs_service.get_release(record.discogs_id)
        except DiscogsConfigError as exc:
            logger.warning("Discogs config error on update: %s", exc)
            raise ValueError(str(exc)) from exc
        except DiscogsAuthError as exc:
            logger.warning("Discogs auth error on update: %s", exc)
            raise ValueError(
                "Не удалось авторизоваться в Discogs. Проверьте API-ключ."
            ) from exc
        except DiscogsNotFoundError as exc:
            logger.info("Discogs release not found on update: %s", exc)
            raise ValueError(f"Релиз {record.discogs_id} не найден в Discogs.") from exc
        except DiscogsApiError as exc:
            logger.error("Discogs API error on update: %s", exc)
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
            logger.info(
                "Найдена существующая запись по каталожному номеру (Redeye): %s",
                existing.id,
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
                ensure_legacy_formats(existing)
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
                logger.info(
                    "Передан невалидный URL Redeye для записи %s: %s",
                    record.pk,
                    resolved_page_url,
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
                    logger.info(
                        "Сохранённый URL Redeye невалиден для записи %s: %s",
                        record.pk,
                        existing_source_url,
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
            logger.info(
                "Redeye update aborted: record_id=%s, catalog_number=%s, reason=%s",
                record.pk,
                normalized_catalog_number or "—",
                reason,
            )
            raise ValueError(reason)

        updated = self.audio_service.attach_audio_from_redeye(
            record=record,
            page_url=resolved_page_url,
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
                logger.info(
                    "Игнорирую невалидный сохранённый URL Redeye для записи %s: %s",
                    record.pk,
                    existing_source_url,
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
            logger.info(
                "Не удалось получить карточку Redeye по CAT=%s для записи %s: %s",
                normalized_catalog_number,
                record.pk,
                exc,
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
            logger.info(
                "URL Redeye после резолва невалиден (record=%s, CAT=%s): %s (%s)",
                record.pk,
                normalized_catalog_number,
                source_url,
                exc,
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
        logger.info(
            "Discogs payload prepared: record_id=%s, structured_formats=%d, legacy_formats=%d",
            getattr(record, "pk", None) or "new",
            self._payload_list_length(normalized, "structured_formats"),
            self._payload_list_length(normalized, "formats"),
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
            logger.info(
                "%s payload prepared without structured_formats: catalog_number=%s, legacy_formats=%d",
                source_name,
                catalog_number or "—",
                self._payload_list_length(normalized, "formats"),
            )
            return normalized

        logger.info(
            "%s payload includes optional structured_formats: catalog_number=%s, structured_formats=%d",
            source_name,
            catalog_number or "—",
            structured_count,
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
                logger.info(
                    "Пропущено обновление discogs_id для записи %s: значение %s уже занято.",
                    record.id,
                    discogs_id,
                )
            else:
                record.discogs_id = discogs_id
                update_fields.append("discogs_id")
                logger.info(
                    "Добавлен недостающий discogs_id для записи %s: %s",
                    record.id,
                    discogs_id,
                )

        if barcode and self._is_empty_identifier(record.barcode):
            if self._has_identifier_conflict(record, barcode=barcode):
                logger.info(
                    "Пропущено обновление barcode для записи %s: значение %s уже занято.",
                    record.id,
                    barcode,
                )
            else:
                record.barcode = barcode
                update_fields.append("barcode")
                logger.info(
                    "Добавлен недостающий barcode для записи %s: %s",
                    record.id,
                    barcode,
                )

        if catalog_number and self._is_empty_identifier(record.catalog_number):
            if self._has_identifier_conflict(record, catalog_number=catalog_number):
                logger.info(
                    "Пропущено обновление catalog_number для записи %s: значение %s уже занято.",
                    record.id,
                    catalog_number,
                )
            else:
                record.catalog_number = catalog_number
                update_fields.append("catalog_number")
                logger.info(
                    "Добавлен недостающий catalog_number для записи %s: %s",
                    record.id,
                    catalog_number,
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
            logger.warning(
                "Не удалось определить URL источника Discogs для записи %s (release_id=%s).",
                record.pk,
                release_id,
            )
            return

        self._upsert_record_source(
            record=record,
            provider=RecordSource.Provider.DISCOGS,
            role=RecordSource.Role.API,
            url=source_url,
            can_fetch_audio=False,
        )
