from __future__ import annotations

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from records.admin.actions import update_audio_from_youtube
from records.admin.record_admin import RecordAdmin
from records.models import AudioEnrichmentJob, Record


def _build_action_request(user):
    request = RequestFactory().post("/admin/records/record/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
def test_update_audio_from_youtube_action_enqueues_job_and_renders_report_link(
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
    record_one = Record.objects.create(title="Record one")
    record_two = Record.objects.create(title="Record two")

    admin = RecordAdmin(Record, AdminSite())
    request = _build_action_request(user=user)
    queryset = Record.objects.filter(pk__in=[record_one.pk, record_two.pk])

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        update_audio_from_youtube(admin, request, queryset)

    job = AudioEnrichmentJob.objects.get()
    assert job.source == AudioEnrichmentJob.Source.MANUAL_LIST
    assert job.status == AudioEnrichmentJob.Status.QUEUED
    assert job.requested_by_user == user
    assert job.overwrite_existing is True
    assert job.total_records == 2
    assert len(callbacks) == 1

    delay_mock.assert_called_once()
    payload = delay_mock.call_args.args[0]
    assert payload["job_id"] == str(job.id)
    assert sorted(payload["record_ids"]) == sorted([record_one.pk, record_two.pk])
    assert payload["overwrite_existing"] is True
    assert payload["requested_by_user_id"] == user.id
    assert payload["source"] == AudioEnrichmentJob.Source.MANUAL_LIST

    messages_list = [str(message) for message in messages.get_messages(request)]
    assert any(
        "Поставлено в очередь YouTube-аудио-обогащение" in msg for msg in messages_list
    )
    assert any("Открыть job report" in msg for msg in messages_list)
    assert any(
        f"/admin/records/audioenrichmentjob/{job.id}/change/" in msg
        for msg in messages_list
    )
