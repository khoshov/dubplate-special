from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.core.exceptions import TooManyFieldsSent
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from config.logging import log_event

logger = logging.getLogger(__name__)
_CORE_MIDDLEWARE_COMPONENT = "core_middleware"

ADMIN_TOO_MANY_FIELDS_SESSION_KEY = "admin_too_many_fields_error"
ADMIN_RECORD_CHANGELIST_PATH = "/admin/records/record/"


class AdminTooManyFieldsSentMiddleware:
    """Middleware перехватывает TooManyFieldsSent для changelist записей в админке."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_exception(
        self, request: HttpRequest, exception: Exception
    ) -> HttpResponse | None:
        """Преобразует ошибку превышения лимита полей в редирект с сообщением."""
        if not isinstance(exception, TooManyFieldsSent):
            return None
        if request.method != "POST":
            return None
        normalized_path = f"{request.path.rstrip('/')}/"
        if normalized_path != ADMIN_RECORD_CHANGELIST_PATH:
            return None

        limit = int(getattr(settings, "DATA_UPLOAD_MAX_NUMBER_FIELDS", 1000))
        message = (
            "Не удалось выполнить действие: выбрано слишком много записей. "
            f"Текущий лимит полей формы: {limit}. "
            "Уменьшите размер выборки и повторите."
        )

        request.session[ADMIN_TOO_MANY_FIELDS_SESSION_KEY] = message
        log_event(
            logger,
            logging.WARNING,
            "Перехвачена TooManyFieldsSent в админке.",
            component=_CORE_MIDDLEWARE_COMPONENT,
            event="too_many_fields",
            path=request.path,
            limit=limit,
        )

        return HttpResponseRedirect(request.get_full_path())
