from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from django.contrib import admin, messages
from django.db.models import QuerySet, Q
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from config.logging import NOTICE_LEVEL, log_event

from records.models import Record

if TYPE_CHECKING:
    from .record_admin import RecordAdmin

logger = logging.getLogger(__name__)
_ADMIN_ACTIONS_COMPONENT = "действия админки"


def _release_report_changelist_url(*, job_id: object) -> str:
    return (
        f"{reverse('admin:records_releasereport_changelist')}?job__id__exact={job_id}"
    )


def _release_report_detail_url(*, report_id: object) -> str:
    return reverse("admin:records_releasereport_change", args=[report_id])


def _vk_report_changelist_url(*, job_id: object) -> str:
    return (
        f"{reverse('admin:records_vkpublicationreport_changelist')}"
        f"?job__id__exact={job_id}"
    )


def _vk_report_detail_url(*, report_id: object) -> str:
    return reverse("admin:records_vkpublicationreport_change", args=[report_id])


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
    record_ids = list(queryset.values_list("pk", flat=True))
    if not record_ids:
        admin_obj.message_user(
            request,
            "Выберите записи для публикации в VK.",
            level=messages.WARNING,
        )
        return

    try:
        job = admin_obj.record_service.enqueue_vk_immediate_publication(
            record_ids=record_ids,
            requested_by_user_id=getattr(request.user, "id", None),
        )
    except Exception as exc:  # noqa: BLE001
        _log_admin_action_event(
            logging.ERROR,
            "vk_publish_enqueue_failed",
            "Не удалось поставить публикацию в VK в очередь.",
            error=str(exc),
            record_ids=",".join(str(record_id) for record_id in record_ids),
        )
        logger.exception(
            "Детали ошибки запуска VK job из admin action.",
            extra={
                "component": _ADMIN_ACTIONS_COMPONENT,
                "event": "vk_publish_enqueue_failed",
            },
        )
        admin_obj.message_user(
            request,
            f"Не удалось поставить публикацию в VK в очередь: {exc!s}",
            level=messages.ERROR,
        )
        return

    first_report = job.job_records.order_by("created", "id").first()
    report_url = (
        _vk_report_detail_url(report_id=first_report.pk)
        if len(record_ids) == 1 and first_report is not None
        else _vk_report_changelist_url(job_id=job.pk)
    )
    if len(record_ids) == 1:
        record = queryset.first()
        admin_obj.message_user(
            request,
            f"Релиз «{record}» отправлен на публикацию на стену VK.",
            level=messages.SUCCESS,
        )
    else:
        admin_obj.message_user(
            request,
            f"{len(record_ids)} релизов отправлены на публикацию на стену VK.",
            level=messages.SUCCESS,
        )
    admin_obj.message_user(
        request,
        format_html(
            'Логи публикации: <a href="{}">Открыть лог</a>.',
            report_url,
        ),
        level=messages.INFO,
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
        on_success=lambda record, _result: record_service.create_sync_release_report(
            record=record,
            requested_by_user_id=getattr(request.user, "id", None),
            source="manual_list",
            operation_name="Обновление релиза из Discogs",
            scope="release",
            release_source_name="Discogs",
            audio_source_summary="Не указан",
            result="Релиз обновлен",
            result_message="Обновление релиза из Discogs завершено успешно.",
        ),
        on_error=lambda record, exc: record_service.create_sync_release_report(
            record=record,
            requested_by_user_id=getattr(request.user, "id", None),
            source="manual_list",
            operation_name="Обновление релиза из Discogs",
            scope="release",
            release_source_name="Discogs",
            audio_source_summary="Не указан",
            status="failed",
            result="Операция завершилась с ошибкой",
            result_message="Обновление релиза из Discogs завершилось с ошибкой.",
            error_message=str(exc),
        ),
    )


