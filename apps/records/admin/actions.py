from __future__ import annotations
import logging
import time
from typing import Any, Callable

from django.contrib import admin, messages
from django.http import HttpRequest

logger = logging.getLogger(__name__)


def _batch_update(
    admin_obj: Any,
    request: HttpRequest,
    queryset,
    *,
    start_log: str,
    empty_msg: str,
    ok_msg: str,
    skip_msg: str,
    skip_header: str,
    fail_msg: str,
    fail_header: str,
    id_label: str,
    get_id: Callable[[Any], str | None],
    do_update: Callable[[Any], object],
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
        record_label = f"#{record.pk} «{record}»"
        extract_id = get_id(record)
        if not extract_id:
            skip += 1
            skipped.append(f"{record_label}: нет {id_label}")
            continue
        try:
            do_update(record)
            ok += 1
        except Exception as e:
            fail += 1
            failed.append(f"{record_label}: {e!s}")
            logger.exception(
                "Ошибка при обновлении %s (%s=%s): %s",
                record_label,
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


@admin.action(description="Обновить из Discogs")
def update_from_discogs(admin_obj: Any, request: HttpRequest, queryset) -> None:
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
        get_id=lambda record: getattr(record, "discogs_id", None),
        do_update=lambda record: record_service.update_from_discogs(record),
    )


@admin.action(description="Обновить из Redeye")
def update_from_redeye(admin_obj: Any, request: HttpRequest, queryset) -> None:
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
        get_id=lambda record: getattr(record, "catalog_number", None),
        do_update=lambda record: record_service.import_from_redeye(catalog_number=record.catalog_number,
                                                                   save_image_decision=True,
                                                                   download_audio_decision=True),
    )
