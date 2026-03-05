from __future__ import annotations

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from records.admin.record_admin import RecordAdmin
from records.models import AudioEnrichmentJob, Record


def _build_post_request(*, user, path: str):
    request = RequestFactory().post(path)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
def test_record_form_youtube_refresh_enqueues_single_record_job(
    mocker,
    django_capture_on_commit_callbacks,
):
    delay_mock = mocker.patch(
        "records.services.record_service.run_youtube_enrichment_job.delay"
    )

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    record = Record.objects.create(title="Single refresh")
    admin = RecordAdmin(Record, AdminSite())
    request = _build_post_request(
        user=user,
        path=f"/admin/records/record/{record.pk}/youtube-refresh/",
    )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        response = admin._refresh_youtube_audio_view(request, str(record.pk))

    job = AudioEnrichmentJob.objects.get()
    assert response.status_code == 302
    assert response.url.endswith(f"/admin/records/record/{record.pk}/change/")
    assert len(callbacks) == 1

    assert job.source == AudioEnrichmentJob.Source.MANUAL_RECORD
    assert job.status == AudioEnrichmentJob.Status.QUEUED
    assert job.requested_by_user == user
    assert job.total_records == 1

    delay_mock.assert_called_once()
    payload = delay_mock.call_args.args[0]
    assert payload["job_id"] == str(job.id)
    assert payload["record_ids"] == [record.pk]
    assert payload["source"] == AudioEnrichmentJob.Source.MANUAL_RECORD

    messages_list = [str(message) for message in messages.get_messages(request)]
    assert any(
        "Поставлено в очередь YouTube-аудио-обогащение" in msg for msg in messages_list
    )
    assert any("Открыть job report" in msg for msg in messages_list)
