from __future__ import annotations

import html
import uuid

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from records.admin.actions import find_audio_on_youtube, update_audio_from_youtube
from records.admin.record_admin import RecordAdmin
from records.models import Record, YouTubeSessionState
from records.services.tasks import (
    login_youtube_session_profile,
    refresh_youtube_session_profile,
)
from records.templatetags.records_admin import youtube_session_banner


class FakeAdmin:
    def __init__(self, record_service):
        self.record_service = record_service
        self.messages: list[tuple[str, int]] = []

    def message_user(self, request, message, level):
        self.messages.append((str(message), level))


class FakeUser:
    def __init__(self, user_id: int = 501):
        self.id = user_id
        self.username = "youtube-admin"
        self.is_active = True
        self.is_staff = True
        self.is_authenticated = True


class RecordingListActionService:
    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        self.calls: list[dict[str, object]] = []

    def enqueue_manual_youtube_audio_enrichment(
        self,
        *,
        record_ids: list[int],
        requested_by_user_id: int | None = None,
    ):
        self.calls.append(
            {
                "record_ids": record_ids,
                "requested_by_user_id": requested_by_user_id,
            }
        )
        return type("Job", (), {"id": self.job_id})()


class RecordingSingleRecordService:
    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        self.calls: list[dict[str, object]] = []

    def enqueue_record_youtube_audio_enrichment(
        self,
        *,
        record,
        requested_by_user_id: int | None = None,
    ):
        self.calls.append(
            {
                "record_id": record.pk,
                "requested_by_user_id": requested_by_user_id,
            }
        )
        return type("Job", (), {"id": self.job_id})()


class RecordingTrackService:
    def __init__(self, job_id: uuid.UUID):
        self.job_id = job_id
        self.calls: list[dict[str, object]] = []

    def enqueue_track_youtube_audio_enrichment(
        self,
        *,
        track,
        requested_by_user_id: int | None = None,
        overwrite_existing: bool = False,
    ):
        self.calls.append(
            {
                "track_id": track.pk,
                "requested_by_user_id": requested_by_user_id,
                "overwrite_existing": overwrite_existing,
            }
        )
        return type("Job", (), {"id": self.job_id})()


class RecordingSearchService:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def enqueue_record_youtube_audio_search(
        self,
        *,
        record,
        requested_by_user_id: int | None = None,
    ) -> None:
        self.calls.append(
            {
                "record_id": record.pk,
                "requested_by_user_id": requested_by_user_id,
            }
        )


def _attach_session_and_messages(request) -> None:
    session_middleware = SessionMiddleware(lambda req: None)
    session_middleware.process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)


@pytest.mark.django_db
def test_update_audio_from_youtube_action_enqueues_manual_job():
    record_one = Record.objects.create(title="R1")
    record_two = Record.objects.create(title="R2")
    queryset = Record.objects.filter(pk__in=[record_one.pk, record_two.pk]).order_by(
        "pk"
    )
    job_id = uuid.uuid4()
    record_service = RecordingListActionService(job_id=job_id)
    admin = FakeAdmin(record_service=record_service)
    request = RequestFactory().post("/admin/records/record/")
    request.user = FakeUser()

    update_audio_from_youtube(
        admin_obj=admin,  # type: ignore[arg-type]
        request=request,  # type: ignore[arg-type]
        queryset=queryset,
    )

    messages_text = html.unescape("\n".join(msg for msg, _ in admin.messages))

    assert record_service.calls == [
        {
            "record_ids": [record_one.pk, record_two.pk],
            "requested_by_user_id": request.user.id,
        }
    ]
    assert any(level == messages.SUCCESS for _, level in admin.messages)
    assert "для 2 записей" in messages_text
    assert f"/admin/records/audioenrichmentjob/{job_id}/change/" in messages_text


