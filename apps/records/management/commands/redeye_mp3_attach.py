# apps/records/management/commands/redeye_mp3_attach.py
"""
docker compose exec django uv run python manage.py redeye_mp3_attach `
  --limit 20 `
  --force
"""

from __future__ import annotations

import logging
import random
import time
from typing import Iterable, Optional
from django.db.models import Exists, OuterRef, Q, Count  # --- добавлено Count ---

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

# относительные импорты — как просил
from ...models import Record, Track, RecordSource
from ...services.record_service import RecordService
from ...services.discogs_service import DiscogsService
from ...services.image_service import ImageService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Массовая докачка mp3-превью для существующих записей с источником Redeye.

    Логика:
      1) Формируем queryset записей:
         - по умолчанию: записи, у которых есть источник Redeye c role=product_page И can_fetch_audio=True,
           И у хотя бы одного трека нет локального превью (audio_preview пуст).
         - --all: игнорируем проверку на отсутствие превью (но источник/роль/can_fetch_audio всё равно обязательны).
         - --catalog: точечная обработка по одному каталогу (перекрывает остальную фильтрацию).
      2) Для каждой записи вызываем RecordService._maybe_attach_redeye_previews(record, force=...).

    Важные флаги:
      --force         Перекачать превью даже если файлы уже есть.
      --dry-run       Показать, что будет сделано, без фактической загрузки.
      --limit/--offset/--order  Управление объёмом и порядком выборки.
      --delay/--jitter/--max-retries/--cooldown/--stop-on-block  Антиблок-поведение.
    """

    help = "Массовая загрузка mp3-превью для записей с источником Redeye (product_page, can_fetch_audio=True)."

    def add_arguments(self, parser):
        # Целевые записи
        parser.add_argument("--all", action="store_true",
                            help="Обрабатывать все записи Redeye (product_page, can_fetch_audio=True), "
                                 "даже если превью уже есть.")
        parser.add_argument("--catalog", type=str,
                            help="Обработать только один конкретный catalog_number (перекрывает прочие выборки).")
        parser.add_argument("--limit", type=int, default=None,
                            help="Ограничить число записей для обработки.")
        parser.add_argument("--offset", type=int, default=0,
                            help="Пропустить первые N записей выборки.")
        parser.add_argument("--order", choices=["asc", "desc"], default="asc",
                            help="Порядок сортировки по ID записи (asc|desc).")

        # Поведение загрузки
        parser.add_argument("--force", action="store_true",
                            help="ПЕРЕКАЧИВАТЬ mp3-превью, даже если они уже есть.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Только показать, что будет сделано, без реальной загрузки.")

        # Антиблок и стабильность
        parser.add_argument("--delay", type=float, default=0.8,
                            help="Базовая задержка между записями (сек).")
        parser.add_argument("--jitter", type=float, default=0.3,
                            help="Случайная добавка к задержке (сек).")
        parser.add_argument("--max-retries", type=int, default=3,
                            help="Максимальное число повторов для одной записи при ошибках.")
        parser.add_argument("--cooldown", type=float, default=60.0,
                            help="Пауза (сек) после признаков блокировки (403/429).")
        parser.add_argument("--stop-on-block", action="store_true",
                            help="Останавливать всю команду при повторной блокировке.")
        parser.add_argument(
            "--diagnose",
            action="store_true",
            help="Вывести подробную диагностику выборки и причин отсеивания записей."
        )
        # Логгирование
        parser.add_argument("--debug", action="store_true",
                            help="Включить подробное логирование.")

    # --- вспомогательные ---------------------------------------------------------
    def _iter_queryset_in_order(
        self, qs, order: str, offset: int = 0, limit: Optional[int] = None
    ) -> Iterable[Record]:
        qs = qs.order_by("id" if order == "asc" else "-id")
        if offset:
            qs = qs[offset:]
        if limit is not None:
            qs = qs[:limit]
        yield from qs.iterator(chunk_size=200)

    def _sleep_with_jitter(self, base: float, jitter: float) -> None:
        pause = max(0.0, base + random.uniform(0, jitter))
        time.sleep(pause)

    # --- основная логика ---------------------------------------------------------
    def handle(self, *args, **options):
        logging.getLogger().setLevel(logging.DEBUG if options["debug"] else logging.INFO)

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

        logger.info("Запуск redeye_mp3_attach | now=%s", timezone.now().isoformat())
        logger.info(
            "Параметры: all=%s, catalog=%s, limit=%s, offset=%s, order=%s, force=%s, dry_run=%s, "
            "delay=%.2f, jitter=%.2f, max_retries=%d, cooldown=%.1f, stop_on_block=%s",
            all_mode, catalog, limit, offset, order, force, dry_run,
            delay, jitter, max_retries, cooldown, stop_on_block,
        )
        if options["diagnose"]:
            self._log_selection_diagnostics()
        try:
            qs = self._build_queryset(all_mode=all_mode, catalog=catalog)
        except Exception as exc:
            raise CommandError(f"Не удалось построить queryset: {exc}") from exc

        total = qs.count()
        logger.info("К обработке найдено записей: %d", total)

        if dry_run:
            for r in self._iter_queryset_in_order(qs, order=order, offset=offset, limit=min(limit or 25, 50)):
                logger.info("[DRY-RUN] id=%s catalog=%s title=%s", r.id, r.catalog_number, r.title)
            logger.info("DRY-RUN завершён.")
            return

        service = RecordService(discogs_service=DiscogsService(), image_service=ImageService())

        processed = 0
        blocked_hits = 0

        for record in self._iter_queryset_in_order(qs, order=order, offset=offset, limit=limit):
            processed += 1
            self._sleep_with_jitter(delay, jitter)

            for attempt in range(1, max_retries + 1):
                try:
                    # --- главное действие ---
                    updated = service._maybe_attach_redeye_previews(record, force=force)  # noqa: SLF001
                    logger.info(
                        "OK  id=%s catalog=%s: обновлено треков=%s (attempt %d/%d)",
                        record.id, record.catalog_number, updated, attempt, max_retries
                    )
                    break

                except Exception as exc:
                    # простая эвристика «похоже на блокировку»
                    msg = str(exc).lower()
                    is_block = any(tok in msg for tok in (" 403", " 429", "forbidden", "too many requests"))
                    if is_block:
                        blocked_hits += 1
                        logger.warning("BLOCK id=%s catalog=%s: %s", record.id, record.catalog_number, exc)
                        if stop_on_block and blocked_hits >= 2:
                            logger.error("Повторная блокировка. Останавливаю по флагу --stop-on-block.")
                            return
                        logger.info("Ожидание %.1f сек...", cooldown)
                        time.sleep(cooldown)
                        continue

                    logger.exception(
                        "ERR id=%s catalog=%s (attempt %d/%d): %s",
                        record.id, record.catalog_number, attempt, max_retries, exc
                    )
                    if attempt >= max_retries:
                        logger.error("Переход к следующей записи после %d неудачных попыток.", max_retries)

        logger.info("Готово. Обработано записей: %d", processed)

    # --- построение выборки ------------------------------------------------------
    def _build_queryset(self, all_mode: bool, catalog: Optional[str]):
        """
        Выборка записей, для которых реально есть смысл пытаться качать превью:
          - есть RecordSource(provider=REDEYE, role=PRODUCT_PAGE, can_fetch_audio=True);
          - если --all не передан, то у каких-то треков нет локальных превью.
        """
        has_redeye_source = RecordSource.objects.filter(
            record=OuterRef("pk"),
            provider=RecordSource.Provider.REDEYE,
            role=RecordSource.Role.PRODUCT_PAGE,
            can_fetch_audio=True,
        )
        qs = Record.objects.annotate(has_redeye=Exists(has_redeye_source)).filter(has_redeye=True)

        if catalog:
            return qs.filter(catalog_number=catalog)

        if not all_mode:
            missing_audio = Track.objects.filter(record=OuterRef("pk")).filter(
                Q(audio_preview__isnull=True) | Q(audio_preview__exact="")
            )
            qs = qs.annotate(missing_audio=Exists(missing_audio)).filter(missing_audio=True)

        return qs

    def _log_selection_diagnostics(self) -> None:
        """
        Печатает сводку по базе: сколько записей всего, сколько с источником Redeye,
        сколько с role=product_page, сколько с can_fetch_audio=True, сколько с отсутствующими превью и их пересечения.
        """
        # 1) базовые числа
        total_records = Record.objects.count()
        total_sources = RecordSource.objects.count()
        logger.info("[DIAG] всего Record: %d, всего RecordSource: %d", total_records, total_sources)

        # 2) провайдеры/роли, как они реально хранятся
        providers = list(RecordSource.objects.values_list("provider", flat=True).distinct())
        roles = list(RecordSource.objects.values_list("role", flat=True).distinct())
        logger.info("[DIAG] distinct provider values: %s", providers)
        logger.info("[DIAG] distinct role values: %s", roles)

        # 3) значения для фильтра (устойчиво к enum/строке)
        role_value = getattr(RecordSource.Role, "PRODUCT_PAGE", "product_page")

        # 4) посчитаем по шагам
        recs_with_redeye = RecordSource.objects.filter(provider__iexact="redeye") \
            .values("record_id").distinct()
        n_with_redeye = recs_with_redeye.count()
        logger.info("[DIAG] записей с provider=redeye: %d", n_with_redeye)

        recs_with_redeye_pp = RecordSource.objects.filter(
            provider__iexact="redeye", role=role_value
        ).values("record_id").distinct()
        n_with_redeye_pp = recs_with_redeye_pp.count()
        logger.info("[DIAG] из них с role=product_page: %d", n_with_redeye_pp)

        recs_with_redeye_pp_can = RecordSource.objects.filter(
            provider__iexact="redeye", role=role_value, can_fetch_audio=True
        ).values("record_id").distinct()
        n_with_redeye_pp_can = recs_with_redeye_pp_can.count()
        logger.info("[DIAG] из них с can_fetch_audio=True: %d", n_with_redeye_pp_can)

        # 5) у каких записей вообще есть «дыры» по превью
        missing_audio_q = Track.objects.filter(record=OuterRef("pk")).filter(
            Q(audio_preview__isnull=True) | Q(audio_preview__exact="")
        )
        recs_missing_qs = Record.objects.annotate(missing=Exists(missing_audio_q)).filter(missing=True)
        n_missing_audio = recs_missing_qs.count()
        logger.info("[DIAG] записей, где у треков отсутствуют превью: %d", n_missing_audio)

        # 6) пересечение «можно качать» и «не хватает превью» (это дефолтная выборка без --all)
        n_candidates_default = recs_missing_qs.filter(id__in=recs_with_redeye_pp_can).count()
        logger.info("[DIAG] кандидатов в дефолтной логике (product_page + can_fetch_audio + отсутствуют превью): %d",
                    n_candidates_default)

        # 7) покажем короткие списки ID, если всё ещё мало
        if n_candidates_default <= 10:
            ids_default = list(recs_missing_qs.filter(id__in=recs_with_redeye_pp_can)
                               .values_list("id", flat=True))
            logger.info("[DIAG] candidate IDs: %s", ids_default)

        # 8) диагностируем те, кто «не попал», но почти подходит
        almost_ids = list(
            Record.objects.filter(id__in=recs_with_redeye_pp)  # есть product_page
            .exclude(id__in=recs_with_redeye_pp_can)  # но can_fetch_audio=False
            .values_list("id", flat=True)
        )
        if almost_ids:
            logger.info("[DIAG] есть записи с role=product_page, но can_fetch_audio=False, ids=%s", almost_ids)

        # 9) выведем пометку по трекам для маленьких наборов
        if total_records <= 50:
            for r in Record.objects.filter(id__in=recs_with_redeye_pp_can).order_by("id"):
                totals = Track.objects.filter(record=r).aggregate(
                    total=Count("id"),
                    have=Count("id", filter=~Q(audio_preview__isnull=True) & ~Q(audio_preview__exact="")),
                    miss=Count("id", filter=Q(audio_preview__isnull=True) | Q(audio_preview__exact="")),
                )
                logger.info("[DIAG] rec id=%s cat=%s | tracks total=%s, have=%s, miss=%s",
                            r.id, r.catalog_number, totals["total"], totals["have"], totals["miss"])

            # и покажем сводку источников, чтобы увидеть реальное значение provider/role/url
            for r in Record.objects.filter(id__in=recs_with_redeye).order_by("id"):
                srcs = list(RecordSource.objects
                            .filter(record=r)
                            .values_list("provider", "role", "can_fetch_audio", "url"))
                logger.info("[DIAG] rec id=%s cat=%s | sources=%s", r.id, r.catalog_number, srcs)
