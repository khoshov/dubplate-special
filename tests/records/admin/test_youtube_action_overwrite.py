from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from records.admin.actions import update_audio_from_youtube
from records.admin.record_admin import RecordAdmin
from records.models import (
    Artist,
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Record,
    Track,
)
from records.services.tasks import (
    process_youtube_enrichment_record,
    run_youtube_enrichment_job,
)


def _build_action_request(user):
    request = RequestFactory().post("/admin/records/record/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
def test_update_audio_from_youtube_action_uses_overwrite_mode(
    mocker,
    django_capture_on_commit_callbacks,
):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    artist = Artist.objects.create(name="Artist one")
    record = Record.objects.create(title="Overwrite record")
    record.artists.add(artist)
    track = Track.objects.create(
        record=record,
        position="A1",
        position_index=1,
        title="Track one",
        youtube_url="https://youtu.be/abc123",
        audio_preview="records/track/audio_preview/old.mp3",
    )

    overwrite_values: list[bool] = []

    def _run_now(payload):
        return run_youtube_enrichment_job(payload)

    def _process_now(payload):
        return process_youtube_enrichment_record(payload)

    def _fake_download(track, url, *, overwrite=False, **_kwargs):
        overwrite_values.append(overwrite)
        return "records/track/audio_preview/new.mp3"

    mocker.patch(
        "records.services.record_service.run_youtube_enrichment_job.delay",
        side_effect=_run_now,
    )
    mocker.patch(
        "records.services.tasks.process_youtube_enrichment_record.delay",
        side_effect=_process_now,
    )
    mocker.patch(
        "records.services.audio.audio_service.AudioService.download_audio_to_track",
        side_effect=_fake_download,
    )

    admin = RecordAdmin(Record, AdminSite())
    request = _build_action_request(user=user)
    with django_capture_on_commit_callbacks(execute=True):
        update_audio_from_youtube(admin, request, Record.objects.filter(pk=record.pk))

    job = AudioEnrichmentJob.objects.get()
    job.refresh_from_db()
    job_record = AudioEnrichmentJobRecord.objects.get(job=job, record=record)
    track_result = AudioEnrichmentTrackResult.objects.get(
        job_record=job_record, track=track
    )

    assert overwrite_values == [True]
    assert job.overwrite_existing is True
    assert job.status == AudioEnrichmentJob.Status.COMPLETED
    assert track_result.status == AudioEnrichmentTrackResult.Status.UPDATED
    assert track_result.previous_audio_present is True
    assert track_result.final_audio_name == "records/track/audio_preview/new.mp3"
