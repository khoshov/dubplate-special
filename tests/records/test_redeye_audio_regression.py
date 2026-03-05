import pytest

from records.models import AudioEnrichmentJob, Record
from records.services.record_service import RecordService


class DummyDiscogsService:
    pass


class DummyRedeyeService:
    pass


class DummyImageService:
    def download_cover(self, record, url):
        return False


class AudioServiceSpy:
    def __init__(self):
        self.calls = 0

    def attach_audio_from_redeye(self, *args, **kwargs):
        self.calls += 1
        return 1


@pytest.mark.django_db
def test_redeye_import_flow_does_not_trigger_discogs_youtube_enrichment(mocker):
    existing = Record.objects.create(title="Redeye record", catalog_number="RT123")
    audio_service = AudioServiceSpy()
    delay_mock = mocker.patch(
        "records.services.record_service.run_youtube_enrichment_job.delay"
    )

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=audio_service,
    )

    record, created = service.import_from_redeye(
        catalog_number="rt123",
        download_audio_decision=True,
    )

    assert created is False
    assert record.pk == existing.pk
    assert audio_service.calls == 1
    delay_mock.assert_not_called()
    assert AudioEnrichmentJob.objects.count() == 0
