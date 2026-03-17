"""
Пакетный импорт карточек Redeye:
  - обходит ссылки карточек из листинга (RedeyeListingScraper),
  - парсит карточку через RecordService.parse_redeye_product_by_url(...),
  - при save=True сохраняет запись через RecordService.import_from_redeye(...),
  - НЕ качает аудио в рамках manage-команды (download_audio_decision=False),
  - гарантирует наличие источника redeye/product_page в RecordSource,
  - отдаёт результаты построчно (ok/created/updated/skipped_duplicate/url/summary/error) для CLI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterator, Optional

from django.db import transaction

from config.logging import NOTICE_LEVEL, log_event
from records.models import Genre, Record, Style
from records.services.audio.audio_service import AudioService
from records.services.image.image_service import ImageService
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.providers.redeye.redeye_service import RedeyeService
from records.services.record_service import RecordService

logger = logging.getLogger(__name__)
_REDEYE_BULK_COMPONENT = "redeye_bulk_import"


def _log_redeye_bulk_event(
    level: int,
    event: str,
    message: str,
    **context: object,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_REDEYE_BULK_COMPONENT,
        event=event,
        **context,
    )


@dataclass
class BulkResult:
    url: str
    ok: bool
    created: bool = False
    updated: bool = False
    skipped_duplicate: bool = False
    summary: Optional[Dict] = None
    error: Optional[str] = None


class RedeyeBulkImporter:
    """
    Класс выполняет обход списка url-адресов на Redeye и обработку карточек.
    """

    def __init__(
        self,
    ) -> None:
        self.svc = RecordService(
            discogs_service=DiscogsService(),
            redeye_service=RedeyeService(),
            image_service=ImageService(),
            audio_service=AudioService(),
        )

    def crawl_category(
        self,
        listing_url: str,
        *,
        category_name: str = "Redeye",
        attach_genre: Optional[str] = None,
        attach_style: Optional[str] = None,
        limit: Optional[int] = None,
        save: bool = False,
    ) -> Iterator["BulkResult"]:
        """
        Обходит список категории Redeye, парсит карточки и (опционально) сохраняет их через RecordService.

        Аргументы:
            listing_url: URL страницы листинга (категория/подкатегория Redeye).
            category_name: имя категории для логов (необязательный).
            attach_genre: (опц.) добавить жанр к записи.
            attach_style: (опц.) добавить стиль к записи.
            limit: максимум карточек к обработке.
            save: при True — сохраняем запись; аудио НЕ качаем.
        """
        from records.scrapers.redeye_listing import RedeyeListingScraper

        _log_redeye_bulk_event(
            logging.INFO,
            "crawl_start",
            "Запущен обход категории Redeye.",
            category=category_name,
            source=listing_url,
            limit=limit,
            save=save,
        )

        scraper = RedeyeListingScraper()

        processed = 0
        duplicate_catalog_numbers: list[str] = []

        for product_url in scraper.iter_product_urls(listing_url):
            if limit is not None and processed >= limit:
                _log_redeye_bulk_event(
                    NOTICE_LEVEL,
                    "limit_reached",
                    "Достигнут лимит обработки карточек Redeye.",
                    limit=limit,
                )
                break

            try:
                raw_payload: dict = (
                    self.svc.parse_redeye_product_by_url(product_url) or {}
                )

                catalog_number = (
                    (raw_payload.get("catalog_number") or "").strip().upper()
                )
                summary = self._summary_from_payload(raw_payload)

                if not catalog_number:
                    _log_redeye_bulk_event(
                        NOTICE_LEVEL,
                        "catalog_missing",
                        "Карточка Redeye пропущена: нет каталожного номера.",
                        source=product_url,
                    )
                    processed += 1
                    yield BulkResult(
                        url=product_url,
                        ok=False,
                        error="catalog_number is empty",
                        summary=summary,
                    )
                    continue

                if not save:
                    _log_redeye_bulk_event(
                        logging.INFO,
                        "parsed_only",
                        "Карточка Redeye обработана без сохранения.",
                        source=product_url,
                        catalog_number=catalog_number,
                    )
                    processed += 1
                    yield BulkResult(url=product_url, ok=True, summary=summary)
                    continue

                with transaction.atomic():
                    record, created = self.svc.import_from_redeye(
                        catalog_number=catalog_number,
                        raw_payload=raw_payload,
                        source_url=product_url,
                        save_image_decision=True,
                        download_audio_decision=False,
                    )

                    if created:
                        if attach_genre:
                            self._attach_single_choice(
                                record,
                                model=Genre,
                                field_name="genres",
                                name=attach_genre,
                            )
                        if attach_style:
                            self._attach_single_choice(
                                record,
                                model=Style,
                                field_name="styles",
                                name=attach_style,
                            )
                    else:
                        duplicate_catalog_numbers.append(catalog_number)

                processed += 1
                yield BulkResult(
                    url=product_url,
                    ok=True,
                    created=bool(created),
                    updated=False,
                    skipped_duplicate=not created,
                    summary=summary,
                )

            except Exception as e:
                _log_redeye_bulk_event(
                    logging.ERROR,
                    "import_failed",
                    "Ошибка импорта карточки Redeye.",
                    source=product_url,
                    error=str(e),
                )
                processed += 1
                yield BulkResult(url=product_url, ok=False, error=str(e))

        _log_redeye_bulk_event(
            logging.INFO,
            "crawl_finish",
            "Обход категории Redeye завершён.",
            processed=processed,
        )
        if duplicate_catalog_numbers:
            _log_redeye_bulk_event(
                NOTICE_LEVEL,
                "duplicates_detected",
                "Пропущены дубли Redeye (catalog_number уже есть в БД).",
                duplicates_total=len(duplicate_catalog_numbers),
                catalog_numbers=", ".join(duplicate_catalog_numbers),
            )

    @staticmethod
    def _summary_from_payload(payload: Dict) -> Dict:
        """Формирует компактное резюме для CLI-вывода."""
        title = payload.get("title") or "-"
        artists = ", ".join(payload.get("artists") or []) or "-"
        label = payload.get("label") or "-"
        catalog_number = payload.get("catalog_number") or "-"
        availability = (
            payload.get("availability") or payload.get("availability_text") or "-"
        )
        price = payload.get("price") or "-"
        # дата может быть частичной
        y = payload.get("release_year")
        m = payload.get("release_month")
        d = payload.get("release_day")
        if y and m and d:
            release = f"{y:04d}-{m:02d}-{d:02d}"
        elif y and m:
            release = f"{y:04d}-{m:02d}"
        elif y:
            release = f"{y:04d}"
        else:
            release = "-"

        return {
            "title": title,
            "artists": artists,
            "label": label,
            "catalog_number": catalog_number,
            "release": release,
            "availability": availability,
            "price": price,
        }

    @staticmethod
    def _attach_single_choice(
        record: Record, *, model, field_name: str, name: str
    ) -> None:
        """
        Добавляет единичное значение в m2m (Genre/Style) для записи.
        """
        name = (name or "").strip()
        if not name:
            return
        obj, _ = model.objects.get_or_create(name=name)
        getattr(record, field_name).add(obj)
