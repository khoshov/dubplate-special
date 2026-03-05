import pytest

from records.models import AudioEnrichmentJob
from records.services import record_service as record_service_module
from records.services.record_service import RecordService


class DummyImageService:
    def download_cover(self, record, url):
        return False


class DummyRedeyeService:
    pass


class DummyAudioService:
    pass


class DummyRelease:
    def __init__(self, release_id: int):
        self.id = release_id
        self.resource_url = f"https://api.discogs.com/releases/{release_id}"
        self.images = []
        self.data = {}


class DiscogsImportStub:
    def __init__(self, release):
        self.release = release

    def search_by_barcode(self, barcode):
        return self.release

    def search_by_catalog_number(self, catalog_number):
        return self.release


@pytest.mark.django_db
def test_import_from_discogs_enqueues_youtube_enrichment_job(
    mocker,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Discogs import title",
            "artists": ["Artist A"],
            "tracks": [
                {
                    "title": "Track 1",
                    "position": "A1",
                    "duration": "03:00",
                    "youtube_url": "https://youtu.be/abc123",
                }
            ],
        },
    )
    delay_mock = mocker.patch(
        "records.services.record_service.run_youtube_enrichment_job.delay"
    )

    service = RecordService(
        discogs_service=DiscogsImportStub(DummyRelease(release_id=321)),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        record, created = service.import_from_discogs(
            barcode="1234567890123",
            save_image=False,
        )

    assert created is True
    assert len(callbacks) == 1
    job = AudioEnrichmentJob.objects.get()
    assert job.source == AudioEnrichmentJob.Source.DISCOGS_UPDATE
    assert job.status == AudioEnrichmentJob.Status.QUEUED
    assert job.overwrite_existing is False

    delay_mock.assert_called_once()
    payload = delay_mock.call_args.args[0]
    assert payload["job_id"] == str(job.id)
    assert payload["record_ids"] == [record.id]
    assert payload["overwrite_existing"] is False
    assert payload["source"] == AudioEnrichmentJob.Source.DISCOGS_UPDATE
