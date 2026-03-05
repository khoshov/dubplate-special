import uuid

import pytest

from records.models import AudioEnrichmentJob, AudioEnrichmentJobRecord, Record
from records.services.audio.providers.youtube_audio_enrichment import (
    PayloadValidationError,
    ProcessRecordPayload,
    RunJobPayload,
)
from records.services.tasks import (
    process_youtube_enrichment_record,
    run_youtube_enrichment_job,
)


def test_run_job_payload_contract_accepts_valid_payload():
    payload = RunJobPayload.from_dict(
        {
            "job_id": str(uuid.uuid4()),
            "record_ids": [1, 2],
            "overwrite_existing": True,
            "requested_by_user_id": None,
            "source": AudioEnrichmentJob.Source.MANUAL_LIST,
        }
    )
    assert payload.overwrite_existing is True
    assert payload.record_ids == (1, 2)
    assert payload.source == AudioEnrichmentJob.Source.MANUAL_LIST


def test_run_job_payload_contract_rejects_empty_record_ids():
    with pytest.raises(PayloadValidationError):
        RunJobPayload.from_dict(
            {
                "job_id": str(uuid.uuid4()),
                "record_ids": [],
                "overwrite_existing": True,
                "source": AudioEnrichmentJob.Source.MANUAL_LIST,
            }
        )


def test_process_record_payload_contract_accepts_valid_payload():
    payload = ProcessRecordPayload.from_dict(
        {
            "job_id": str(uuid.uuid4()),
            "record_id": 10,
            "overwrite_existing": False,
        }
    )
    assert payload.record_id == 10
    assert payload.overwrite_existing is False


@pytest.mark.django_db
def test_run_job_task_contract_enqueues_process_task(mocker):
    job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_LIST
    )
    record = Record.objects.create(title="Task Contract")
    delay_mock = mocker.patch(
        "records.services.tasks.process_youtube_enrichment_record.delay"
    )

    result = run_youtube_enrichment_job(
        {
            "job_id": str(job.id),
            "record_ids": [record.id],
            "overwrite_existing": True,
            "requested_by_user_id": None,
            "source": AudioEnrichmentJob.Source.MANUAL_LIST,
        }
    )

    job.refresh_from_db()
    job_record = AudioEnrichmentJobRecord.objects.get(job=job, record=record)

    assert result["queued_records"] == 1
    assert result["skipped_records"] == 0
    assert job.status == AudioEnrichmentJob.Status.RUNNING
    assert job_record.status == AudioEnrichmentJobRecord.Status.QUEUED
    delay_mock.assert_called_once()


@pytest.mark.django_db
def test_process_record_task_contract_marks_job_record_completed():
    job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_RECORD
    )
    record = Record.objects.create(title="Process Contract")
    job_record = AudioEnrichmentJobRecord.objects.create(
        job=job,
        record=record,
        status=AudioEnrichmentJobRecord.Status.QUEUED,
    )

    result = process_youtube_enrichment_record(
        {
            "job_id": str(job.id),
            "record_id": record.id,
            "overwrite_existing": True,
        }
    )

    job_record.refresh_from_db()
    assert result["status"] == AudioEnrichmentJobRecord.Status.COMPLETED
    assert job_record.status == AudioEnrichmentJobRecord.Status.COMPLETED