@pytest.mark.django_db
def test_record_admin_youtube_refresh_view_enqueues_single_record_job(monkeypatch):
    record = Record.objects.create(title="Single Record")
    admin = RecordAdmin(Record, AdminSite())
    job_id = uuid.uuid4()
    admin.record_service = RecordingSingleRecordService(job_id=job_id)
    monkeypatch.setattr(admin, "has_change_permission", lambda request, obj=None: True)

    request = RequestFactory().post(
        f"/admin/records/record/{record.pk}/youtube-refresh/"
    )
    request.user = FakeUser()
    _attach_session_and_messages(request)

    response = admin._refresh_youtube_audio_view(request, str(record.pk))

    rendered_messages = [str(msg) for msg in messages.get_messages(request)]

    assert response.status_code == 302
    assert response["Location"].endswith(f"/admin/records/record/{record.pk}/change/")
    assert admin.record_service.calls == [
        {
            "record_id": record.pk,
            "requested_by_user_id": request.user.id,
        }
    ]
    assert any(
        "Поставлена в очередь задача обновления аудио треков из YouTube." in msg
        for msg in rendered_messages
    )
    assert any(
        f"/admin/records/audioenrichmentjob/{job_id}/change/" in msg
        for msg in rendered_messages
    )


@pytest.mark.django_db
def test_record_admin_track_enqueue_mp3_view_enqueues_job(monkeypatch):
    record = Record.objects.create(title="Single Record")
    track = record.tracks.create(title="Track 1", youtube_url="https://youtu.be/abc")
    admin = RecordAdmin(Record, AdminSite())
    job_id = uuid.uuid4()
    admin.record_service = RecordingTrackService(job_id=job_id)
    monkeypatch.setattr(admin, "has_change_permission", lambda request, obj=None: True)

    request = RequestFactory().post(
        f"/admin/records/record/{record.pk}/tracks/{track.pk}/enqueue-mp3/"
    )
    request.user = FakeUser()
    _attach_session_and_messages(request)

    response = admin.enqueue_track_mp3_view(request, str(record.pk), str(track.pk))

    assert response.status_code == 200
    assert admin.record_service.calls == [
        {
            "track_id": track.pk,
            "requested_by_user_id": request.user.id,
            "overwrite_existing": False,
        }
    ]


@pytest.mark.django_db
def test_record_admin_youtube_search_view_enqueues_task(monkeypatch):
    record = Record.objects.create(title="Search Record")
    record.tracks.create(title="Track 1", youtube_url=None)
    admin = RecordAdmin(Record, AdminSite())
    record_service = RecordingSearchService()
    admin.record_service = record_service
    monkeypatch.setattr(admin, "has_change_permission", lambda request, obj=None: True)

    request = RequestFactory().post(
        f"/admin/records/record/{record.pk}/youtube-search/"
    )
    request.user = FakeUser()
    request.headers = {"x-requested-with": "XMLHttpRequest"}
    _attach_session_and_messages(request)

    response = admin._search_youtube_audio_view(request, str(record.pk))

    assert response.status_code == 200
    assert record_service.calls == [
        {
            "record_id": record.pk,
            "requested_by_user_id": request.user.id,
        }
    ]


@pytest.mark.django_db
def test_find_audio_on_youtube_action_enqueues_for_missing_only():
    record_with_missing = Record.objects.create(title="Missing")
    record_with_missing.tracks.create(title="No URL", youtube_url=None)
    record_with_urls = Record.objects.create(title="Filled")
    record_with_urls.tracks.create(title="Has URL", youtube_url="https://youtu.be/demo")

    record_service = RecordingSearchService()
    admin = FakeAdmin(record_service=record_service)
    request = RequestFactory().post("/admin/records/record/")
    request.user = FakeUser()

    queryset = Record.objects.filter(
        pk__in=[record_with_missing.pk, record_with_urls.pk]
    ).order_by("pk")
    find_audio_on_youtube(
        admin_obj=admin,  # type: ignore[arg-type]
        request=request,  # type: ignore[arg-type]
        queryset=queryset,
    )

    assert record_service.calls == [
        {
            "record_id": record_with_missing.pk,
            "requested_by_user_id": request.user.id,
        }
    ]


