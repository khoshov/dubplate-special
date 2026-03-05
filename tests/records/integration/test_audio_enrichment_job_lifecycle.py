from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from records.admin.actions import update_audio_from_youtube
from records.admin.record_admin import RecordAdmin
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


def _build_action_request(user):
    request = RequestFactory().post("/admin/records/record/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _build_record_request(*, user, path: str):
    request = RequestFactory().post(path)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
def test_audio_enrichment_job_lifecycle_for_discogs_list_and_record_flows(
    mocker,
    django_capture_on_commit_callbacks,
):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    artist = Artist.objects.create(name="Lifecycle Artist")
    record = Record.objects.create(title="Lifecycle Record")
    record.artists.add(artist)
    Track.objects.create(
        record=record,
        position="A1",
        position_index=1,
        title="Lifecycle Track",
        youtube_url="https://youtu.be/lifecycle1",
    )

    mocker.patch(
        "records.services.record_service.run_youtube_enrichment_job.delay",
        side_effect=lambda payload: run_youtube_enrichment_job(payload),
    )
    mocker.patch(
        "records.services.tasks.process_youtube_enrichment_record.delay",
        side_effect=lambda payload: process_youtube_enrichment_record(payload),
    )
    mocker.patch(
        "records.services.tasks.AudioService.download_audio_to_track",
        side_effect=lambda track, url, **kwargs: (
            f"records/track/audio_preview/{track.pk}_{'ow' if kwargs.get('overwrite') else 'no'}.mp3"
        ),
    )

    admin = RecordAdmin(Record, AdminSite())

    with django_capture_on_commit_callbacks(execute=True):
        discogs_job = admin.record_service.enqueue_discogs_audio_enrichment(
            record=record,
            requested_by_user_id=user.id,
        )
    discogs_job.refresh_from_db()

    with django_capture_on_commit_callbacks(execute=True):
        update_audio_from_youtube(
            admin,
            _build_action_request(user=user),
            Record.objects.filter(pk=record.pk),
        )
    list_job = AudioEnrichmentJob.objects.filter(
        source=AudioEnrichmentJob.Source.MANUAL_LIST
    ).latest("created")
    list_job.refresh_from_db()

    with django_capture_on_commit_callbacks(execute=True):
        admin._refresh_youtube_audio_view(
            _build_record_request(
                user=user,
                path=f"/admin/records/record/{record.pk}/youtube-refresh/",
            ),
            str(record.pk),
        )
    record_job = AudioEnrichmentJob.objects.filter(
        source=AudioEnrichmentJob.Source.MANUAL_RECORD
    ).latest("created")
    record_job.refresh_from_db()

    for job in (discogs_job, list_job, record_job):
        assert job.status == AudioEnrichmentJob.Status.COMPLETED
        assert job.started_at is not None
        assert job.finished_at is not None
        assert job.total_records == 1
        assert job.total_tracks == 1
        assert job.updated_count == 1
        assert job.skipped_count == 0
        assert job.error_count == 0
        assert (
            AudioEnrichmentTrackResult.objects.filter(
                job_record__job=job,
                status=AudioEnrichmentTrackResult.Status.UPDATED,
            ).count()
            == 1
        )
