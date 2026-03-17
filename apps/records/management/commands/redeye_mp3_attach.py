"""
Массовая докачка mp3-превью для записей с источником Redeye.

Запуск (выберите один вариант):

Локально (bash):
  uv run manage.py redeye_mp3_attach \
    --limit 20 \
    --force

Локально (PowerShell):
  uv run manage.py redeye_mp3_attach `
    --limit 20 `
    --force

Docker (bash):
  docker compose exec django uv run manage.py redeye_mp3_attach \
    --limit 20 \
    --force

Docker (PowerShell):
  docker compose exec django uv run manage.py redeye_mp3_attach `
    --limit 20 `
    --force
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Iterable, Optional
from playwright.sync_api import sync_playwright, Browser
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from config.logging import build_log_extra, log_event
from records.models import Record, RecordSource, Track
from records.services.audio.audio_service import AudioService
from records.services.image.image_service import ImageService
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.providers.redeye.redeye_service import RedeyeService
from records.services.record_service import RecordService

logger = logging.getLogger(__name__)
_REDEYE_MP3_COMMAND_COMPONENT = "redeye_mp3_attach"


def _log_redeye_mp3_event(
    level: int,
    event: str,
    message: str,
    **context: object,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_REDEYE_MP3_COMMAND_COMPONENT,
        event=event,
        **context,
    )


class Command(BaseCommand):
    """
    Массовая докачка mp3-превью для существующих записей с источником Redeye.

    Логика:
      1) Формируем queryset записей:
         - по умолчанию: записи, у которых есть источник Redeye c role=product_page И can_fetch_audio=True,
           И у хотя бы одного трека нет локального превью (audio_preview пуст).
         - --all: игнорируем проверку на отсутствие превью (но источник/роль/can_fetch_audio всё равно обязательны).
         - --catalog: точечная обработка по одному каталожному номеру (перекрывает остальную фильтрацию).
      2) Для каждой записи вызываем RecordService.attach_audio_from_redeye(record, force=...).

    Важные флаги:
      --force         Перекачать превью даже если файлы уже есть.
      --dry-run       Показать, что будет сделано, без фактической загрузки.
      --limit/--offset/--order  Управление объёмом и порядком выборки.
      --delay/--jitter/--max-retries/--cooldown/--stop-on-block  Антиблок-поведение.
    """

    help = (
        "Массовая загрузка mp3-превью для записей с источником Redeye "
        "(role=product_page, can_fetch_audio=True)."
    )

    def add_arguments(self, parser) -> None:
        # Целевые записи
        parser.add_argument(
            "--all",
            action="store_true",
            help=(
                "Обрабатывать все записи Redeye (product_page, can_fetch_audio=True), "
                "даже если превью уже есть."
            ),
        )
        parser.add_argument(
            "--catalog",
            type=str,
            help="Обработать только один конкретный catalog_number (перекрывает прочие выборки).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Ограничить число записей для обработки.",
        )
        parser.add_argument(
            "--offset",
            type=int,
            default=0,
            help="Пропустить первые N записей выборки.",
        )
        parser.add_argument(
            "--order",
            choices=["asc", "desc"],
            default="asc",
            help="Порядок сортировки по ID записи (asc|desc).",
        )

        # Поведение загрузки
        parser.add_argument(
            "--force",
            action="store_true",
            help="ПЕРЕКАЧИВАТЬ mp3-превью, даже если они уже есть.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что будет сделано, без реальной загрузки.",
        )

        # Антиблок и стабильность
        parser.add_argument(
            "--delay",
            type=float,
            default=0.8,
            help="Базовая задержка между записями (сек).",
        )
        parser.add_argument(
            "--jitter",
            type=float,
            default=0.3,
            help="Случайная добавка к задержке (сек).",
        )
        parser.add_argument(
            "--max-retries",
            type=int,
            default=3,
            help="Максимальное число повторов для одной записи при ошибках.",
        )
        parser.add_argument(
            "--cooldown",
            type=float,
            default=60.0,
            help="Пауза (сек) после признаков блокировки (403/429).",
        )
        parser.add_argument(
            "--stop-on-block",
            action="store_true",
            help="Останавливать всю команду при повторной блокировке.",
        )
        parser.add_argument(
            "--diagnose",
            action="store_true",
            help="Вывести подробную диагностику выборки и причин отсеивания записей.",
        )

        parser.add_argument(
            "--debug",
            action="store_true",
            help="Включить подробное логирование.",
        )

    @staticmethod
    def _iter_queryset_in_order(
        qs, order: str, offset: int = 0, limit: Optional[int] = None
    ) -> Iterable[Record]:
        """Итерирует queryset с учётом сортировки/offset/limit."""
        qs = qs.order_by("id" if order == "asc" else "-id")
        if offset:
            qs = qs[offset:]
        if limit is not None:
            qs = qs[:limit]
        yield from qs.iterator(chunk_size=200)

    @staticmethod
    def _sleep_with_jitter(base: float, jitter: float) -> None:
        """Пауза между обработкой записей (anti-ban), добавляет случайный джиттер."""
        pause = max(0.0, base + random.uniform(0, jitter))
        time.sleep(pause)

    def handle(self, *args, **options) -> None:
        logging.getLogger().setLevel(
            logging.DEBUG if options["debug"] else logging.INFO
        )

        all_mode: bool = options["all"]
        catalog: Optional[str] = options["catalog"]
        limit: Optional[int] = options["limit"]
        offset: int = options["offset"]
        order: str = options["order"]

        force: bool = options["force"]
        dry_run: bool = options["dry_run"]

        delay: float = options["delay"]
        jitter: float = options["jitter"]
        max_retries: int = options["max_retries"]
        cooldown: float = options["cooldown"]
        stop_on_block: bool = options["stop_on_block"]

        _log_redeye_mp3_event(
            logging.INFO,
            "command_start",
            "Запуск команды redeye_mp3_attach.",
            timestamp=timezone.now().isoformat(),
            all_mode=all_mode,
            catalog=catalog or "—",
            limit=limit,
            offset=offset,
            order=order,
            force=force,
            dry_run=dry_run,
            delay=round(delay, 2),
            jitter=round(jitter, 2),
            max_retries=max_retries,
            cooldown=round(cooldown, 1),
            stop_on_block=stop_on_block,
        )

        if options["diagnose"]:
            self._log_selection_diagnostics()

        try:
            qs = self._build_queryset(all_mode=all_mode, catalog=catalog)
        except Exception as exc:
            raise CommandError(f"Не удалось построить queryset: {exc}") from exc

        total = qs.count()
        _log_redeye_mp3_event(
            logging.INFO,
            "selection_ready",
            "К обработке найдено записей.",
            total=total,
        )

        if dry_run:
            for r in self._iter_queryset_in_order(
                qs, order=order, offset=offset, limit=min(limit or 25, 50)
            ):
                _log_redeye_mp3_event(
                    logging.INFO,
                    "dry_run_item",
                    "DRY-RUN запись для обработки.",
                    record_id=r.id,
                    catalog_number=r.catalog_number or "—",
                    title=r.title or "—",
                )
            _log_redeye_mp3_event(
                logging.INFO,
                "dry_run_finish",
                "DRY-RUN завершён.",
            )
            return

        service = RecordService(
            discogs_service=DiscogsService(),
            redeye_service=RedeyeService(),
            image_service=ImageService(),
            audio_service=AudioService(),
        )

        processed = 0
        blocked_hits = 0

        # --- добавлено: временно разрешаем синхронный ORM при активном event loop ---
        prev_unsafe = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")  # --- добавлено ---
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "1"  # --- добавлено ---
        try:
            # --- ИЗМЕНЕНО: держим Playwright ОТКРЫТЫМ на ВЕСЬ цикл обработки ---
            with sync_playwright() as pw:  # --- изменено ---
                browser: Browser = pw.chromium.launch(
                    headless=True,
                    args=[
                        "--autoplay-policy=no-user-gesture-required",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--disable-background-timer-throttling",
                        "--disable-renderer-backgrounding",
                    ],
                )

                for (
                    record
                ) in self._iter_queryset_in_order(  # --- перемещено внутрь with ---
                    qs, order=order, offset=offset, limit=limit
                ):
                    processed += 1
                    self._sleep_with_jitter(delay, jitter)

                    for attempt in range(1, max_retries + 1):
                        try:
                            with transaction.atomic():
                                updated = service.attach_audio_from_redeye(
                                    record=record,
                                    force=force,
                                    per_click_timeout_sec=None,  # дефолт применит скрапер
                                    browser=browser,  # единый браузер на весь прогон
                                )
                            logger.info(
                                "Запись обработана.",
                                extra=build_log_extra(
                                    component=_REDEYE_MP3_COMMAND_COMPONENT,
                                    event="record_processed",
                                    record_id=record.id,
                                    catalog_number=record.catalog_number or "—",
                                    updated_count=updated,
                                    attempt=attempt,
                                    max_retries=max_retries,
                                ),
                            )
                            break

                        except Exception as exc:
                            msg = str(exc).lower()
                            is_block = any(
                                tok in msg
                                for tok in (
                                    " 403",
                                    " 429",
                                    "forbidden",
                                    "too many requests",
                                )
                            )
                            if is_block:
                                blocked_hits += 1
                                _log_redeye_mp3_event(
                                    logging.WARNING,
                                    "blocked_detected",
                                    "Обнаружены признаки блокировки Redeye.",
                                    record_id=record.id,
                                    catalog_number=record.catalog_number or "—",
                                    error=str(exc),
                                )
                                if stop_on_block and blocked_hits >= 2:
                                    _log_redeye_mp3_event(
                                        logging.ERROR,
                                        "blocked_stop",
                                        "Повторная блокировка. Остановка по флагу --stop-on-block.",
                                        record_id=record.id,
                                    )
                                    return
                                _log_redeye_mp3_event(
                                    logging.INFO,
                                    "cooldown_wait",
                                    "Ожидание после блокировки Redeye.",
                                    cooldown=round(cooldown, 1),
                                )
                                time.sleep(cooldown)
                                continue

                            logger.exception(
                                "Ошибка при обработке записи Redeye.",
                                extra=build_log_extra(
                                    component=_REDEYE_MP3_COMMAND_COMPONENT,
                                    event="record_failed",
                                    record_id=record.id,
                                    catalog_number=record.catalog_number or "—",
                                    attempt=attempt,
                                    max_retries=max_retries,
                                    error=str(exc),
                                ),
                            )
                            if attempt >= max_retries:
                                _log_redeye_mp3_event(
                                    logging.ERROR,
                                    "record_failed_max_retries",
                                    "Переход к следующей записи после неудачных попыток.",
                                    max_retries=max_retries,
                                    record_id=record.id,
                                )
        finally:
            # --- возвращаем переменную окружения в исходное состояние ---
            if prev_unsafe is None:
                os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
            else:
                os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = prev_unsafe

        _log_redeye_mp3_event(
            logging.INFO,
            "command_finish",
            "Команда redeye_mp3_attach завершена.",
            processed=processed,
        )

    def _build_queryset(self, all_mode: bool, catalog: Optional[str]):
        """
        Формирует выборку записей, для которых имеет смысл пытаться качать превью:
          - есть RecordSource(provider=REDEYE, role=PRODUCT_PAGE, can_fetch_audio=True);
          - если --all не передан, то у каких-то треков нет локальных превью.
        """
        has_redeye_source = RecordSource.objects.filter(
            record=OuterRef("pk"),
            provider=RecordSource.Provider.REDEYE,
            role=RecordSource.Role.PRODUCT_PAGE,
            can_fetch_audio=True,
        )
        qs = Record.objects.annotate(has_redeye=Exists(has_redeye_source)).filter(
            has_redeye=True
        )

        if catalog:
            return qs.filter(catalog_number=catalog)

        if not all_mode:
            missing_audio = Track.objects.filter(record=OuterRef("pk")).filter(
                Q(audio_preview__isnull=True) | Q(audio_preview__exact="")
            )
            qs = qs.annotate(missing_audio=Exists(missing_audio)).filter(
                missing_audio=True
            )

        return qs

    @staticmethod
    def _log_selection_diagnostics() -> None:
        """
        Печатает сводку по базе: сколько записей всего, сколько с источником Redeye,
        сколько с role=product_page, сколько с can_fetch_audio=True, сколько с отсутствующими превью и их пересечения.
        """
        total_records = Record.objects.count()
        total_sources = RecordSource.objects.count()
        _log_redeye_mp3_event(
            logging.DEBUG,
            "diag_totals",
            "DIAG: общее количество записей/источников.",
            total_records=total_records,
            total_sources=total_sources,
        )

        providers = list(
            RecordSource.objects.values_list("provider", flat=True).distinct()
        )
        roles = list(RecordSource.objects.values_list("role", flat=True).distinct())
        _log_redeye_mp3_event(
            logging.DEBUG,
            "diag_distinct",
            "DIAG: уникальные значения provider/role.",
            providers=providers,
            roles=roles,
        )

        role_value = getattr(RecordSource.Role, "PRODUCT_PAGE", "product_page")

        recs_with_redeye = (
            RecordSource.objects.filter(provider__iexact="redeye")
            .values("record_id")
            .distinct()
        )
        n_with_redeye = recs_with_redeye.count()
        _log_redeye_mp3_event(
            logging.DEBUG,
            "diag_redeye_sources",
            "DIAG: записей с provider=redeye.",
            total=n_with_redeye,
        )

        recs_with_redeye_pp = (
            RecordSource.objects.filter(provider__iexact="redeye", role=role_value)
            .values("record_id")
            .distinct()
        )
        n_with_redeye_pp = recs_with_redeye_pp.count()
        _log_redeye_mp3_event(
            logging.DEBUG,
            "diag_redeye_product_page",
            "DIAG: записей с role=product_page.",
            total=n_with_redeye_pp,
        )

        recs_with_redeye_pp_can = (
            RecordSource.objects.filter(
                provider__iexact="redeye", role=role_value, can_fetch_audio=True
            )
            .values("record_id")
            .distinct()
        )
        n_with_redeye_pp_can = recs_with_redeye_pp_can.count()
        _log_redeye_mp3_event(
            logging.DEBUG,
            "diag_redeye_can_fetch_audio",
            "DIAG: записей с can_fetch_audio=True.",
            total=n_with_redeye_pp_can,
        )

        missing_audio_q = Track.objects.filter(record=OuterRef("pk")).filter(
            Q(audio_preview__isnull=True) | Q(audio_preview__exact="")
        )
        recs_missing_qs = Record.objects.annotate(
            missing=Exists(missing_audio_q)
        ).filter(missing=True)
        n_missing_audio = recs_missing_qs.count()
        _log_redeye_mp3_event(
            logging.DEBUG,
            "diag_missing_audio",
            "DIAG: записей без превью у треков.",
            total=n_missing_audio,
        )

        n_candidates_default = recs_missing_qs.filter(
            id__in=recs_with_redeye_pp_can
        ).count()
        _log_redeye_mp3_event(
            logging.DEBUG,
            "diag_candidates_default",
            "DIAG: кандидатов по дефолтной логике.",
            total=n_candidates_default,
        )

        if n_candidates_default <= 10:
            ids_default = list(
                recs_missing_qs.filter(id__in=recs_with_redeye_pp_can).values_list(
                    "id", flat=True
                )
            )
            _log_redeye_mp3_event(
                logging.DEBUG,
                "diag_candidates_ids",
                "DIAG: candidate IDs.",
                ids=ids_default,
            )

        almost_ids = list(
            Record.objects.filter(id__in=recs_with_redeye_pp)
            .exclude(id__in=recs_with_redeye_pp_can)
            .values_list("id", flat=True)
        )
        if almost_ids:
            _log_redeye_mp3_event(
                logging.DEBUG,
                "diag_candidates_almost",
                "DIAG: записи с role=product_page, но can_fetch_audio=False.",
                ids=almost_ids,
            )

        if total_records <= 50:
            for r in Record.objects.filter(id__in=recs_with_redeye_pp_can).order_by(
                "id"
            ):
                totals = Track.objects.filter(record=r).aggregate(
                    total=Count("id"),
                    have=Count(
                        "id",
                        filter=~Q(audio_preview__isnull=True)
                        & ~Q(audio_preview__exact=""),
                    ),
                    miss=Count(
                        "id",
                        filter=Q(audio_preview__isnull=True)
                        | Q(audio_preview__exact=""),
                    ),
                )
                _log_redeye_mp3_event(
                    logging.DEBUG,
                    "diag_tracks",
                    "DIAG: статистика треков записи.",
                    record_id=r.id,
                    catalog_number=r.catalog_number or "—",
                    total=totals["total"],
                    have=totals["have"],
                    miss=totals["miss"],
                )

            for r in Record.objects.filter(id__in=recs_with_redeye).order_by("id"):
                srcs = list(
                    RecordSource.objects.filter(record=r).values_list(
                        "provider", "role", "can_fetch_audio", "url"
                    )
                )
                _log_redeye_mp3_event(
                    logging.DEBUG,
                    "diag_sources",
                    "DIAG: источники записи.",
                    record_id=r.id,
                    catalog_number=r.catalog_number or "—",
                    sources=srcs,
                )
