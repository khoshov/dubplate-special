from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from records.models import Record, VKPublicationLog


def register_vk_publication_event(
    *,
    record: Record,
    mode: str,
    status: str,
    planned_publish_at: datetime | None = None,
    effective_publish_at: datetime | None = None,
    vk_post_id: int | None = None,
    error_message: str = "",
) -> VKPublicationLog:
    """
    Регистрирует событие публикации в VK и синхронизирует поле Record.vk_published_at.

    Для успешной публикации обновляет `record.vk_published_at` на effective/planned/now (в этом порядке).
    Для ошибок пишет только лог-событие.
    """
    effective_at = effective_publish_at
    if status == VKPublicationLog.Status.SUCCESS and effective_at is None:
        effective_at = planned_publish_at or timezone.now()

    normalized_error = (error_message or "").strip()

    with transaction.atomic():
        event = VKPublicationLog.objects.create(
            record=record,
            mode=mode,
            status=status,
            planned_publish_at=planned_publish_at,
            effective_publish_at=effective_at,
            vk_post_id=vk_post_id,
            error_message=normalized_error,
        )

        if status == VKPublicationLog.Status.SUCCESS and effective_at is not None:
            Record.objects.filter(pk=record.pk).update(vk_published_at=effective_at)

    return event
