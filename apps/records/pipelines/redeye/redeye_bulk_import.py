# apps/records/pipelines/redeye_bulk_import.py
"""
Пакетный импорт карточек Redeye:
  - обходит списки urls пластинок (iterate_category_urls);
  - парсит карточки RedeyeService.parse_product_by_url(...);
  - по данным карточки пластинки (payload) делает upsert Record
  (create or update в БД) + m2m + cover + ТРЕКИ.
"""

from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from typing import Iterable, Optional, Dict, Any, List

import requests
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.text import slugify

from ...models import Record, Genre, Style, Label, Artist
from ...scrapers.redeye_listing import iterate_category_urls
from ...services.providers.redeye.redeye_service import RedeyeService
from ...services.record_service import RecordService
from records.services.tracklist_writer import create_tracks_for_record
from ...models import RecordSource
# from .services.record_service import RecordService
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.image.image_service import ImageService

logger = logging.getLogger(__name__)


@dataclass
class ImportResult:
    url: str
    ok: bool
    created: bool
    updated: bool
    error: Optional[str] = None
    record_id: Optional[str] = None
    catalog_number: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None


class RedeyeBulkImporter:
    """
    Обход категорий и массовый импорт карточек Redeye.
    """

    def __init__(
        self,
        *,
        delay_sec: float = 0.6,
        jitter_sec: float = 0.5,
        max_retries: int = 4,
        cooldown_sec: int = 90,
        stop_on_block: bool = False,
    ) -> None:
        self.delay_sec = delay_sec
        self.svc = RedeyeService(
            delay_sec=delay_sec,
            jitter_sec=jitter_sec,
            max_retries=max_retries,
            cooldown_sec=cooldown_sec,
            stop_on_block=stop_on_block,
        )

    def crawl_category(
        self,
        category_url: str,
        *,
        attach_genre: Optional[str] = None,
        attach_style: Optional[str] = None,
        limit: Optional[int] = None,
        save: bool = False,
    ) -> Iterable[ImportResult]:
        """
        Итератор по карточкам в категории: парсит payload и (опц.) сохраняет.

        Args:
            category_url: URL листинга Redeye.
            attach_genre: если раздел известен, доклеиваем жанр в payload.
            attach_style: аналогично — стиль раздела.
            limit: ограничение на число карточек из листинга.
            save: если True — делаем upsert в БД (Record + связанные сущности).

        Yields:
            ImportResult для каждой карточки (ok/error + краткая сводка).
        """
        for product_url in iterate_category_urls(
            category_url, delay_sec=self.delay_sec, limit=limit
        ):
            try:
                parsed = self.svc.parse_redeye_product_by_url(product_url)
                payload = parsed.payload or {}
                payload.setdefault("source", "redeye")
                payload.setdefault(
                    "source_url",
                    getattr(parsed, "source_url", None) or parsed.source_url,
                )
                if "has_audio_previews" not in payload:
                    payload["has_audio_previews"] = bool(
                        (parsed.payload or {}).get("has_audio_previews")
                    )

                # Приклеим жанр/стиль от раздела, если их нет в самой карточке
                if attach_genre:
                    payload.setdefault("genres", [])
                    if attach_genre not in payload["genres"]:
                        payload["genres"].append(attach_genre)
                if attach_style:
                    payload.setdefault("styles", [])
                    if attach_style not in payload["styles"]:
                        payload["styles"].append(attach_style)

                created = updated = False
                rec_id = None
                catno = (payload.get("catalog_number") or "").strip() or None
                title = (payload.get("title") or "").strip() or None

                if save:
                    with transaction.atomic():
                        rec = self._upsert_record_from_payload(payload)
                        rec_id = str(rec.pk)
                        created = getattr(rec, "_import_created", False)
                        updated = getattr(rec, "_import_updated", False)

                    self._ensure_redeye_record_source(rec, payload)

                # Сводка для CLI
                y, m, d = (
                    payload.get("release_year"),
                    payload.get("release_month"),
                    payload.get("release_day"),
                )
                if y and m and d:
                    rel = f"{y:04d}-{m:02d}-{d:02d}"
                elif y and m:
                    rel = f"{y:04d}-{m:02d}"
                elif y:
                    rel = f"{y:04d}"
                else:
                    rel = "-"

                summary = {
                    "title": title or "-",
                    "artists": ", ".join(payload.get("artists") or []) or "-",
                    "label": (payload.get("label") or "-").strip(),
                    "catalog_number": catno or "-",
                    "release": rel,
                    "availability": payload.get("availability") or "-",
                    "price": f"£{payload.get('price_gbp')}"
                    if payload.get("price_gbp") is not None
                    else "-",
                }

                yield ImportResult(
                    url=parsed.source_url,
                    ok=True,
                    created=created,
                    updated=updated,
                    record_id=rec_id,
                    catalog_number=catno,
                    title=title,
                    summary=summary,
                )
            except Exception as e:
                logger.exception("Failed to import %s", product_url)
                yield ImportResult(
                    url=product_url,
                    ok=False,
                    created=False,
                    updated=False,
                    error=str(e),
                )

    # -------- внутреннее сохранение --------
    def _upsert_record_from_payload(self, payload: dict) -> Record:
        """
        Upsert Record по catalog_number (fallback: title+label) + привязки.

        Здесь же:
          - genres/styles/artists (m2m) создаются/привязываются;
          - cover_image скачивается и прикрепляется;
          - ТРЕКИ ПЕРЕЗАПИСЫВАЮТСЯ через create_tracks_for_record(...).
        """
        title = (payload.get("title") or "").strip()
        label_name = (payload.get("label") or "").strip()
        catalog_number = (payload.get("catalog_number") or "").strip()
        country = (payload.get("country") or "") or None
        notes = payload.get("notes") or ""
        barcode = (payload.get("barcode") or "") or None

        # дата релиза (может быть частичной)
        ry = payload.get("release_year")
        rm = payload.get("release_month")
        rd = payload.get("release_day")

        # медиа и треки
        image_url: str | None = payload.get("image_url") or None
        tracks: List[dict] = payload.get("tracks") or []

        # m2m
        genres = [g for g in (payload.get("genres") or []) if g]
        styles = [s for s in (payload.get("styles") or []) if s]
        artist_names = [a for a in (payload.get("artists") or []) if a]

        # поиск существующей записи
        rec = None
        if catalog_number:
            rec = Record.objects.filter(catalog_number__iexact=catalog_number).first()

        if not rec and title and label_name:
            label = Label.objects.filter(name__iexact=label_name).first()
            if label:
                rec = Record.objects.filter(title__iexact=title, label=label).first()

        created = False
        if not rec:
            rec = Record(title=title)
            created = True

        # базовые поля
        if label_name:
            label_obj, _ = Label.objects.get_or_create(name=label_name)
            rec.label = label_obj
        if country:
            rec.country = country
        if catalog_number:
            rec.catalog_number = catalog_number
        if barcode:
            rec.barcode = barcode
        if notes:
            rec.notes = (rec.notes + "\n" + notes).strip() if rec.notes else notes

        if ry is not None:
            rec.release_year = ry
        if rm is not None:
            rec.release_month = rm
        if rd is not None:
            rec.release_day = rd

        rec.save()

        # M2M
        if genres:
            self._apply_vocab(rec, Genre, "genres", genres)
        if styles:
            self._apply_vocab(rec, Style, "styles", styles)

        if artist_names:
            artist_objs = []
            for name in artist_names:
                obj, _ = Artist.objects.get_or_create(name=name[:255])
                artist_objs.append(obj)
            rec.artists.set(artist_objs)

        # --- ТРЕКИ: единая точка записи — create_tracks_for_record ---
        if tracks:
            create_tracks_for_record(rec, tracks, replace=True)

        # Обложка (не валим транзакцию при ошибке)
        if image_url:
            try:
                self._download_and_attach_cover(rec, image_url)
            except Exception:
                logger.warning(
                    "Failed to fetch cover image: %s", image_url, exc_info=True
                )

        setattr(rec, "_import_created", created)
        setattr(rec, "_import_updated", not created)
        return rec

    def _download_and_attach_cover(self, rec: Record, image_url: str) -> None:
        """
        Качает картинку и сохраняет в cover_image с человекочитаемым именем.
        Пути upload_to уже настроены в модели (storage конфиг проекта).
        """
        if not image_url:
            return

        resp = requests.get(image_url, timeout=20)
        resp.raise_for_status()
        content = resp.content

        mime = resp.headers.get("Content-Type", "")
        ext = mimetypes.guess_extension(mime) or ".jpg"
        base = slugify(rec.title or rec.catalog_number or "cover") or "cover"
        filename = f"{base}{ext}"

        rec.cover_image.save(filename, ContentFile(content), save=True)


    def _ensure_redeye_record_source(self, rec: Record, payload: dict) -> None:
        """
        Создать/обновить RecordSource для Redeye product_page.
        Idempotent: если источник уже есть — только обновит поля.
        """
        url = (payload.get("source_url") or "").strip()
        if not url:
            logger.warning(
                "No source_url in payload for rec id=%s (catalog=%s) — skip RecordSource upsert",
                rec.id,
                rec.catalog_number,
            )
            return

        can_fetch = bool(payload.get("has_audio_previews"))
        try:
            service = RecordService(
                discogs_service=DiscogsService(), image_service=ImageService()
            )
            service._upsert_record_source(record=rec, provider=RecordSource.Provider.REDEYE,
                                          role=RecordSource.Role.PRODUCT_PAGE, url=url, can_fetch_audio=can_fetch)
            logger.debug(
                "RecordSource upserted: record=%s provider=redeye role=product_page can_fetch_audio=%s",
                rec.id,
                can_fetch,
            )
        except Exception:
            logger.warning(
                "Failed to upsert RecordSource for rec id=%s url=%s",
                rec.id,
                url,
                exc_info=True,
            )

    @staticmethod
    def _apply_vocab(
        rec: Record, model_cls, field_name: str, values: list[str]
    ) -> None:
        """
        Привязка m2m по списку строк (create-or-get) с дедупом и сохранением порядка.
        """
        seen = set()
        normalized: list[str] = []
        for v in values:
            key = v.strip()
            if not key:
                continue
            low = key.lower()
            if low in seen:
                continue
            seen.add(low)
            normalized.append(key)

        ids = []
        for name in normalized:
            obj, _ = model_cls.objects.get_or_create(name=name)
            ids.append(obj.id)

        getattr(rec, field_name).set(ids)
