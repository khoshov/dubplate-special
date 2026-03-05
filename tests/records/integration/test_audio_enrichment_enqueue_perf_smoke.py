from __future__ import annotations

import time

import pytest

from records.models import AudioEnrichmentJob, Record
from records.services.tasks import run_youtube_enrichment_job


@pytest.mark.django_db
def test_audio_enrichment_enqueue_perf_smoke_for_100_records(mocker):
    records = [
        Record.objects.create(title=f"Perf record {index}") for index in range(100)
    ]
    job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_LIST,
        status=AudioEnrichmentJob.Status.QUEUED,
        overwrite_existing=True,
        total_records=100,
    )

    process_delay_mock = mocker.patch(
        "records.services.tasks.process_youtube_enrichment_record.delay"
    )

    started_at = time.perf_counter()
    result = run_youtube_enrichment_job(
        {
            "job_id": str(job.id),
            "record_ids": [record.id for record in records],
            "overwrite_existing": True,
            "requested_by_user_id": None,
            "source": AudioEnrichmentJob.Source.MANUAL_LIST,
        }
    )
    elapsed_sec = time.perf_counter() - started_at

    job.refresh_from_db()
    assert result["queued_records"] == 100
    assert result["skipped_records"] == 0
    assert process_delay_mock.call_count == 100
    assert job.status == AudioEnrichmentJob.Status.RUNNING
    assert elapsed_sec <= 2.0
