from __future__ import annotations

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from records.api.v1.serializers import RecordSerializer
from records.models import Format, Record
from records.services.record_assembly import (
    ensure_active_structured_format_variant,
    ensure_legacy_formats,
    sync_format_state,
)


@pytest.mark.django_db
def test_sync_format_state_creates_structured_rows_and_default_legacy_format() -> None:
    record = Record.objects.create(title="Structured")

    sync_format_state(
        record,
        {
            "structured_formats": [
                {
                    "variant_of_format": 1,
                    "carrier": "Vinyl",
                    "quantity": 2,
                    "format_name": '12"',
                    "details": "Album",
                },
                {
                    "variant_of_format": 2,
                    "carrier": "CD",
                    "quantity": 1,
                    "format_name": "Album",
                    "details": "Reissue",
                },
            ]
        },
        preserve_existing_legacy_formats=False,
    )

    rows = list(record.structured_formats.order_by("variant_of_format", "id"))

    assert [
        (row.variant_of_format, row.carrier, row.quantity, row.format_name, row.details)
        for row in rows
    ] == [
        (1, "Vinyl", 2, '12"', "Album"),
        (2, "CD", 1, "Album", "Reissue"),
    ]
    assert record.active_structured_format_variant == 1
    assert list(record.formats.values_list("name", flat=True)) == ["Not specified"]


@pytest.mark.django_db
def test_sync_format_state_preserves_existing_legacy_formats_when_structured_rows_are_empty() -> (
    None
):
    record = Record.objects.create(title="Legacy fallback")
    format_obj, _ = Format.objects.get_or_create(name='12" Vinyl')
    record.formats.add(format_obj)

    sync_format_state(
        record,
        {"structured_formats": []},
        preserve_existing_legacy_formats=True,
    )

    assert record.structured_formats.count() == 0
    assert list(record.formats.values_list("name", flat=True)) == ['12" Vinyl']


@pytest.mark.django_db
def test_record_serializer_keeps_legacy_format_and_exposes_structured_formats() -> None:
    record = Record.objects.create(title="Serializer")
    sync_format_state(
        record,
        {
            "formats": ['12" Vinyl'],
            "structured_formats": [
                {
                    "variant_of_format": 1,
                    "carrier": "Vinyl",
                    "quantity": 1,
                    "format_name": '12"',
                    "details": "Album",
                }
            ],
        },
        preserve_existing_legacy_formats=False,
    )

    request = Request(APIRequestFactory().get("/api/v1/records/"))
    serializer = RecordSerializer(instance=record, context={"request": request})

    assert serializer.data["format"] == [
        {"id": record.formats.get(name='12" Vinyl').id, "name": '12" Vinyl'},
    ]
    assert serializer.data["active_structured_format_variant"] == 1
    assert serializer.data["structured_formats"] == [
        {
            "variant_of_format": 1,
            "carrier": "Vinyl",
            "quantity": 1,
            "format_name": '12"',
            "details": "Album",
        }
    ]


@pytest.mark.django_db
def test_sync_format_state_preserves_active_structured_variant_when_still_present() -> (
    None
):
    record = Record.objects.create(
        title="Selected variant",
        active_structured_format_variant=2,
    )

    sync_format_state(
        record,
        {
            "structured_formats": [
                {
                    "variant_of_format": 1,
                    "carrier": "Vinyl",
                    "quantity": 1,
                    "format_name": '12"',
                    "details": "Album",
                },
                {
                    "variant_of_format": 2,
                    "carrier": "CD",
                    "quantity": 1,
                    "format_name": "Album",
                    "details": "Promo",
                },
            ]
        },
        preserve_existing_legacy_formats=False,
    )

    record.refresh_from_db()
    assert record.active_structured_format_variant == 2


@pytest.mark.django_db
def test_ensure_active_structured_format_variant_clears_invalid_value_when_no_variants() -> (
    None
):
    record = Record.objects.create(
        title="No variants",
        active_structured_format_variant=3,
    )

    ensure_active_structured_format_variant(record)

    record.refresh_from_db()
    assert record.active_structured_format_variant is None


def test_selected_structured_format_variant_returns_none_for_unsaved_record() -> None:
    record = Record(title="Unsaved")

    assert record.selected_structured_format_variant() is None


def test_ensure_active_structured_format_variant_ignores_unsaved_record() -> None:
    record = Record(title="Unsaved", active_structured_format_variant=2)

    ensure_active_structured_format_variant(record)

    assert record.active_structured_format_variant == 2


def test_ensure_legacy_formats_ignores_unsaved_record() -> None:
    record = Record(title="Unsaved")

    ensure_legacy_formats(record)

    assert record.pk is None
