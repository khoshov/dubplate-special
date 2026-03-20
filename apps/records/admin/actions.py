from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from django.contrib import admin, messages
from django.db.models import QuerySet, Q
from django.http import HttpRequest, HttpResponseRedirect
from django.utils.text import Truncator
from django.utils import timezone
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from config.logging import NOTICE_LEVEL, log_event

from records.models import Record, VKPublicationLog
from records.services.social.publication_log import register_vk_publication_event

if TYPE_CHECKING:
    from .record_admin import RecordAdmin

logger = logging.getLogger(__name__)
_ADMIN_ACTIONS_COMPONENT = "действия админки"


def _log_admin_action_event(
    level: int,
    event: str,
    message: str,
    **context,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_ADMIN_ACTIONS_COMPONENT,
        event=event,
        **context,
    )


def _batch_update(
    admin_obj: RecordAdmin,
    request: HttpRequest,
    queryset: QuerySet[Record],
    *,
    start_log: str,
    empty_msg: str,
    ok_msg: str,
    skip_msg: str,
    skip_header: str,
    fail_msg: str,
    fail_header: str,
    id_label: str,
    get_id: Callable[[Record], str | None],
    do_update: Callable[[Record], object],
    on_success: Callable[[Record, object], None] | None = None,
    on_error: Callable[[Record, Exception], None] | None = None,
    format_failed_item: Callable[[Record, str | None, Exception], str] | None = None,
    show_fail_summary: bool = True,
    expected_errors: tuple[type[Exception], ...] = (),
) -> None:
    """Универсальный исполнитель массового обновления."""
    start_ts = time.perf_counter()
    total = queryset.count()
    user = getattr(request, "user", None)
    username = getattr(user, "username", "unknown")

    _log_admin_action_event(
        logging.INFO,
        "batch_start",
        f"===== {start_log} =====",
        action=start_log,
        records_total=total,
        username=username,
    )

    if total == 0:
        admin_obj.message_user(request, empty_msg, level=messages.WARNING)
        return

    ok = skip = fail = 0
    skipped, failed = [], []

    for record in queryset:
        log_record_name = f"#{record.pk} «{record}»"
        extract_id = get_id(record)
        if not extract_id:
            skip += 1
            skipped.append(f"{log_record_name}: нет {id_label}")
            continue
        try:
            result = do_update(record)
            if on_success is not None:
                on_success(record, result)
            ok += 1
        except Exception as e:
            fail += 1
            if format_failed_item is not None:
                failed.append(format_failed_item(record, extract_id, e))
            else:
                failed.append(f"{log_record_name}: {e!s}")
            if on_error is not None:
                on_error(record, e)
            if expected_errors and isinstance(e, expected_errors):
                _log_admin_action_event(
                    NOTICE_LEVEL,
                    "batch_item_failed_expected",
                    "Операция завершилась ожидаемой ошибкой.",
                    action=start_log,
                    record_id=record.pk,
                    item_id=extract_id,
                    id_label=id_label,
                    error=str(e),
                )
            else:
                _log_admin_action_event(
                    logging.ERROR,
                    "batch_item_failed",
                    "Операция завершилась ошибкой.",
                    action=start_log,
                    record_id=record.pk,
                    item_id=extract_id,
                    id_label=id_label,
                    error=str(e),
                )
                logger.exception("Детали ошибки batch action.")

    if ok:
        admin_obj.message_user(
            request, ok_msg.format(ok=ok, total=total), level=messages.SUCCESS
        )
    if skip:
        admin_obj.message_user(request, skip_msg.format(n=skip), level=messages.WARNING)
        admin_obj.message_user(
            request, skip_header + "\n• " + "\n• ".join(skipped), level=messages.INFO
        )
    if fail:
        if show_fail_summary:
            admin_obj.message_user(
                request, fail_msg.format(n=fail), level=messages.ERROR
            )
        failed_lines_html = format_html_join(
            "",
            "<br>&nbsp;&nbsp;&bull; {}",
            ((item,) for item in failed),
        )
        admin_obj.message_user(
            request,
            format_html("{}{}", fail_header, failed_lines_html),
            level=messages.ERROR,
        )

    _log_admin_action_event(
        logging.INFO,
        "batch_finish",
        f"===== {start_log} завершено =====",
        action=start_log,
        ok=ok,
        skipped=skip,
        failed=fail,
        records_total=total,
        elapsed_sec=f"{time.perf_counter() - start_ts:.2f}",
        username=username,
    )


