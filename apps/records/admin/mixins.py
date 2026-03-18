from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from config.logging import log_event

from records.services.tasks import (
    login_youtube_session_profile,
    refresh_youtube_session_profile,
)

logger = logging.getLogger(__name__)
_ADMIN_MIXINS_COMPONENT = "records_admin_mixins"


def _log_admin_mixin_event(
    level: int,
    event: str,
    message: str,
    **context,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_ADMIN_MIXINS_COMPONENT,
        event=event,
        **context,
    )


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
            _log_admin_mixin_event(
                logging.INFO,
                "redeye_audio_refresh_finished",
                "Завершено обновление аудио из Redeye по кнопке записи.",
                record_id=obj.pk,
                updated_tracks=added_count,
                overwrite=False,
            )
            if added_count > 0:
                messages.success(request, f"Добавлены mp3-превью: {added_count}.")
            else:
                messages.info(request, "Новых mp3-превью не найдено.")
        except Exception as exc:  # noqa: BLE001
            _log_admin_mixin_event(
                logging.ERROR,
                "redeye_audio_refresh_failed",
                "Не удалось выполнить обновление аудио из Redeye по кнопке записи.",
                record_id=obj.pk,
                error=str(exc),
            )
            logger.exception("Детали ошибки обновления Redeye по кнопке записи.")
            messages.error(request, f"Не удалось закачать аудио: {exc!s}")

        return redirect(reverse("admin:records_record_change", args=[obj.pk]))


