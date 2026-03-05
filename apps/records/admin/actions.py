from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponseRedirect
from django.utils.text import Truncator
from django.utils import timezone
from django.urls import reverse
from django.utils.html import format_html

from records.models import Record, VKPublicationLog
from records.services.social.publication_log import register_vk_publication_event

if TYPE_CHECKING:
    from .record_admin import RecordAdmin

logger = logging.getLogger(__name__)


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
) -> None:
    """Универсальный исполнитель массового обновления."""
    start_ts = time.perf_counter()
    total = queryset.count()
    user = getattr(request, "user", None)
    username = getattr(user, "username", "unknown")

    logger.info("%s (записей: %s, пользователь: %s)", start_log, total, username)

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
            failed.append(f"{log_record_name}: {e!s}")
            if on_error is not None:
                on_error(record, e)
            logger.exception(
                "Ошибка при обновлении %s (%s=%s): %s",
                log_record_name,
                id_label,
                extract_id,
                e,
            )

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
        admin_obj.message_user(request, fail_msg.format(n=fail), level=messages.ERROR)
        admin_obj.message_user(
            request, fail_header + "\n• " + "\n• ".join(failed), level=messages.ERROR
        )

    logger.info(
        "%s завершено: ок=%s, пропуск=%s, ошибки=%s, всего=%s, %.2fs, пользователь=%s",
        start_log,
        ok,
        skip,
        fail,
        total,
        time.perf_counter() - start_ts,
        username,
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
        logger.error(
            "VK: сервис не инициализирован в RecordAdmin.__init__ (vk_service отсутствует)."
        )
        return

    logger.info(
        "Публикация записей в VK (записей: %d, пользователь: %s).",
        queryset.count(),
        getattr(request.user, "username", "?"),
    )

    def _get_id(record: Record) -> str | None:
        return str(getattr(record, "pk", None))

    def _do_post(record: Record) -> int:
        title = getattr(record, "title", "")
        cover = getattr(record, "cover_image", None)
        cover_path = getattr(cover, "path", None) if cover else None
        logger.debug(
            "Попытка публикации: record_id=%s, title='%s', есть_обложка=%s, cover_path=%s",
            getattr(record, "pk", None),
            Truncator(title).chars(80),
            bool(cover_path),
            cover_path,
        )

        post_id = vk_service.post_record_with_audio(record=record)
        logger.info(
            "Опубликовано в VK с аудио: record_id=%s, post_id=%s, title=%s",
            getattr(record, "pk", None),
            post_id,
            Truncator(title).chars(80),
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
    enqueued_job_ids: list[str] = []

    def _on_success(_record: Record, result: object) -> None:
        job_id = getattr(result, "_discogs_enrichment_job_id", None)
        if job_id:
            enqueued_job_ids.append(str(job_id))

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
        do_update=lambda record: record_service.update_from_discogs(
            record=record,
            requested_by_user_id=getattr(request.user, "id", None),
        ),
        on_success=_on_success,
    )
    if enqueued_job_ids:
        admin_obj.message_user(
            request,
            (
                "Запущено YouTube-аудио-обогащение: "
                f"{len(enqueued_job_ids)} job(s). IDs: {', '.join(enqueued_job_ids)}"
            ),
            level=messages.INFO,
        )


@admin.action(description="Обновить аудио из YouTube")
def update_audio_from_youtube(
    admin_obj: RecordAdmin, request: HttpRequest, queryset: QuerySet[Record]
) -> None:
    """Ставит массовое YouTube-аудио-обогащение в очередь."""
    total = queryset.count()
    if total == 0:
        admin_obj.message_user(
            request,
            "Выберите записи для обновления аудио из YouTube.",
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
        logger.exception(
            "Ошибка запуска YouTube-аудио-обогащения (records=%s): %s",
            record_ids,
            exc,
        )
        admin_obj.message_user(
            request,
            f"Не удалось запустить обновление аудио из YouTube: {exc!s}",
            level=messages.ERROR,
        )
        return

    report_url = reverse("admin:records_audioenrichmentjob_change", args=[job.id])
    admin_obj.message_user(
        request,
        f"Поставлено в очередь YouTube-аудио-обогащение для {total} записей (job {job.id}).",
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


@admin.action(description="Обновить из Redeye")
def update_from_redeye(
    admin_obj: RecordAdmin, request: HttpRequest, queryset: QuerySet[Record]
) -> None:
    record_service = admin_obj.record_service
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
        get_id=lambda record: record.catalog_number,
        do_update=lambda record: record_service.import_from_redeye(
            catalog_number=record.catalog_number
        ),
    )