@admin.action(description="Опубликовать в VK")
def post_to_vk(
    admin_obj: RecordAdmin, request: HttpRequest, queryset: QuerySet[Record]
) -> None:
    """
    Публикует выбранные записи в сообщество ВКонтакте со всеми аудио-треками.
    """
    vk_service = getattr(admin_obj, "vk_service", None)
    if vk_service is None:
        admin_obj.message_user(
            request,
            "Сервис VK не сконфигурирован. Обратитесь к администратору.",
            level=messages.ERROR,
        )
        _log_admin_action_event(
            logging.ERROR,
            "vk_service_missing",
            "Публикация в VK не запущена: сервис VK не инициализирован.",
        )
        return

    _log_admin_action_event(
        logging.INFO,
        "vk_publish_start",
        "Запущена публикация записей в VK.",
        records_total=queryset.count(),
        username=getattr(request.user, "username", "?"),
    )

    def _get_id(record: Record) -> str | None:
        return str(getattr(record, "pk", None))

    def _do_post(record: Record) -> int:
        title = getattr(record, "title", "")
        cover = getattr(record, "cover_image", None)
        cover_path = getattr(cover, "path", None) if cover else None
        _log_admin_action_event(
            logging.DEBUG,
            "vk_publish_attempt",
            "Подготовлена публикация записи в VK.",
            record_id=getattr(record, "pk", None),
            title=Truncator(title).chars(80),
            has_cover=bool(cover_path),
            cover_path=cover_path or "—",
        )

        post_id = vk_service.post_record_with_audio(record=record)
        _log_admin_action_event(
            logging.INFO,
            "vk_publish_success",
            "Запись опубликована в VK.",
            record_id=getattr(record, "pk", None),
            post_id=post_id,
            title=Truncator(title).chars(80),
        )
        return post_id

    def _on_success(record: Record, result: object) -> None:
        post_id = int(result)
        register_vk_publication_event(
            record=record,
            mode=VKPublicationLog.Mode.IMMEDIATE,
            status=VKPublicationLog.Status.SUCCESS,
            effective_publish_at=timezone.now(),
            vk_post_id=post_id,
        )

    def _on_error(record: Record, error: Exception) -> None:
        register_vk_publication_event(
            record=record,
            mode=VKPublicationLog.Mode.IMMEDIATE,
            status=VKPublicationLog.Status.FAILED,
            error_message=str(error),
        )

    _batch_update(
        admin_obj,
        request,
        queryset,
        start_log="Публикация записей в VK",
        empty_msg="Выберите записи для публикации в VK.",
        ok_msg="Опубликовано в VK: {ok} из {total}.",
        skip_msg="Пропущено (не выбрано): {n}.",
        skip_header="Пропущено:",
        fail_msg="С ошибками публикации: {n}.",
        fail_header="Ошибки:",
        id_label="record_id",
        get_id=_get_id,
        do_update=_do_post,
        on_success=_on_success,
        on_error=_on_error,
    )


@admin.action(description="Запланировать публикацию в VK")
def schedule_to_vk(
    admin_obj: RecordAdmin, request: HttpRequest, queryset: QuerySet[Record]
) -> HttpResponseRedirect | None:
    """
    Перенаправляет на форму выбора интервала для равномерной публикации.
    """
    total = queryset.count()
    if total == 0:
        admin_obj.message_user(
            request,
            "Выберите записи для планирования публикации.",
            level=messages.WARNING,
        )
        return None

    ids = list(queryset.values_list("pk", flat=True))
    url = reverse("admin:records_record_vk_schedule")
    return HttpResponseRedirect(f"{url}?ids={','.join(str(i) for i in ids)}")


