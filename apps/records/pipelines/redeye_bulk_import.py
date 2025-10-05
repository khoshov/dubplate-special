# apps/records/pipelines/redeye_bulk_import.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional, Dict, Any

from django.db import transaction

from records.scrapers.redeye_listing import iterate_category_urls
from records.services.redeye_service import RedeyeService
from records.models import Record, Genre, Style, Label, Track

import re
import mimetypes
import requests

from django.core.files.base import ContentFile
from django.utils.text import slugify

logger = logging.getLogger(__name__)

REDEYE_URLS = [
    {
        "url": "https://www.redeyerecords.co.uk/bass-music/pre-orders",
        "style": "Bass Music",
        "genre": "Electronic",
        "code": "bass",
    },
    {
        "url": "https://www.redeyerecords.co.uk/drum-and-bass/pre-orders",
        "style": "Drum n Bass",
        "genre": "Electronic",
        "code": "dnb",
    },
]


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
    summary: Optional[Dict[str, Any]] = None  # <- добавили: готовые поля для печати


class RedeyeBulkImporter:
    """
    Проходит по категории, собирает ссылки карточек, парсит и (опционально) сохраняет в БД.
    """

    def __init__(self, *, delay_sec: float = 0.6) -> None:
        self.delay_sec = delay_sec
        self.svc = RedeyeService()

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
        Обходит категорию и возвращает итератор результатов.
        Если save=True — пробует создать/обновить записи в БД.
        """
        for product_url in iterate_category_urls(category_url, delay_sec=self.delay_sec, limit=limit):
            try:
                parsed = self.svc.parse_product_by_url(product_url)
                payload = parsed.payload or {}
                payload.setdefault("source", "redeye")

                # аккуратно домерджим жанр/стиль от категории (если их нет в payload)
                if attach_genre:
                    payload.setdefault("genres", []).append(attach_genre)
                if attach_style:
                    payload.setdefault("styles", []).append(attach_style)

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

                # Сводка для команды (чтобы НЕ парсить второй раз)
                y, m, d = payload.get("release_year"), payload.get("release_month"), payload.get("release_day")
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
                    "price": f"£{payload.get('price_gbp')}" if payload.get("price_gbp") is not None else "-",
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
                yield ImportResult(url=product_url, ok=False, created=False, updated=False, error=str(e))

    # -------- внутреннее сохранение --------
    def _upsert_record_from_payload(self, payload: dict) -> Record:
        """
        Upsert записи по catalog_number (fallback: title+label).
        Заполняем базовые поля, дату релиза, цену, картинку, треклист, m2m (жанры/стили).
        """
        # базовые поля
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

        # цена
        # price_gbp = payload.get("price_gbp")

        # медиа и треки
        image_url = payload.get("image_url") or None
        track_lines = payload.get("tracks") or []

        # m2m
        genres = [g for g in (payload.get("genres") or []) if g]
        styles = [s for s in (payload.get("styles") or []) if s]
        # formats = [f for f in (payload.get("formats") or []) if f]
        # artists — опускаем, т.к. у нас отдельная модель (если есть) и логика маппинга может отличаться

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

        # заполняем основные поля
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

        # if price_gbp is not None:
        #     # payload может отдавать строку — конвертируем осторожно
        #     try:
        #         rec.price = float(price_gbp)
        #     except Exception:
        #         pass

        rec.save()

        # M2M
        if genres:
            self._apply_vocab(rec, Genre, "genres", genres)
        if styles:
            self._apply_vocab(rec, Style, "styles", styles)
        # formats: пропускаем, если нет отдельной модели

        # --- ARTISTS (M2M) ---
        artist_names = [a for a in (payload.get("artists") or []) if a]
        if artist_names:
            from records.models import Artist  # локальный импорт во избежание циклов
            artist_objs = []
            for name in artist_names:
                obj, _ = Artist.objects.get_or_create(name=name[:255])
                artist_objs.append(obj)
            rec.artists.set(artist_objs)

        # Треки: перезаписываем, если пришли
        track_lines = payload.get("tracks") or []
        if track_lines:
            Track.objects.filter(record=rec).delete()
            bulk = []
            for idx, item in enumerate(track_lines, start=1):
                # Поддержка двух форматов: dict {"position","title"} ИЛИ просто строка
                pos = None
                title_t = None
                if isinstance(item, dict):
                    pos = (item.get("position") or "").strip()
                    title_t = (item.get("title") or "").strip()
                else:
                    raw = (str(item) or "").strip()
                    if not raw:
                        continue
                    # Выделяем позицию (A1/B2/1/2) и заголовок из строки
                    m = re.match(r"^([A-D]\d{1,2}|[0-9]{1,2}\.?)[\s\-–—]+(.+)$", raw, flags=re.I)
                    if m:
                        pos = m.group(1).rstrip(".").upper()
                        title_t = m.group(2).strip()
                    else:
                        pos = ""
                        title_t = raw
                if not title_t:
                    continue
                # Fallback позиция, если пустая
                if not pos:
                    pos = f"{idx}"
                bulk.append(Track(record=rec, position=pos, title=title_t))
            if bulk:
                Track.objects.bulk_create(bulk, ignore_conflicts=True)

        # Обложка: качаем и сохраняем, если у записи нет cover_image или хотим обновить
        if image_url:
            try:
                self._download_and_attach_cover(rec, image_url)
            except Exception:
                # не валим транзакцию из-за картинки
                logger.warning("Failed to fetch cover image: %s", image_url, exc_info=True)

        # флаги для отчёта
        setattr(rec, "_import_created", created)
        setattr(rec, "_import_updated", not created)

        return rec

    def _download_and_attach_cover(self, rec: Record, image_url: str) -> None:
        """
        Качает картинку и сохраняет в cover_image с человекочитаемым именем.
        Пути upload_to уже настроены в проекте, просто сохраняем файл.
        """
        if not image_url:
            return

        # тянем контент
        resp = requests.get(image_url, timeout=20)
        resp.raise_for_status()
        content = resp.content

        # определяем имя файла (title.slug + расширение по mime)
        mime = resp.headers.get("Content-Type", "")
        ext = mimetypes.guess_extension(mime) or ".jpg"
        base = slugify(rec.title or rec.catalog_number or "cover") or "cover"
        filename = f"{base}{ext}"

        # сохраняем
        rec.cover_image.save(filename, ContentFile(content), save=True)

    @staticmethod
    def _apply_vocab(rec: Record, model_cls, field_name: str, values: list[str]) -> None:
        """
        Привязка m2m по списку строк с auto-create недостающих значений.
        """
        # нормализуем: без дублей, с сохранением порядка появления
        seen = set()
        normalized = []
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
