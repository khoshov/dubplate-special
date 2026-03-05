import pytest

from records.models import (
    Artist,
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Record,
    Track,
)
from records.services.tasks import process_youtube_enrichment_record


@pytest.mark.django_db
def test_failed_track_persists_attempts_and_reason_in_job_report(mocker):
    record = Record.objects.create(title="Attempts visibility")
    artist = Artist.objects.create(name="Artist A")
    record.artists.add(artist)
    track = Track.objects.create(
        record=record,
        title="Track 1",
        position_index=1,
        youtube_url="https://youtu.be/abc123",
    )
    job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_RECORD
    )

    mocker.patch(
        "records.services.tasks.AudioService.download_audio_to_track",
        return_value=None,
    )

    result = process_youtube_enrichment_record(
        {
            "job_id": str(job.id),
            "record_id": record.id,
            "overwrite_existing": True,
        }
    )

    job.refresh_from_db()
    job_record = AudioEnrichmentJobRecord.objects.get(job=job, record=record)
    track_result = AudioEnrichmentTrackResult.objects.get(
        job_record=job_record, track=track
    )

    assert result["status"] == AudioEnrichmentJobRecord.Status.COMPLETED_WITH_ERRORS
    assert track_result.status == AudioEnrichmentTrackResult.Status.FAILED
    assert track_result.reason_code == AudioEnrichmentTrackResult.Reason.RETRY_EXHAUSTED
    assert track_result.attempts == 3
    assert job_record.error_count == 1
    assert job.error_count == 1
