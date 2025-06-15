import pytest
from records.admin import RecordAdmin, TrackInline
from records.models import Record, Track

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory


@pytest.fixture
def record_admin():
    return RecordAdmin(model=Record, admin_site=AdminSite())


@pytest.fixture
def track_inline():
    return TrackInline(Track, admin_site=AdminSite())


@pytest.mark.django_db
def test_track_inline(track_inline):
    assert track_inline.model == Track
    assert track_inline.extra == 0
    assert track_inline.readonly_fields == ("position", "title", "duration")
    assert track_inline.can_delete is False


@pytest.mark.django_db
def test_record_admin_get_fields(record_admin):
    factory = RequestFactory()
    request = factory.get("/admin/records/record/add/")

    # Для новой записи
    assert record_admin.get_fields(request) == ("barcode",)

    # Для существующей записи
    record = Record.objects.create(barcode="12345678")
    assert "barcode" in record_admin.get_fields(request, record)


@pytest.mark.django_db
def test_record_admin_inlines(record_admin):
    factory = RequestFactory()
    request = factory.get("/admin/records/record/add/")

    # Для новой записи
    assert len(record_admin.get_inline_instances(request)) == 0

    # Для существующей записи
    record = Record.objects.create(barcode="12345678")
    inlines = record_admin.get_inline_instances(request, record)
    assert len(inlines) == 1
    assert isinstance(inlines[0], TrackInline)
