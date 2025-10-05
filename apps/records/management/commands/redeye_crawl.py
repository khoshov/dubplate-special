# apps/records/management/commands/redeye_crawl.py
from __future__ import annotations

import logging
from django.core.management.base import BaseCommand, CommandError
from records.pipelines.redeye_bulk_import import RedeyeBulkImporter, REDEYE_URLS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Crawl Redeye categories, parse product pages, and (optionally) save to DB."

    def add_arguments(self, parser):
        parser.add_argument(
            "--category",
            choices=[c["code"] for c in REDEYE_URLS] + ["all"],
            default="all",
            help="Which category to crawl (default: all).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Max number of product URLs to crawl per category."
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=0.6,
            help="Delay (seconds) between listing pages."
        )
        parser.add_argument(
            "--save",
            action="store_true",
            help="Save results to DB (otherwise just print parsed info)."
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable DEBUG logging for this run."
        )

    def handle(self, *args, **options):
        if options["debug"]:
            logging.getLogger().setLevel(logging.DEBUG)

        importer = RedeyeBulkImporter(delay_sec=options["delay"])

        cats = REDEYE_URLS
        if options["category"] != "all":
            code = options["category"]
            cats = [c for c in REDEYE_URLS if c["code"] == code]
            if not cats:
                raise CommandError(f"Unknown category code: {code}")

        total_ok = total_err = total_created = total_updated = 0

        for cfg in cats:
            url = cfg["url"]
            genre = cfg.get("genre")
            style = cfg.get("style")

            self.stdout.write(self.style.MIGRATE_HEADING(f"Category: {url} [{genre} / {style}]"))

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
                    self.stdout.write(self.style.WARNING(f"[ERROR] {res.url} :: {res.error}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. OK={total_ok}, ERRORS={total_err}, CREATED={total_created}, UPDATED={total_updated}"
            )
        )
