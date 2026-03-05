from __future__ import annotations

import pytest

from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentTrackResult,
    Artist,
    Record,
    Track,
)
from records.services.tasks import (
    process_youtube_enrichment_record,
    run_youtube_enrichment_job,
)


@pytest.mark.django_db
def test_audio_enrichment_success_criteria_for_30_discogs_records(mocker):
    artist = Artist.objects.create(name="Discogs Artist")
    records: list[Record] = []

    for index in range(30):
        record = Record.objects.create(title=f"Discogs record {index}")
        record.artists.add(artist)
        Track.objects.create(
            record=record,
            position="A1",
            position_index=1,
            title=f"Track {index}",
            youtube_url=f"https://youtu.be/sc_{index}",
        )
        records.append(record)

    failing_record_id = records[-1].id

    def _fake_download(track, url, **_kwargs):
        if track.record_id == failing_record_id:
            raise RuntimeError("Simulated external source failure")
        return f"records/track/audio_preview/{track.pk}.mp3"

    mocker.patch(
        "records.services.tasks.process_youtube_enrichment_record.delay",
        side_effect=lambda payload: process_youtube_enrichment_record(payload),
    )
    mocker.patch(
        "records.services.tasks.AudioService.download_audio_to_track",
        side_effect=_fake_download,
    )

    job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.DISCOGS_UPDATE,
        status=AudioEnrichmentJob.Status.QUEUED,
        overwrite_existing=False,
        total_records=30,
    )

    run_youtube_enrichment_job(
        {
            "job_id": str(job.id),
            "record_ids": [record.id for record in records],
            "overwrite_existing": False,
            "requested_by_user_id": None,
            "source": AudioEnrichmentJob.Source.DISCOGS_UPDATE,
        }
    )

    job.refresh_from_db()
    track_results = AudioEnrichmentTrackResult.objects.filter(job_record__job=job)
    updated_tracks = track_results.filter(
        status=AudioEnrichmentTrackResult.Status.UPDATED
    ).count()
    total_tracks = track_results.count()
    success_rate = updated_tracks / total_tracks

    assert total_tracks == 30
    assert updated_tracks == 29
    assert success_rate >= 0.95
    assert job.total_records == 30
    assert job.total_tracks == 30
    assert job.updated_count == 29
    assert job.error_count == 1
    assert job.status == AudioEnrichmentJob.Status.COMPLETED_WITH_ERRORS
