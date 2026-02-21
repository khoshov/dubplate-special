"""
Management-команда: парсинг разделов Redeye и (опционально) сохранение в БД.

Поток данных:
  CLI → RedeyeBulkImporter.crawl_category(...) → RedeyeService.parse_product_by_url(...)
     → payload  → _upsert_record_from_payload(save=True)
     → create_tracks_for_record(...)

Запуск (выберите один вариант):

Локально (bash):
  uv run manage.py parse_redeye \
    --category all \
    --limit 2 \
    --save

Локально (PowerShell):
  uv run manage.py parse_redeye `
    --category all `
    --limit 2 `
    --save

Docker (bash):
  docker compose exec django uv run manage.py parse_redeye \
    --category all \
    --limit 2 \
    --save

Docker (PowerShell):
  docker compose exec django uv run manage.py parse_redeye `
    --category all `
    --limit 2 `
    --save

"""

from __future__ import annotations

import logging
import re
from typing import List

from django.core.management.base import BaseCommand, CommandError

from records.constants import REDEYE_URLS
from records.pipelines.redeye.bulk_import_from_redeye import RedeyeBulkImporter

logger = logging.getLogger(__name__)


def _derive_code(url: str | None, genre: str | None, style: str | None) -> str:
    """
    Производный «короткий код» категории для CLI, если в конфиге нет поля 'code'.
    Берём часть пути URL после домена и заменяем разделители на '-'.
    Фолбэк — склейка genre/style.
    """
    if url:
        # выцепим "drum-and-bass/pre-orders" → "drum-and-bass-pre-orders"
        m = re.search(r"redeyerecords\.co\.uk/(.+)$", url)
        if m:
            return re.sub(r"[^a-z0-9]+", "-", m.group(1).lower()).strip("-")
    parts = [p for p in [genre, style] if p]
    return re.sub(r"[^a-z0-9]+", "-", "-".join(parts).lower()).strip("-") or "category"


class Command(BaseCommand):
    help = (
        "Парсит разделы Redeye Records, собирает карточки релизов "
        "и (опционально) сохраняет их в базу данных."
    )

    def add_arguments(self, parser):
        codes: List[str] = []
        for c in REDEYE_URLS:
            code = c.get("code") or _derive_code(
                c.get("url"), c.get("genre"), c.get("style")
            )
            codes.append(code)

        parser.add_argument(
            "--category",
            choices=sorted(set(codes + ["all"])),
            default="all",
            help="Какую категорию парсить (по умолчанию: all).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Максимум карточек на категорию (0/None = без ограничения).",
        )
        parser.add_argument(
            "--save",
            action="store_true",
            help="Сохранять результаты в базу данных (иначе печать payload summary).",
        )

    def handle(self, *args, **options):
        importer = RedeyeBulkImporter()

        category_code = options["category"]
        selected = []
        for cfg in REDEYE_URLS:
            code = cfg.get("code") or _derive_code(
                cfg.get("url"), cfg.get("genre"), cfg.get("style")
            )
            if category_code == "all" or code == category_code:
                selected.append(cfg)

        if not selected:
            raise CommandError(f"Не найдена категория: {category_code}")

        total_ok = total_err = total_created = total_updated = 0

        for cfg in selected:
            url = cfg["url"]
            genre = cfg.get("genre")
            style = cfg.get("style")
            code = cfg.get("code") or _derive_code(url, genre, style)

            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"[{code}] {url} [{genre or '-'} / {style or '-'}]"
                )
            )

            for res in importer.crawl_category(
                url,
                attach_genre=genre,
                attach_style=style,
                limit=options["limit"],
                save=options["save"],
            ):
                if res.ok:
                    total_ok += 1
                    if res.created:
                        total_created += 1
                    elif res.updated:
                        total_updated += 1

                    marker = (
                        "CREATED"
                        if res.created
                        else ("UPDATED" if res.updated else "OK")
                    )
                    s = res.summary or {}
                    self.stdout.write(
                        f"[{marker}] {res.url}\n"
                        f"      title: {s.get('title', '-')}\n"
                        f"     artists: {s.get('artists', '-')}\n"
                        f"       label: {s.get('label', '-')}\n"
                        f"   catnumber: {s.get('catalog_number', '-')}\n"
                        f" release date: {s.get('release', '-')}\n"
                        f" availability: {s.get('availability', '-')}\n"
                        f"       price: {s.get('price', '-')}\n"
                    )
                else:
                    total_err += 1
                    self.stdout.write(
                        self.style.WARNING(f"[ERROR] {res.url} :: {res.error}")
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Успешно: {total_ok}, Ошибок: {total_err}, "
                f"Создано: {total_created}, Обновлено: {total_updated}"
            )
        )
