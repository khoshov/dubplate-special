from __future__ import annotations

from django.contrib.admin.sites import AdminSite
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
