import pytest

from records.models import Record, RecordSource
from records.services import record_service as record_service_module
from records.services.record_service import RecordService


class DummyImageService:
    def download_cover(self, record, url):
        return False


class DummyRedeyeService:
    pass


class DummyAudioService:
    pass


class DiscogsImportStub:
    def __init__(self, release):
        self.release = release

    def search_by_barcode(self, barcode):
        return self.release

    def search_by_catalog_number(self, catalog_number):
        return self.release


class DiscogsUpdateStub:
    def __init__(self, release):
        self.release = release

    def get_release(self, discogs_id):
        return self.release


class DummyRelease:
    def __init__(self, release_id=None, resource_url=None, data=None):
        self.id = release_id
        self.resource_url = resource_url
        self.data = data or {}
        self.images = []


@pytest.mark.django_db
def test_import_from_discogs_creates_discogs_record_source(monkeypatch):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Discogs Import",
            "discogs_id": 321,
            "release_year": 2025,
            "release_month": 9,
            "release_day": 26,
            "tracks": [],
        },
    )

    service = RecordService(
        discogs_service=DiscogsImportStub(
            DummyRelease(
                release_id=321,
                resource_url="https://api.discogs.com/releases/321",
            )
        ),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_discogs(
        barcode="1234567890123", save_image=False
    )
    source = RecordSource.objects.get(
        record=record,
        provider=RecordSource.Provider.DISCOGS,
        role=RecordSource.Role.API,
    )

    assert created is True
    assert record.discogs_id == 321
    assert record.release_year == 2025
    assert record.release_month == 9
    assert record.release_day == 26
    assert source.url == "https://api.discogs.com/releases/321"
    assert source.can_fetch_audio is False


@pytest.mark.django_db
def test_update_from_discogs_updates_source_with_release_id_fallback(monkeypatch):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Updated from Discogs",
            "discogs_id": 777,
            "release_year": 2025,
            "release_month": 9,
            "release_day": 26,
            "tracks": [],
        },
    )

    record = Record.objects.create(
        title="Initial Title",
        discogs_id=777,
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    existing_source = RecordSource.objects.create(
        record=record,
        provider=RecordSource.Provider.DISCOGS,
        role=RecordSource.Role.API,
        url="https://api.discogs.com/releases/old",
        can_fetch_audio=True,
    )

    service = RecordService(
        discogs_service=DiscogsUpdateStub(DummyRelease(release_id=None, data={})),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    updated_record = service.update_from_discogs(record, update_image=False)
    source = RecordSource.objects.get(
        record=record,
        provider=RecordSource.Provider.DISCOGS,
        role=RecordSource.Role.API,
    )

    assert source.pk == existing_source.pk
    assert source.url == "https://api.discogs.com/releases/777"
    assert source.can_fetch_audio is False
    assert updated_record.title == "Updated from Discogs"
    assert updated_record.discogs_id == 777
    assert updated_record.release_year == 2025
    assert updated_record.release_month == 9
    assert updated_record.release_day == 26
