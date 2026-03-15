from __future__ import annotations

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from records.admin.record_admin import RecordAdmin
from records.constants import SOURCE_CHOICES, SOURCE_DISCOGS, SOURCE_REDEYE
from records.models import Record


def test_source_choices_include_only_redeye_and_discogs_in_expected_order() -> None:
    assert SOURCE_CHOICES == (
        (SOURCE_REDEYE, "Redeye Records"),
        (SOURCE_DISCOGS, "Discogs"),
    )


def test_get_fieldsets_add_defaults_to_redeye_description() -> None:
    admin = RecordAdmin(Record, AdminSite())
    request = RequestFactory().get("/admin/records/record/add/")

    fieldsets = admin.get_fieldsets(request, obj=None)

    assert fieldsets[0][1]["fields"] == (
        "source",
        "catalog_number",
        "source_url",
    )
    assert "Импорт из Redeye" in fieldsets[0][1]["description"]


def test_get_fieldsets_add_uses_discogs_description_when_source_selected() -> None:
    admin = RecordAdmin(Record, AdminSite())
    request = RequestFactory().get(
        "/admin/records/record/add/",
        {"source": SOURCE_DISCOGS},
    )

    fieldsets = admin.get_fieldsets(request, obj=None)

    assert fieldsets[0][1]["fields"] == (
        "source",
        "discogs_id",
        "catalog_number",
        "barcode",
    )
    assert "Discogs" in fieldsets[0][1]["description"]


def test_get_changeform_initial_data_defaults_to_redeye() -> None:
    admin = RecordAdmin(Record, AdminSite())
    request = RequestFactory().get("/admin/records/record/add/")

    initial = admin.get_changeform_initial_data(request)

    assert initial["source"] == SOURCE_REDEYE


def test_get_changeform_initial_data_uses_source_from_query() -> None:
    admin = RecordAdmin(Record, AdminSite())
    request = RequestFactory().get(
        "/admin/records/record/add/",
        {"source": SOURCE_DISCOGS},
    )

    initial = admin.get_changeform_initial_data(request)

    assert initial["source"] == SOURCE_DISCOGS


def test_get_readonly_fields_add_allows_discogs_id_editing() -> None:
    admin = RecordAdmin(Record, AdminSite())
    request = RequestFactory().get("/admin/records/record/add/")

    readonly = admin.get_readonly_fields(request, obj=None)

    assert "discogs_id" not in readonly


def test_get_readonly_fields_change_keeps_discogs_id_readonly() -> None:
    admin = RecordAdmin(Record, AdminSite())
    request = RequestFactory().get("/admin/records/record/1/change/")
    obj = Record(title="Test")

    readonly = admin.get_readonly_fields(request, obj=obj)

    assert "discogs_id" in readonly


def _attach_session(request) -> None:
    session_middleware = SessionMiddleware(lambda req: None)
    session_middleware.process_request(request)
    request.session.save()


@pytest.mark.django_db
def test_add_view_catches_validation_error_and_redirects(monkeypatch) -> None:
    admin = RecordAdmin(Record, AdminSite())
    request = RequestFactory().post(
        "/admin/records/record/add/",
        {"source": SOURCE_DISCOGS, "catalog_number": "SP 34"},
    )
    _attach_session(request)
    request._messages = FallbackStorage(request)

    def _raise_validation_error(*args, **kwargs):
        raise ValidationError(
            {
                "catalog_number": [
                    "Не удалось импортировать из discogs: Ошибка при импорте по каталожному номеру. Попробуйте добавить по barecode или по Discogs ID."
                ]
            }
        )

    monkeypatch.setattr(
        "django.contrib.admin.options.ModelAdmin.add_view",
        _raise_validation_error,
    )

    response = admin.add_view(request)

    assert response.status_code == 302
    assert response["Location"].endswith("/admin/records/record/add/?source=discogs")
    rendered_messages = [str(msg) for msg in messages.get_messages(request)]
    assert any(
        "Ошибка при импорте по каталожному номеру" in msg for msg in rendered_messages
    )
