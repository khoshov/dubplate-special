from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from records.admin.inlines import StructuredFormatInline
from records.admin.record_admin import RecordAdmin
from records.models import Format, Record, StructuredFormat


def _make_superuser():
    return get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )


def test_record_admin_places_details_fieldset_last_for_structured_format_block() -> (
    None
):
    admin = RecordAdmin(Record, AdminSite())

    assert admin.fieldsets[-1][0] == "Детали"
    assert admin.fieldsets[3][0] == "Склад и цены"
    assert "condition" in admin.fieldsets[3][1]["fields"]
    assert "condition" not in admin.fieldsets[-1][1]["fields"]


@pytest.mark.django_db
def test_record_format_inline_uses_custom_template_and_disables_row_management() -> (
    None
):
    inline = StructuredFormatInline(Record, AdminSite())
    request = RequestFactory().get("/admin/records/record/1/change/")
    empty_record = Record.objects.create(title="Empty structured")
    record_with_structured = Record.objects.create(title="Structured exists")
    StructuredFormat.objects.create(
        record=record_with_structured,
        variant_of_format=1,
        carrier="Vinyl",
        quantity=1,
        format_name='12"',
        details="Album",
    )

    assert inline.template == "admin/edit_inline/record_format_tabular.html"
    assert inline.fields == ("carrier", "quantity", "format_name", "details")
    assert inline.has_add_permission(request, empty_record) is True
    assert inline.has_add_permission(request, record_with_structured) is False
    assert inline.has_delete_permission(request) is False


@pytest.mark.django_db
def test_record_format_inline_queryset_preserves_variant_of_format() -> None:
    record = Record.objects.create(title="Ordering")
    request_user = _make_superuser()
    StructuredFormat.objects.create(
        record=record,
        variant_of_format=2,
        carrier="CD",
        quantity=1,
        format_name="Album",
        details="Second",
    )
    StructuredFormat.objects.create(
        record=record,
        variant_of_format=1,
        carrier="Vinyl",
        quantity=1,
        format_name='12"',
        details="First",
    )

    inline = StructuredFormatInline(Record, AdminSite())
    request = RequestFactory().get("/admin/records/record/1/change/")
    request.user = request_user
    queryset = inline.get_queryset(request)

    assert list(
        queryset.filter(record=record).values_list("variant_of_format", flat=True)
    ) == [
        1,
        2,
    ]


