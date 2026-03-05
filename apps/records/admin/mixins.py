from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import format_html

logger = logging.getLogger(__name__)


class RedeyeAudioRefreshMixin:
    """
    Вспомогательный класс с дополнительными методами для
    RecordAdmin: добавляет URL и обработчик кнопки
    «Закачать mp3 с Redeye» на странице редактирования записи.

    Требуется атрибут self.record_service с методом:
    attach_audio_from_redeye(record, *, force: bool = False) -> int
    """

    def get_urls(self: Any):
        base_urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/refresh/",
                self.admin_site.admin_view(self._refresh_audio_view),
                name="records_record_redeye_mp3_download",
            ),
        ]
        return custom + base_urls

    @transaction.atomic
    def _refresh_audio_view(
        self: Any, request: HttpRequest, object_id: str
    ) -> HttpResponse:
        """Обработчик кнопки «Закачать mp3 с Redeye»."""
        if request.method != "POST":
            messages.error(request, "Разрешён только POST-запрос.")
            return redirect(reverse("admin:records_record_change", args=[object_id]))

        obj = get_object_or_404(self.model, pk=object_id)

        if not self.has_change_permission(request, obj):
            messages.error(request, "Недостаточно прав для обновления этой записи.")
            return redirect(reverse("admin:records_record_change", args=[obj.pk]))

        if not getattr(obj, "catalog_number", None):
            messages.error(
                request,
                "Невозможно закачать аудио: у записи не указан каталожный номер.",
            )
            return redirect(reverse("admin:records_record_change", args=[obj.pk]))

        try:
            added_count: int = self.record_service.attach_audio_from_redeye(
                obj, force=False
            )
            if added_count > 0:
                messages.success(request, f"Добавлены mp3-превью: {added_count}.")
            else:
                messages.info(request, "Новых mp3-превью не найдено.")
        except Exception as e:
            logger.exception(
                "Ошибка закачки mp3 с Redeye для записи #%s: %s", obj.pk, e
            )
            messages.error(request, f"Не удалось закачать аудио: {e!s}")

        return redirect(reverse("admin:records_record_change", args=[obj.pk]))


class YouTubeAudioRefreshMixin:
    """
    Добавляет URL и обработчик кнопки
    «Обновить аудио из YouTube» на странице редактирования записи.

    Требуется атрибут self.record_service с методом:
    enqueue_record_youtube_audio_enrichment(record, *, requested_by_user_id: int | None) -> AudioEnrichmentJob
    """

    def get_urls(self: Any):
        base_urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/youtube-refresh/",
                self.admin_site.admin_view(self._refresh_youtube_audio_view),
                name="records_record_youtube_audio_refresh",
            ),
        ]
        return custom + base_urls

    @transaction.atomic
    def _refresh_youtube_audio_view(
        self: Any, request: HttpRequest, object_id: str
    ) -> HttpResponse:
        """Обработчик кнопки «Обновить аудио из YouTube»."""
        if request.method != "POST":
            messages.error(request, "Разрешён только POST-запрос.")
            return redirect(reverse("admin:records_record_change", args=[object_id]))

        obj = get_object_or_404(self.model, pk=object_id)

        if not self.has_change_permission(request, obj):
            messages.error(request, "Недостаточно прав для обновления этой записи.")
            return redirect(reverse("admin:records_record_change", args=[obj.pk]))

        try:
            job = self.record_service.enqueue_record_youtube_audio_enrichment(
                record=obj,
                requested_by_user_id=getattr(request.user, "id", None),
            )
            report_url = reverse(
                "admin:records_audioenrichmentjob_change", args=[job.id]
            )
            messages.success(
                request,
                "Поставлено в очередь YouTube-аудио-обогащение для записи.",
            )
            messages.info(
                request,
                format_html(
                    'Отчёт задачи: <a href="{}">Открыть job report</a>.',
                    report_url,
                ),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "Ошибка запуска YouTube-аудио-обогащения для записи #%s: %s",
                obj.pk,
                e,
            )
            messages.error(
                request,
                f"Не удалось запустить обновление аудио из YouTube: {e!s}",
            )

        return redirect(reverse("admin:records_record_change", args=[obj.pk]))
