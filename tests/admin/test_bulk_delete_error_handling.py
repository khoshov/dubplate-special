from __future__ import annotations

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import TooManyFieldsSent
from django.test import RequestFactory

from core.middleware import (
    ADMIN_TOO_MANY_FIELDS_SESSION_KEY,
    AdminTooManyFieldsSentMiddleware,
)
from records.admin.record_admin import RecordAdmin
from records.models import Record


def _attach_session(request) -> None:
    session_middleware = SessionMiddleware(lambda req: None)
    session_middleware.process_request(request)
    request.session.save()


def _build_middleware() -> AdminTooManyFieldsSentMiddleware:
    return AdminTooManyFieldsSentMiddleware(lambda request: None)


@pytest.mark.django_db
def test_admin_too_many_fields_is_redirected_with_session_message(settings):
    settings.DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000

    request = RequestFactory().post("/admin/records/record/")
    _attach_session(request)

    response = _build_middleware().process_exception(
        request, TooManyFieldsSent("Too many fields sent")
    )

    assert response is not None
    assert response.status_code == 302
    assert response["Location"] == "/admin/records/record/"
    message = request.session.get(ADMIN_TOO_MANY_FIELDS_SESSION_KEY, "")
    assert "слишком много записей" in message
    assert "5000" in message


@pytest.mark.django_db
def test_admin_too_many_fields_ignores_non_admin_path():
    request = RequestFactory().post("/api/v1/records/")
    _attach_session(request)

    response = _build_middleware().process_exception(
        request, TooManyFieldsSent("Too many fields sent")
    )

    assert response is None
    assert ADMIN_TOO_MANY_FIELDS_SESSION_KEY not in request.session


@pytest.mark.django_db
def test_admin_too_many_fields_ignores_other_admin_path():
    request = RequestFactory().post("/admin/records/genre/")
    _attach_session(request)

    response = _build_middleware().process_exception(
        request, TooManyFieldsSent("Too many fields sent")
    )

    assert response is None
    assert ADMIN_TOO_MANY_FIELDS_SESSION_KEY not in request.session


@pytest.mark.django_db
def test_record_admin_changelist_consumes_session_error_message():
    user = get_user_model().objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )

    request = RequestFactory().get("/admin/records/record/")
    request.user = user
    _attach_session(request)
    request.session[ADMIN_TOO_MANY_FIELDS_SESSION_KEY] = "Тестовая ошибка лимита."
    request._messages = FallbackStorage(request)

    admin = RecordAdmin(Record, AdminSite())
    response = admin.changelist_view(request)

    assert response.status_code == 200
    rendered_messages = [str(msg) for msg in messages.get_messages(request)]
    assert any("Тестовая ошибка лимита." in msg for msg in rendered_messages)
    assert ADMIN_TOO_MANY_FIELDS_SESSION_KEY not in request.session
