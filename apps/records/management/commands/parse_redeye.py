# apps/records/management/commands/parse_redeye.py
from __future__ import annotations

import logging
from django.core.management.base import BaseCommand, CommandError
from ...pipelines.redeye_bulk_import import RedeyeBulkImporter
from ...rec_config.redeye import REDEYE_URLS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Парсит разделы Redeye Records, собирает карточки релизов "
        "и (опционально) сохраняет их в базу данных."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--category",
            choices=[c["code"] for c in REDEYE_URLS] + ["all"],
            default="all",
            help="Какую категорию парсить (по умолчанию: all).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Максимальное число карточек на категорию (0 = без ограничения).",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.8,
            help="Базовая задержка между запросами (сек.).",
        )
        parser.add_argument(
            "--jitter",
            type=float,
            default=0.5,
            help="Случайная прибавка к задержке (сек.).",
        )
        parser.add_argument(
            "--max-retries",
            type=int,
            default=5,
            help="Максимум повторов при сетевых ошибках.",
        )
        parser.add_argument(
            "--cooldown",
            type=int,
            default=120,
            help="Охлаждение (сек.) при блокировке (403/429).",
        )
        parser.add_argument(
            "--stop-on-block",
            action="store_true",
            help="Останавливать парсинг при повторной блокировке.",
        )
        parser.add_argument(
            "--save",
            action="store_true",
            help="Сохранять результаты в базу данных (иначе просто печать).",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Включить DEBUG-логирование (подробный вывод).",
        )

    def handle(self, *args, **options):
        if options["debug"]:
            logging.getLogger().setLevel(logging.DEBUG)

        importer = RedeyeBulkImporter(
            delay_sec=options["delay"],
            jitter_sec=options["jitter"],
            max_retries=options["max_retries"],
            cooldown_sec=options["cooldown"],
            stop_on_block=options["stop_on_block"],
        )

        cats = REDEYE_URLS
        if options["category"] != "all":
            code = options["category"]
            cats = [c for c in REDEYE_URLS if c["code"] == code]
            if not cats:
                raise CommandError(f"Неизвестный код категории: {code}")

        total_ok = total_err = total_created = total_updated = 0

        for cfg in cats:
            url = cfg["url"]
            genre = cfg.get("genre")
            style = cfg.get("style")

            self.stdout.write(
                self.style.MIGRATE_HEADING(f"Категория: {url} [{genre} / {style}]")
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

                    marker = "CREATED" if res.created else ("UPDATED" if res.updated else "OK")
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