class YouTubeAudioRefreshMixin:
    """
    Добавляет URL и обработчик кнопки
    «Обновить треки из YouTube» на странице редактирования записи.

    Требуется атрибут self.record_service с методом:
    enqueue_record_youtube_audio_enrichment(record, *, requested_by_user_id: int | None)
    """

    def get_urls(self: Any):
        base_urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/youtube-refresh/",
                self.admin_site.admin_view(self._refresh_youtube_audio_view),
                name="records_record_youtube_audio_refresh",
            ),
            path(
                "<path:object_id>/youtube-search/",
                self.admin_site.admin_view(self._search_youtube_audio_view),
                name="records_record_youtube_audio_search",
            ),
            path(
                "youtube-session/refresh/",
                self.admin_site.admin_view(self._refresh_youtube_session_view),
                name="records_record_youtube_session_refresh",
            ),
            path(
                "youtube-session/login/",
                self.admin_site.admin_view(self._login_youtube_session_view),
                name="records_record_youtube_session_login",
            ),
            path(
                "youtube-session/recover/",
                self.admin_site.admin_view(self._recover_youtube_session_view),
                name="records_record_youtube_session_recover",
            ),
        ]
        return custom + base_urls

    @transaction.atomic
    def _refresh_youtube_audio_view(
        self: Any, request: HttpRequest, object_id: str
    ) -> HttpResponse:
        """Обработчик кнопки «Обновить аудио треков из YouTube»."""
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
            _log_admin_mixin_event(
                logging.INFO,
                "youtube_audio_refresh_enqueued",
                "Поставлена в очередь задача обновления аудио треков из YouTube по кнопке записи.",
                record_id=obj.pk,
                job_id=job.id,
                requested_by_user_id=getattr(request.user, "id", None),
            )
            report_url = reverse(
                "admin:records_audioenrichmentjob_change",
                args=[job.id],
            )
            messages.success(
                request,
                "Поставлена в очередь задача обновления аудио треков из YouTube.",
            )
            messages.info(
                request,
                format_html(
                    'Отчёт задачи: <a href="{}">Открыть job report</a>.',
                    report_url,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            _log_admin_mixin_event(
                logging.ERROR,
                "youtube_audio_refresh_failed",
                "Не удалось поставить в очередь задачу обновления аудио треков из YouTube по кнопке записи.",
                record_id=obj.pk,
                error=str(exc),
            )
            logger.exception("Детали ошибки запуска YouTube job по кнопке записи.")
            messages.error(
                request,
                f"Не удалось запустить обновление аудио треков из YouTube: {exc!s}",
            )

        return redirect(reverse("admin:records_record_change", args=[obj.pk]))

    @transaction.atomic
    def _search_youtube_audio_view(
        self: Any, request: HttpRequest, object_id: str
    ) -> HttpResponse:
        """Обработчик кнопки «Найти аудио на YouTube»."""
        _log_admin_mixin_event(
            logging.DEBUG,
            "youtube_audio_search_request",
            "Получен запрос на поиск аудио на YouTube.",
            record_id=object_id,
            method=request.method,
        )
        if request.method != "POST":
            error_payload = {
                "ok": False,
                "error": "Разрешён только POST-запрос.",
            }
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse(error_payload, status=405)
            messages.error(request, "Разрешён только POST-запрос.")
            return redirect(reverse("admin:records_record_change", args=[object_id]))

        obj = get_object_or_404(self.model, pk=object_id)

        if not self.has_change_permission(request, obj):
            error_payload = {
                "ok": False,
                "error": "Недостаточно прав для обновления этой записи.",
            }
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse(error_payload, status=403)
            messages.error(request, "Недостаточно прав для обновления этой записи.")
            return redirect(reverse("admin:records_record_change", args=[obj.pk]))

        try:
            self.record_service.enqueue_record_youtube_audio_search(
                record=obj,
                requested_by_user_id=getattr(request.user, "id", None),
            )
            _log_admin_mixin_event(
                logging.INFO,
                "youtube_audio_search_enqueued",
                f"Поставлен в очередь поиск аудио на YouTube для релиза «{obj}».",
                record_id=obj.pk,
                requested_by_user_id=getattr(request.user, "id", None),
            )
            _log_admin_mixin_event(
                logging.DEBUG,
                "youtube_audio_search_enqueued",
                "Детали постановки поиска аудио на YouTube.",
                record_id=obj.pk,
                requested_by_user_id=getattr(request.user, "id", None),
            )
            messages.success(
                request,
                "Поставлена в очередь задача поиска аудио на YouTube.",
            )
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": True})
        except Exception as exc:  # noqa: BLE001
            _log_admin_mixin_event(
                logging.ERROR,
                "youtube_audio_search_failed",
                "Не удалось поставить в очередь задачу поиска аудио на YouTube.",
                record_id=obj.pk,
                error=str(exc),
            )
            logger.exception("Детали ошибки запуска поиска YouTube.")
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse(
                    {
                        "ok": False,
                        "error": "Не удалось запустить поиск аудио на YouTube.",
                    },
                    status=500,
                )
            messages.error(
                request,
                f"Не удалось запустить поиск аудио на YouTube: {exc!s}",
            )

        return redirect(reverse("admin:records_record_change", args=[obj.pk]))

    def _refresh_youtube_session_view(self: Any, request: HttpRequest) -> HttpResponse:
        """Ставит headless refresh YouTube-сессии в очередь."""
        if request.method != "POST":
            messages.error(request, "Разрешён только POST-запрос.")
            return redirect(self._youtube_session_redirect_target(request))

        try:
            refresh_youtube_session_profile.delay()
            _log_admin_mixin_event(
                logging.INFO,
                "youtube_session_refresh_enqueued",
                "Поставлена в очередь задача обновления YouTube-сессии.",
                requested_by_user_id=getattr(
                    getattr(request, "user", None), "id", None
                ),
            )
            messages.success(
                request,
                "Поставлена в очередь задача обновления YouTube-сессии.",
            )
        except Exception as exc:  # noqa: BLE001
            _log_admin_mixin_event(
                logging.ERROR,
                "youtube_session_refresh_failed",
                "Не удалось поставить в очередь задачу обновления YouTube-сессии.",
                error=str(exc),
            )
            logger.exception("Детали ошибки запуска обновления YouTube-сессии.")
            messages.error(
                request,
                f"Не удалось запустить обновление YouTube-сессии: {exc!s}",
            )

        return redirect(self._youtube_session_redirect_target(request))

    def _login_youtube_session_view(self: Any, request: HttpRequest) -> HttpResponse:
        """Ставит интерактивную авторизацию YouTube-сессии в очередь."""
        if request.method != "POST":
            messages.error(request, "Разрешён только POST-запрос.")
            return redirect(self._youtube_session_redirect_target(request))

        timeout_sec = int(
            getattr(settings, "YOUTUBE_SESSION_LOGIN_TIMEOUT_MS", 900_000) / 1000
        )
        ui_url = str(getattr(settings, "YOUTUBE_SESSION_UI_URL", "") or "").strip()
        try:
            login_youtube_session_profile.delay(timeout_sec=timeout_sec)
            _log_admin_mixin_event(
                logging.INFO,
                "youtube_session_login_enqueued",
                "Поставлена в очередь интерактивная авторизация YouTube-сессии.",
                requested_by_user_id=getattr(
                    getattr(request, "user", None), "id", None
                ),
                timeout_sec=timeout_sec,
            )
            messages.success(
                request,
                "Запущена интерактивная авторизация YouTube-сессии.",
            )
            if ui_url:
                messages.info(
                    request,
                    format_html(
                        'Откройте окно авторизации: <a href="{}" target="_blank" rel="noopener">YouTube session UI</a>.',
                        ui_url,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            _log_admin_mixin_event(
                logging.ERROR,
                "youtube_session_login_failed",
                "Не удалось поставить в очередь интерактивную авторизацию YouTube-сессии.",
                error=str(exc),
            )
            logger.exception("Детали ошибки запуска авторизации YouTube-сессии.")
            messages.error(
                request,
                f"Не удалось запустить авторизацию YouTube-сессии: {exc!s}",
            )

        return redirect(self._youtube_session_redirect_target(request))

    def _recover_youtube_session_view(self: Any, request: HttpRequest) -> HttpResponse:
        """Открывает recovery-страницу, которая сама запускает окно и login-task."""
        if request.method != "GET":
            messages.error(request, "Разрешён только GET-запрос.")
            return redirect(self._youtube_session_redirect_target(request))

        next_url = self._youtube_session_redirect_target(request)
        context = {
            **self.admin_site.each_context(request),
            "title": "Авторизация YouTube-сессии",
            "opts": self.model._meta,
            "ui_url": str(
                getattr(settings, "YOUTUBE_SESSION_UI_URL", "") or ""
            ).strip(),
            "login_url": reverse("admin:records_record_youtube_session_login"),
            "next_url": next_url,
            "launch_delay_ms": 2_000,
        }
        return TemplateResponse(
            request,
            "admin/records/youtube_session_recover.html",
            context,
        )

    @staticmethod
    def _youtube_session_redirect_target(request: HttpRequest) -> str:
        next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
        if next_url:
            return next_url
        referer = (request.META.get("HTTP_REFERER") or "").strip()
        if referer:
            return referer
        return reverse("admin:index")
