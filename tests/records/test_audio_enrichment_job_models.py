import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction

from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Record,
    Track,
)


@pytest.mark.django_db
def test_audio_enrichment_active_record_constraint_blocks_parallel_jobs():
    record = Record.objects.create(title="Lock Test")
    job_1 = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_LIST
    )
    job_2 = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_LIST
    )

    first_job_record = AudioEnrichmentJobRecord.objects.create(
        job=job_1,
        record=record,
        status=AudioEnrichmentJobRecord.Status.QUEUED,
    )

    with transaction.atomic():
        with pytest.raises(IntegrityError):
            AudioEnrichmentJobRecord.objects.create(
                job=job_2,
                record=record,
                status=AudioEnrichmentJobRecord.Status.RUNNING,
            )

    first_job_record.status = AudioEnrichmentJobRecord.Status.COMPLETED
    first_job_record.save(update_fields=["status", "modified"])

    second_job_record = AudioEnrichmentJobRecord.objects.create(
        job=job_2,
        record=record,
        status=AudioEnrichmentJobRecord.Status.QUEUED,
    )
    assert second_job_record.status == AudioEnrichmentJobRecord.Status.QUEUED


@pytest.mark.django_db
def test_audio_enrichment_track_result_attempts_validation():
    record = Record.objects.create(title="Attempts Test")
    track = Track.objects.create(record=record, title="Track A", position_index=1)
    job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_RECORD
    )
    job_record = AudioEnrichmentJobRecord.objects.create(
        job=job,
        record=record,
        status=AudioEnrichmentJobRecord.Status.COMPLETED,
    )

    track_result = AudioEnrichmentTrackResult(
        job_record=job_record,
        track=track,
        status=AudioEnrichmentTrackResult.Status.FAILED,
        reason_code=AudioEnrichmentTrackResult.Reason.RETRY_EXHAUSTED,
        attempts=4,
    )

    with pytest.raises(ValidationError):
        track_result.full_clean()
