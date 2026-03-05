import pytest

from records.models import AudioEnrichmentJob, Record
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


class DiscogsUpdateStub:
    def __init__(self, release):
        self.release = release

    def get_release(self, discogs_id):
        return self.release


@pytest.mark.django_db
def test_update_from_discogs_enqueues_youtube_enrichment_job(
    mocker,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Updated title",
            "artists": ["Artist B"],
            "tracks": [
                {
                    "title": "Track 1",
                    "position": "A1",
                    "duration": "03:33",
                    "youtube_url": "https://www.youtube.com/watch?v=abc123",
                }
            ],
        },
    )
    delay_mock = mocker.patch(
        "records.services.record_service.run_youtube_enrichment_job.delay"
    )

    record = Record.objects.create(title="Before update", discogs_id=777)
    service = RecordService(
        discogs_service=DiscogsUpdateStub(DummyRelease(release_id=777)),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        updated_record = service.update_from_discogs(record=record, update_image=False)

    assert updated_record.title == "Updated title"
    assert len(callbacks) == 1
    job = AudioEnrichmentJob.objects.get()
    assert job.source == AudioEnrichmentJob.Source.DISCOGS_UPDATE
    assert job.status == AudioEnrichmentJob.Status.QUEUED
    assert str(job.id) == getattr(updated_record, "_discogs_enrichment_job_id")

    delay_mock.assert_called_once()
    payload = delay_mock.call_args.args[0]
    assert payload["job_id"] == str(job.id)
    assert payload["record_ids"] == [record.id]
    assert payload["source"] == AudioEnrichmentJob.Source.DISCOGS_UPDATE
