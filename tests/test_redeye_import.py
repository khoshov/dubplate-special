import pytest
from django.db import IntegrityError

from records.models import Record
from records.services.record_service import RecordService
from records.services import record_service as record_service_module


class DummyDiscogsService:
    pass


class DummyImageService:
    def download_cover(self, record, url):
        return False


class DummyRedeyeService:
    def fetch_by_catalog_number(self, catalog_number):
        return type("Result", (), {"payload": {}, "source_url": None})()


class DummyAudioService:
    def attach_audio_from_redeye(self, *args, **kwargs):
        raise AssertionError("attach_audio_from_redeye не должен вызываться")


@pytest.mark.django_db
def test_import_from_redeye_duplicate_catalog_number_returns_existing():
    existing = Record.objects.create(title="R1", catalog_number="RT123")

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_redeye(
        catalog_number="rt123", download_audio_decision=False
    )

    assert created is False
    assert record.pk == existing.pk


@pytest.mark.django_db
def test_import_from_redeye_integrity_error_returns_existing(monkeypatch):
    existing = Record.objects.create(title="R1", catalog_number="RT123")

    def raise_integrity(_payload):
        raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(
        record_service_module, "build_record_from_payload", raise_integrity
    )

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_redeye(
        catalog_number="RT123", download_audio_decision=False
    )

    assert created is False
    assert record.pk == existing.pk