@pytest.mark.django_db
def test_change_page_shows_blank_structured_format_row_and_legacy_formats(
    client,
) -> None:
    admin_user = _make_superuser()
    client.force_login(admin_user)
    record = Record.objects.create(title="Empty state")
    format_obj, _ = Format.objects.get_or_create(name='12" Vinyl')
    record.formats.add(format_obj)

    response = client.get(reverse("admin:records_record_change", args=[record.pk]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'id="id_structured_formats-0-carrier"' in content
    assert 'id="id_structured_formats-0-format_name"' in content
    assert "structured-format-variant-select" not in content
    assert "Нет структурированных форматов Discogs." not in content
    assert "Структурированный формат релиза" in content
    assert "Форматы" in content


@pytest.mark.django_db
def test_record_format_inline_formset_allows_clearing_existing_row() -> None:
    admin_user = _make_superuser()
    record = Record.objects.create(title="Clearing")
    entry = StructuredFormat.objects.create(
        record=record,
        variant_of_format=1,
        carrier="Vinyl",
        quantity=1,
        format_name='12"',
        details="Album",
    )

    inline = StructuredFormatInline(Record, AdminSite())
    request = RequestFactory().post(f"/admin/records/record/{record.pk}/change/")
    request.user = admin_user
    formset_cls = inline.get_formset(request, record)
    prefix = formset_cls.get_default_prefix()
    formset = formset_cls(
        data={
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "1",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
            f"{prefix}-0-id": str(entry.pk),
            f"{prefix}-0-record": str(record.pk),
            f"{prefix}-0-carrier": "",
            f"{prefix}-0-quantity": "",
            f"{prefix}-0-format_name": "",
            f"{prefix}-0-details": "",
        },
        instance=record,
        prefix=prefix,
    )

    assert formset.is_valid()
    formset.save()
    entry.refresh_from_db()
    assert entry.variant_of_format == 1
    assert entry.carrier == ""
    assert entry.quantity == 1
    assert entry.format_name == ""
    assert entry.details == ""


@pytest.mark.django_db
def test_record_format_inline_formset_assigns_variant_of_format_to_first_manual_row() -> None:
    admin_user = _make_superuser()
    record = Record.objects.create(title="Manual structured row")

    inline = StructuredFormatInline(Record, AdminSite())
    request = RequestFactory().post(f"/admin/records/record/{record.pk}/change/")
    request.user = admin_user
    formset_cls = inline.get_formset(request, record)
    prefix = formset_cls.get_default_prefix()
    formset = formset_cls(
        data={
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
            f"{prefix}-0-id": "",
            f"{prefix}-0-record": str(record.pk),
            f"{prefix}-0-carrier": "Vinyl",
            f"{prefix}-0-quantity": "2",
            f"{prefix}-0-format_name": '12"',
            f"{prefix}-0-details": "Album",
        },
        instance=record,
        prefix=prefix,
    )

    assert formset.is_valid()
    formset.save()

    entry = record.structured_formats.get()
    assert entry.variant_of_format == 1
    assert entry.carrier == "Vinyl"
    assert entry.quantity == 2
    assert entry.format_name == '12"'
    assert entry.details == "Album"


@pytest.mark.django_db
def test_change_page_renders_structured_rows_in_discogs_order(client) -> None:
    admin_user = _make_superuser()
    client.force_login(admin_user)
    record = Record.objects.create(title="Rendered ordering")
    StructuredFormat.objects.create(
        record=record,
        variant_of_format=2,
        carrier="CD",
        quantity=1,
        format_name="Album",
        details="Second row",
    )
    StructuredFormat.objects.create(
        record=record,
        variant_of_format=1,
        carrier="Vinyl",
        quantity=1,
        format_name='12"',
        details="First row",
    )

    response = client.get(reverse("admin:records_record_change", args=[record.pk]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert '<col class="col-carrier">' in content
    assert '<col class="col-quantity">' in content
    assert '<col class="col-format_name">' in content
    assert '<col class="col-details">' in content
    assert content.index("First row") < content.index("Second row")


@pytest.mark.django_db
def test_change_page_shows_variant_selector_only_for_multiple_structured_formats(
    client,
) -> None:
    admin_user = _make_superuser()
    client.force_login(admin_user)
    record = Record.objects.create(
        title="Variant selector",
        active_structured_format_variant=2,
    )
    StructuredFormat.objects.create(
        record=record,
        variant_of_format=1,
        carrier="Vinyl",
        quantity=1,
        format_name='12"',
        details="First row",
    )
    StructuredFormat.objects.create(
        record=record,
        variant_of_format=2,
        carrier="CD",
        quantity=1,
        format_name="Album",
        details="Second row",
    )

    response = client.get(reverse("admin:records_record_change", args=[record.pk]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert '<col class="col-variant_of_format">' in content
    assert "structured-format-variant-select" in content
    assert 'name="active_structured_format_variant"' in content
    assert 'value="2"' in content


@pytest.mark.django_db
def test_change_page_preserves_selected_variant_after_save(client) -> None:
    admin_user = _make_superuser()
    client.force_login(admin_user)
    record = Record.objects.create(
        title="Persist variant",
        stock=1,
        availability_status="PREORDER",
        condition="SS",
        active_structured_format_variant=1,
    )
    format_obj, _ = Format.objects.get_or_create(name='12" Vinyl')
    record.formats.add(format_obj)
    first_variant = StructuredFormat.objects.create(
        record=record,
        variant_of_format=1,
        carrier="Vinyl",
        quantity=1,
        format_name='12"',
        details="Album",
    )
    second_variant = StructuredFormat.objects.create(
        record=record,
        variant_of_format=2,
        carrier="CD",
        quantity=1,
        format_name="Album",
        details="Promo",
    )

    response = client.post(
        reverse("admin:records_record_change", args=[record.pk]),
        {
            "title": record.title,
            "label": "",
            "release_year": "",
            "release_month": "",
            "release_day": "",
            "barcode": "",
            "catalog_number": "",
            "stock": "1",
            "availability_status": "PREORDER",
            "price": "",
            "condition": "SS",
            "notes": "",
            "formats": [str(format_obj.pk)],
            "active_structured_format_variant": "2",
            "structured_formats-TOTAL_FORMS": "2",
            "structured_formats-INITIAL_FORMS": "2",
            "structured_formats-MIN_NUM_FORMS": "0",
            "structured_formats-MAX_NUM_FORMS": "1000",
            "structured_formats-0-id": str(first_variant.pk),
            "structured_formats-0-record": str(record.pk),
            "structured_formats-0-carrier": "Vinyl",
            "structured_formats-0-quantity": "1",
            "structured_formats-0-format_name": '12"',
            "structured_formats-0-details": "Album",
            "structured_formats-1-id": str(second_variant.pk),
            "structured_formats-1-record": str(record.pk),
            "structured_formats-1-carrier": "CD",
            "structured_formats-1-quantity": "1",
            "structured_formats-1-format_name": "Album",
            "structured_formats-1-details": "Promo",
            "tracks-TOTAL_FORMS": "0",
            "tracks-INITIAL_FORMS": "0",
            "tracks-MIN_NUM_FORMS": "0",
            "tracks-MAX_NUM_FORMS": "0",
            "_save": "Save",
        },
    )

    assert response.status_code == 302

    record.refresh_from_db()
    assert record.active_structured_format_variant == 2

    reload_response = client.get(reverse("admin:records_record_change", args=[record.pk]))
    assert reload_response.status_code == 200
    assert 'value="2"' in reload_response.content.decode("utf-8")


@pytest.mark.django_db
def test_change_page_shows_error_for_incomplete_active_structured_format(client) -> None:
    admin_user = _make_superuser()
    client.force_login(admin_user)
    record = Record.objects.create(
        title="Invalid structured format",
        stock=1,
        availability_status="PREORDER",
        condition="SS",
        active_structured_format_variant=1,
    )
    format_obj, _ = Format.objects.get_or_create(name='12" Vinyl')
    record.formats.add(format_obj)
    variant = StructuredFormat.objects.create(
        record=record,
        variant_of_format=1,
        carrier="Vinyl",
        quantity=1,
        format_name='12"',
        details="Album",
    )

    response = client.post(
        reverse("admin:records_record_change", args=[record.pk]),
        {
            "title": record.title,
            "label": "",
            "release_year": "",
            "release_month": "",
            "release_day": "",
            "barcode": "",
            "catalog_number": "",
            "stock": "1",
            "availability_status": "PREORDER",
            "price": "",
            "condition": "SS",
            "notes": "",
            "formats": [str(format_obj.pk)],
            "active_structured_format_variant": "1",
            "structured_formats-TOTAL_FORMS": "1",
            "structured_formats-INITIAL_FORMS": "1",
            "structured_formats-MIN_NUM_FORMS": "0",
            "structured_formats-MAX_NUM_FORMS": "1000",
            "structured_formats-0-id": str(variant.pk),
            "structured_formats-0-record": str(record.pk),
            "structured_formats-0-carrier": "Vinyl",
            "structured_formats-0-quantity": "",
            "structured_formats-0-format_name": "",
            "structured_formats-0-details": "",
            "tracks-TOTAL_FORMS": "0",
            "tracks-INITIAL_FORMS": "0",
            "tracks-MIN_NUM_FORMS": "0",
            "tracks-MAX_NUM_FORMS": "0",
            "_save": "Save",
        },
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Поля структурированного формата заполнен не полностью." in content
    assert "Обязательны к заполнению:" in content
    assert "стандартном справочнике форматов." in content