@pytest.mark.django_db
def test_record_admin_youtube_session_refresh_view_enqueues_task(monkeypatch):
    admin = RecordAdmin(Record, AdminSite())
    queued: list[str] = []
    monkeypatch.setattr(
        refresh_youtube_session_profile,
        "delay",
        lambda: queued.append("refresh"),
    )

    request = RequestFactory().post("/admin/records/record/youtube-session/refresh/")
    request.user = FakeUser()
    request.META["HTTP_REFERER"] = "/admin/records/record/"
    _attach_session_and_messages(request)

    response = admin._refresh_youtube_session_view(request)
    rendered_messages = [str(msg) for msg in messages.get_messages(request)]

    assert response.status_code == 302
    assert response["Location"] == "/admin/records/record/"
    assert queued == ["refresh"]
    assert any(
        "Поставлена в очередь задача обновления YouTube-сессии." in msg
        for msg in rendered_messages
    )


@pytest.mark.django_db
def test_record_admin_youtube_session_login_view_enqueues_task(monkeypatch, settings):
    admin = RecordAdmin(Record, AdminSite())
    queued: list[int] = []
    settings.YOUTUBE_SESSION_UI_URL = "http://localhost:6080/vnc.html"
    settings.YOUTUBE_SESSION_LOGIN_TIMEOUT_MS = 120_000
    monkeypatch.setattr(
        login_youtube_session_profile,
        "delay",
        lambda **kwargs: queued.append(kwargs["timeout_sec"]),
    )

    request = RequestFactory().post("/admin/records/record/youtube-session/login/")
    request.user = FakeUser()
    request.META["HTTP_REFERER"] = "/admin/records/record/"
    _attach_session_and_messages(request)

    response = admin._login_youtube_session_view(request)
    rendered_messages = [str(msg) for msg in messages.get_messages(request)]

    assert response.status_code == 302
    assert response["Location"] == "/admin/records/record/"
    assert queued == [120]
    assert any(
        "Запущена интерактивная авторизация YouTube-сессии." in msg
        for msg in rendered_messages
    )
    assert any("http://localhost:6080/vnc.html" in msg for msg in rendered_messages)


@pytest.mark.django_db
def test_record_admin_youtube_session_recover_view_renders_page(settings):
    admin = RecordAdmin(Record, AdminSite())
    settings.YOUTUBE_SESSION_UI_URL = "http://localhost:6080/vnc.html"

    request = RequestFactory().get(
        "/admin/records/record/youtube-session/recover/",
        {"next": "/admin/records/record/"},
    )
    request.user = FakeUser()
    _attach_session_and_messages(request)

    response = admin._recover_youtube_session_view(request)
    rendered = response.render().content.decode("utf-8")

    assert response.status_code == 200
    assert "Открыть окно авторизации вручную" in rendered
    assert "Запустить авторизацию вручную" in rendered
    assert "http://localhost:6080/vnc.html" in rendered
    assert "/admin/records/record/" in rendered


@pytest.mark.django_db
def test_youtube_session_banner_shows_auth_state(settings):
    state = YouTubeSessionState.get_solo()
    state.status = YouTubeSessionState.Status.AUTH_REQUIRED
    state.status_message = "Требуется повторный вход."
    state.save()

    request = RequestFactory().get("/admin/")
    request.user = FakeUser()
    context = {"request": request}

    banner = youtube_session_banner(context)

    assert banner["show_banner"] is True
    assert banner["state"].status == YouTubeSessionState.Status.AUTH_REQUIRED
    assert banner["banner_level"] == "warning"


@pytest.mark.django_db
def test_youtube_session_banner_hides_healthy_and_unknown_states():
    request = RequestFactory().get("/admin/")
    request.user = FakeUser()
    context = {"request": request}

    state = YouTubeSessionState.get_solo()
    state.status = YouTubeSessionState.Status.UNKNOWN
    state.save()
    assert youtube_session_banner(context)["show_banner"] is False

    state.status = YouTubeSessionState.Status.HEALTHY
    state.save()
    assert youtube_session_banner(context)["show_banner"] is False
