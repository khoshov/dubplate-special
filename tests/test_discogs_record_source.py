import pytest
from django.core.files.base import ContentFile

from records.models import Artist, Format, Label, Record, RecordSource, Track
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
            "structured_formats": [
                {
                    "variant_of_format": 1,
                    "carrier": "Vinyl",
                    "quantity": 1,
                    "format_name": '12"',
                    "details": "Album",
                }
            ],
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
    assert list(
        record.structured_formats.values_list(
            "variant_of_format", "carrier", "quantity", "format_name", "details"
        )
    ) == [(1, "Vinyl", 1, '12"', "Album")]
    assert record.active_structured_format_variant == 1
    assert list(record.formats.values_list("name", flat=True)) == ["Not specified"]
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
            "structured_formats": [
                {
                    "variant_of_format": 1,
                    "carrier": "CD",
                    "quantity": 1,
                    "format_name": "Album",
                    "details": "Reissue",
                }
            ],
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
    assert list(
        updated_record.structured_formats.values_list(
            "variant_of_format", "carrier", "quantity", "format_name", "details"
        )
    ) == [(1, "CD", 1, "Album", "Reissue")]
    assert updated_record.active_structured_format_variant == 1
    assert list(updated_record.formats.values_list("name", flat=True)) == [
        "Not specified"
    ]


@pytest.mark.django_db
def test_import_from_discogs_does_not_store_none_like_catalog_number(monkeypatch):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Discogs Import",
            "discogs_id": 654,
            "catalog_number": " none ",
            "release_year": 2025,
            "release_month": 9,
            "release_day": 26,
            "structured_formats": [],
            "tracks": [],
        },
    )

    service = RecordService(
        discogs_service=DiscogsImportStub(
            DummyRelease(
                release_id=654,
                resource_url="https://api.discogs.com/releases/654",
            )
        ),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_discogs(
        barcode="1234567890123", save_image=False
    )

    assert created is True
    assert record.catalog_number is None


@pytest.mark.django_db
def test_update_from_discogs_does_not_overwrite_catalog_number_with_none_like_value(
    monkeypatch,
):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Updated from Discogs",
            "discogs_id": 777,
            "catalog_number": "NONE",
            "release_year": 2025,
            "release_month": 9,
            "release_day": 26,
            "structured_formats": [],
            "tracks": [],
        },
    )

    record = Record.objects.create(
        title="Initial Title",
        discogs_id=777,
        catalog_number="SP34",
        release_year=2000,
        release_month=1,
        release_day=1,
    )

    service = RecordService(
        discogs_service=DiscogsUpdateStub(
            DummyRelease(release_id=777, data={"id": 777})
        ),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    updated_record = service.update_from_discogs(record, update_image=False)

    assert updated_record.catalog_number == "SP34"


@pytest.mark.django_db
def test_update_from_discogs_rebuilds_structured_formats_and_preserves_legacy_when_payload_empty(
    monkeypatch,
):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Updated from Discogs",
            "discogs_id": 888,
            "release_year": 2025,
            "release_month": 9,
            "release_day": 26,
            "structured_formats": [],
            "tracks": [],
        },
    )

    record = Record.objects.create(
        title="Initial Title",
        discogs_id=888,
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    format_obj, _ = Format.objects.get_or_create(name='12" Vinyl')
    record.formats.add(format_obj)
    record.structured_formats.create(
        variant_of_format=1,
        carrier="Vinyl",
        quantity=1,
        format_name='12"',
        details="Old",
    )

    service = RecordService(
        discogs_service=DiscogsUpdateStub(
            DummyRelease(release_id=888, data={"id": 888})
        ),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    updated_record = service.update_from_discogs(record, update_image=False)

    assert updated_record.structured_formats.count() == 0
    assert updated_record.active_structured_format_variant is None
    assert list(updated_record.formats.values_list("name", flat=True)) == ['12" Vinyl']


@pytest.mark.django_db
def test_update_from_discogs_reuses_existing_suffixed_artist_label_and_does_not_create_duplicates(
    monkeypatch,
):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Updated from Discogs",
            "discogs_id": 990,
            "label": "Kong",
            "artists": ["Jerome"],
            "release_year": 2025,
            "release_month": 9,
            "release_day": 26,
            "structured_formats": [],
            "tracks": [],
        },
    )

    legacy_artist = Artist.objects.create(name="Jerome (24)")
    legacy_label = Label.objects.create(name="Kong (7)")
    stale_artist = Artist.objects.create(name="Stale Artist")
    record = Record.objects.create(
        title="Initial Title",
        discogs_id=990,
        label=legacy_label,
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    record.artists.add(legacy_artist, stale_artist)

    service = RecordService(
        discogs_service=DiscogsUpdateStub(
            DummyRelease(release_id=990, data={"id": 990})
        ),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    updated_record = service.update_from_discogs(record, update_image=False)
    updated_record.refresh_from_db()

    assert Label.objects.count() == 1
    assert Artist.objects.count() == 2
    assert Label.objects.filter(name="Kong").exists()
    assert Artist.objects.filter(name="Jerome").exists()
    assert not Label.objects.filter(name="Kong (7)").exists()
    assert not Artist.objects.filter(name="Jerome (24)").exists()
    assert updated_record.label is not None
    assert updated_record.label.name == "Kong"
    assert list(updated_record.artists.values_list("name", flat=True)) == ["Jerome"]


@pytest.mark.django_db
def test_update_from_discogs_preserves_existing_track_audio_preview(monkeypatch):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Updated from Discogs",
            "discogs_id": 991,
            "release_year": 2025,
            "release_month": 9,
            "release_day": 26,
            "structured_formats": [],
            "tracks": [
                {
                    "position": "A1",
                    "title": "Keep Me",
                    "duration": "03:00",
                    "youtube_url": "https://www.youtube.com/watch?v=keep",
                },
                {
                    "position": "A2",
                    "title": "New Track",
                    "duration": "02:00",
                    "youtube_url": "https://www.youtube.com/watch?v=new",
                },
            ],
        },
    )

    record = Record.objects.create(
        title="Initial Title",
        discogs_id=991,
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    old_track = Track.objects.create(
        record=record,
        position="A1",
        position_index=1,
        title="Keep Me",
        duration="03:00",
    )
    old_track.audio_preview.save("keep-me.mp3", ContentFile(b"mp3"), save=True)
    old_audio_name = old_track.audio_preview.name

    service = RecordService(
        discogs_service=DiscogsUpdateStub(
            DummyRelease(release_id=991, data={"id": 991})
        ),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    updated_record = service.update_from_discogs(record, update_image=False)
    updated_track = updated_record.tracks.get(title="Keep Me")
    new_track = updated_record.tracks.get(title="New Track")

    assert updated_track.audio_preview.name == old_audio_name
    assert updated_track.youtube_url == "https://www.youtube.com/watch?v=keep"
    assert not new_track.audio_preview