@admin.action(description="Добавление аудио по URL (YouTube/Bandcamp)")
def update_audio_from_youtube(
    admin_obj: RecordAdmin, request: HttpRequest, queryset: QuerySet[Record]
) -> None:
    """Ставит массовую задачу добавления аудио по URL в очередь с overwrite=true."""
    total = queryset.count()
    if total == 0:
        admin_obj.message_user(
            request,
            "Выберите записи для добавления аудио по URL (YouTube/Bandcamp).",
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
            "Не удалось поставить в очередь задачу добавления аудио по URL (YouTube/Bandcamp).",
            record_ids=",".join(str(record_id) for record_id in record_ids),
            error=str(exc),
        )
        logger.exception("Детали ошибки запуска YouTube job из admin action.")
        admin_obj.message_user(
            request,
            f"Не удалось запустить добавление аудио по URL (YouTube/Bandcamp): {exc!s}",
            level=messages.ERROR,
        )
        return

    report_url = _release_report_changelist_url(job_id=job.id)
    _log_admin_action_event(
        logging.INFO,
        "youtube_audio_enqueued",
        "Поставлена в очередь задача добавления аудио по URL (YouTube/Bandcamp) из admin action.",
        job_id=job.id,
        records_total=total,
        record_ids=",".join(str(record_id) for record_id in record_ids),
        requested_by_user_id=getattr(request.user, "id", None),
    )
    admin_obj.message_user(
        request,
        f"Поставлена в очередь задача добавления аудио по URL (YouTube/Bandcamp) для {total} записей.",
        level=messages.SUCCESS,
    )
    admin_obj.message_user(
        request,
        format_html(
            'Логи операции: <a href="{}">Открыть лог</a>.',
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
    record_service = admin_obj.record_service
    user_id = getattr(request.user, "id", None)
    username = getattr(request.user, "username", "unknown")
    record_ids_to_search: list[int] = []

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
        record_ids_to_search.append(record.pk)

    job = None
    if record_ids_to_search:
        try:
            job = record_service.enqueue_manual_youtube_audio_search(
                record_ids=record_ids_to_search,
                requested_by_user_id=user_id,
            )
            ok = len(record_ids_to_search)
        except Exception as exc:  # noqa: BLE001
            fail = len(record_ids_to_search)
            _log_admin_action_event(
                logging.ERROR,
                "youtube_search_failed",
                "Не удалось поставить записи в очередь поиска YouTube.",
                record_ids=",".join(
                    str(record_id) for record_id in record_ids_to_search
                ),
                error=str(exc),
            )
            logger.exception("Детали ошибки постановки поиска YouTube.")

    if ok:
        admin_obj.message_user(
            request,
            f"Поиск YouTube запущен для {ok} из {total} записей.",
            level=messages.SUCCESS,
        )
        if job is not None:
            report_url = _release_report_changelist_url(job_id=job.id)
            admin_obj.message_user(
                request,
                format_html(
                    'Логи операции: <a href="{}">Открыть лог</a>.',
                    report_url,
                ),
                level=messages.INFO,
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
            f"Не удалось поставить в очередь поиск аудио на YouTube: {fail}.",
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
    total = queryset.count()
    if total == 0:
        admin_obj.message_user(
            request,
            "Выберите записи для обновления из Redeye.",
            level=messages.WARNING,
        )
        return

    record_ids = list(queryset.values_list("pk", flat=True))
    try:
        job = admin_obj.record_service.enqueue_manual_redeye_audio_enrichment(
            record_ids=record_ids,
            requested_by_user_id=getattr(request.user, "id", None),
        )
    except Exception as exc:  # noqa: BLE001
        _log_admin_action_event(
            logging.ERROR,
            "redeye_audio_enqueue_failed",
            "Не удалось поставить в очередь задачу обновления аудио из Redeye.",
            record_ids=",".join(str(record_id) for record_id in record_ids),
            error=str(exc),
        )
        logger.exception("Детали ошибки запуска Redeye job из admin action.")
        admin_obj.message_user(
            request,
            f"Не удалось запустить обновление аудио из Redeye: {exc!s}",
            level=messages.ERROR,
        )
        return

    report_url = _release_report_changelist_url(job_id=job.id)
    _log_admin_action_event(
        logging.INFO,
        "redeye_audio_enqueued",
        "Поставлена в очередь задача обновления аудио из Redeye из admin action.",
        job_id=job.id,
        records_total=total,
        record_ids=",".join(str(record_id) for record_id in record_ids),
        requested_by_user_id=getattr(request.user, "id", None),
    )
    admin_obj.message_user(
        request,
        f"Поставлена в очередь задача обновления аудио из Redeye для {total} записей.",
        level=messages.SUCCESS,
    )
    admin_obj.message_user(
        request,
        format_html(
            'Логи операции: <a href="{}">Открыть лог</a>.',
            report_url,
        ),
        level=messages.INFO,
    )