@admin.action(description="Обновить из Discogs")
def update_from_discogs(
    admin_obj: RecordAdmin, request: HttpRequest, queryset: QuerySet[Record]
) -> None:
    record_service = admin_obj.record_service
    _batch_update(
        admin_obj,
        request,
        queryset,
        start_log="Обновление из Discogs",
        empty_msg="Выберите записи для обновления из Discogs.",
        ok_msg="Обновлено из Discogs: {ok} из {total}.",
        skip_msg="Пропущено (нет Discogs ID): {n}.",
        skip_header="Пропущено:",
        fail_msg="С ошибками: {n}.",
        fail_header="Ошибки:",
        id_label="Discogs ID",
        get_id=lambda record: record.discogs_id,
        do_update=lambda record: record_service.update_from_discogs(record=record),
    )


@admin.action(description="Обновить аудио треков из YouTube")
def update_audio_from_youtube(
    admin_obj: RecordAdmin, request: HttpRequest, queryset: QuerySet[Record]
) -> None:
    """Ставит массовую YouTube-задачу в очередь с overwrite=true."""
    total = queryset.count()
    if total == 0:
        admin_obj.message_user(
            request,
            "Выберите записи для обновления аудио треков из YouTube.",
            level=messages.WARNING,
        )
        return

    record_ids = list(queryset.values_list("pk", flat=True))
    try:
        job = admin_obj.record_service.enqueue_manual_youtube_audio_enrichment(
            record_ids=record_ids,
            requested_by_user_id=getattr(request.user, "id", None),
        )
    except Exception as exc:  # noqa: BLE001
        _log_admin_action_event(
            logging.ERROR,
            "youtube_audio_enqueue_failed",
            "Не удалось поставить в очередь задачу обновления аудио треков из YouTube.",
            record_ids=",".join(str(record_id) for record_id in record_ids),
            error=str(exc),
        )
        logger.exception("Детали ошибки запуска YouTube job из admin action.")
        admin_obj.message_user(
            request,
            f"Не удалось запустить обновление аудио треков из YouTube: {exc!s}",
            level=messages.ERROR,
        )
        return

    report_url = reverse("admin:records_audioenrichmentjob_change", args=[job.id])
    _log_admin_action_event(
        logging.INFO,
        "youtube_audio_enqueued",
        "Поставлена в очередь задача обновления аудио треков из YouTube из admin action.",
        job_id=job.id,
        records_total=total,
        record_ids=",".join(str(record_id) for record_id in record_ids),
        requested_by_user_id=getattr(request.user, "id", None),
    )
    admin_obj.message_user(
        request,
        (
            f"Поставлена в очередь задача обновления аудио треков из YouTube "
            f"для {total} записей."
        ),
        level=messages.SUCCESS,
    )
    admin_obj.message_user(
        request,
        format_html(
            'Отчёт задачи: <a href="{}">Открыть job report</a>.',
            report_url,
        ),
        level=messages.INFO,
    )


