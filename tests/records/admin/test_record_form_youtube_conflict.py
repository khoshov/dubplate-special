from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.utils import timezone

from records.admin.record_admin import RecordAdmin
from records.models import AudioEnrichmentJob, AudioEnrichmentJobRecord, Record
from records.services.tasks import run_youtube_enrichment_job


def _build_post_request(*, user, path: str):
    request = RequestFactory().post(path)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
def test_record_form_youtube_refresh_marks_conflict_as_skipped(
    mocker,
    django_capture_on_commit_callbacks,
):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    record = Record.objects.create(title="Conflict test")

    active_job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_LIST,
        status=AudioEnrichmentJob.Status.RUNNING,
        overwrite_existing=True,
        total_records=1,
        started_at=timezone.now(),
    )
    AudioEnrichmentJobRecord.objects.create(
        job=active_job,
        record=record,
        status=AudioEnrichmentJobRecord.Status.RUNNING,
        started_at=timezone.now(),
    )

    process_delay_mock = mocker.patch(
        "records.services.tasks.process_youtube_enrichment_record.delay"
    )
    mocker.patch(
        "records.services.record_service.run_youtube_enrichment_job.delay",
        side_effect=lambda payload: run_youtube_enrichment_job(payload),
    )

    admin = RecordAdmin(Record, AdminSite())
    request = _build_post_request(
        user=user,
        path=f"/admin/records/record/{record.pk}/youtube-refresh/",
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = admin._refresh_youtube_audio_view(request, str(record.pk))

    assert response.status_code == 302

    new_job = AudioEnrichmentJob.objects.exclude(pk=active_job.pk).get()
    new_job.refresh_from_db()
    new_job_record = AudioEnrichmentJobRecord.objects.get(job=new_job, record=record)

    assert new_job.source == AudioEnrichmentJob.Source.MANUAL_RECORD
    assert new_job.status == AudioEnrichmentJob.Status.COMPLETED_WITH_ERRORS
    assert new_job_record.status == AudioEnrichmentJobRecord.Status.SKIPPED
    assert new_job_record.reason_code == AudioEnrichmentJobRecord.Reason.ALREADY_RUNNING
    process_delay_mock.assert_not_called()