@admin.action(description="Найти аудио на YouTube")
def find_audio_on_youtube(
    admin_obj: RecordAdmin, request: HttpRequest, queryset: QuerySet[Record]
) -> None:
    total = queryset.count()
    if total == 0:
        admin_obj.message_user(
            request,
            "Выберите записи для поиска аудио на YouTube.",
            level=messages.WARNING,
        )
        return

    ok = skip = fail = 0
    skipped: list[str] = []
    failed: list[str] = []
    record_service = admin_obj.record_service
    user_id = getattr(request.user, "id", None)
    username = getattr(request.user, "username", "unknown")

    _log_admin_action_event(
        logging.INFO,
        "youtube_search_start",
        "Запущен поиск аудио на YouTube для выбранных записей.",
        records_total=total,
        username=username,
    )

    for record in queryset:
        missing = record.tracks.filter(
            Q(youtube_url__isnull=True) | Q(youtube_url="")
        ).exists()
        if not missing:
            skip += 1
            skipped.append(f"#{record.pk} «{record}»: ссылки уже заполнены")
            continue
        try:
            record_service.enqueue_record_youtube_audio_search(
                record=record,
                requested_by_user_id=user_id,
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            fail += 1
            failed.append(f"#{record.pk} «{record}»: {exc!s}")
            _log_admin_action_event(
                logging.ERROR,
                "youtube_search_failed",
                "Не удалось поставить запись в очередь поиска YouTube.",
                record_id=record.pk,
                error=str(exc),
            )
            logger.exception("Детали ошибки постановки поиска YouTube.")

    if ok:
        admin_obj.message_user(
            request,
            f"Поиск YouTube запущен для {ok} из {total} записей.",
            level=messages.SUCCESS,
        )
    if skip:
        admin_obj.message_user(
            request,
            f"Пропущено (ссылки уже есть): {skip}.",
            level=messages.WARNING,
        )
        admin_obj.message_user(
            request,
            "Пропущено:\n• " + "\n• ".join(skipped),
            level=messages.INFO,
        )
    if fail:
        admin_obj.message_user(
            request,
            f"Ошибок при постановке в очередь: {fail}.",
            level=messages.ERROR,
        )
        admin_obj.message_user(
            request,
            format_html_join(
                "", "<br>&nbsp;&nbsp;&bull; {}", ((item,) for item in failed)
            ),
            level=messages.ERROR,
        )

    _log_admin_action_event(
        logging.INFO,
        "youtube_search_finish",
        "Поиск аудио на YouTube для выбранных записей завершён.",
        ok=ok,
        skipped=skip,
        failed=fail,
        records_total=total,
        username=username,
    )


@admin.action(description="Обновить из Redeye")
def update_from_redeye(
    admin_obj: RecordAdmin, request: HttpRequest, queryset: QuerySet[Record]
) -> None:
    record_service = admin_obj.record_service

    def _get_redeye_catalog_number(record: Record) -> str | None:
        raw_value = getattr(record, "catalog_number", None)
        if not isinstance(raw_value, str):
            _log_admin_action_event(
                NOTICE_LEVEL,
                "redeye_catalog_invalid",
                "Запись пропущена при обновлении из Redeye: каталожный номер невалиден.",
                record_id=record.pk,
                raw_catalog_number=raw_value,
                catalog_number="—",
                reason="invalid_catalog_number",
            )
            return None

        normalized = raw_value.strip().upper()
        if not normalized:
            _log_admin_action_event(
                NOTICE_LEVEL,
                "redeye_catalog_invalid",
                "Запись пропущена при обновлении из Redeye: каталожный номер пуст.",
                record_id=record.pk,
                raw_catalog_number=raw_value,
                catalog_number="—",
                reason="invalid_catalog_number",
            )
            return None

        if normalized in {"NONE", "NULL", "N/A", "N-A", "-", "—"}:
            _log_admin_action_event(
                NOTICE_LEVEL,
                "redeye_catalog_invalid",
                "Запись пропущена при обновлении из Redeye: каталожный номер содержит псевдо-пустое значение.",
                record_id=record.pk,
                raw_catalog_number=raw_value,
                catalog_number=normalized,
                reason="invalid_catalog_number",
            )
            return None

        return normalized

    def _format_redeye_failed_item(
        record: Record, _extract_id: str | None, _error: Exception
    ) -> str:
        record_label = f"#{record.pk} «{record}»"
        catalog_number = _get_redeye_catalog_number(record) or "—"
        return (
            f"Обновление записи с id {record_label} из Redeye невозможно: "
            f"на сайте не найден релиз с каталожным номером '{catalog_number}'."
        )

    _batch_update(
        admin_obj,
        request,
        queryset,
        start_log="Обновление из Redeye",
        empty_msg="Выберите записи для обновления из Redeye.",
        ok_msg="Обновлено из Redeye: {ok} из {total}.",
        skip_msg="Пропущено (нет каталожного номера): {n}.",
        skip_header="Пропущено:",
        fail_msg="С ошибками: {n}.",
        fail_header="Ошибки:",
        id_label="каталожный номер",
        get_id=_get_redeye_catalog_number,
        do_update=lambda record: record_service.attach_audio_from_redeye(
            record=record,
            force=False,
            require_source=True,
        ),
        format_failed_item=_format_redeye_failed_item,
        show_fail_summary=False,
        expected_errors=(ValueError,),
    )
