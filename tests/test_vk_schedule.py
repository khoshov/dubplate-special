from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests
from django.contrib import admin as django_admin
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.utils import timezone
from vk_api.exceptions import ApiError

from records.admin.actions import post_to_vk
from records.admin.record_admin import RecordAdmin
from records.models import (
    Record,
    Track,
    VKPublicationJob,
    VKPublicationJobRecord,
)
from records.services import record_service as record_service_module
from records.services import tasks as tasks_module
from records.services.social.schedule import build_even_schedule
from records.services.social.vk_service import (
    VKConfig,
    VKPreparedPublication,
    VKPublicationResult,
    VKService,
)


def _patch_vk_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    delayed_payloads: list[dict] = []
    monkeypatch.setattr(record_service_module.transaction, "on_commit", lambda fn: fn())
    monkeypatch.setattr(
        record_service_module.run_vk_publication_job,
        "delay",
        lambda payload: delayed_payloads.append(payload),
    )
    return delayed_payloads


def test_build_even_schedule_single():
    start_at = datetime(2025, 1, 1, 10, 0)
    end_at = datetime(2025, 1, 1, 12, 0)

    assert build_even_schedule(start_at, end_at, 1) == [start_at]


def test_build_even_schedule_five_over_eight_hours():
    start_at = datetime(2025, 1, 1, 0, 0)
    end_at = datetime(2025, 1, 1, 8, 0)

    result = build_even_schedule(start_at, end_at, 5)
    expected = [
        datetime(2025, 1, 1, 0, 0),
        datetime(2025, 1, 1, 2, 0),
        datetime(2025, 1, 1, 4, 0),
        datetime(2025, 1, 1, 6, 0),
        datetime(2025, 1, 1, 8, 0),
    ]

    assert result == expected


def test_build_even_schedule_two():
    start_at = datetime(2025, 1, 1, 10, 0)
    end_at = datetime(2025, 1, 1, 12, 0)

    assert build_even_schedule(start_at, end_at, 2) == [start_at, end_at]


def test_vk_service_publish_date_param():
    class DummyVK:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        def method(self, name: str, params: dict):
            self.calls.append((name, params))
            return {"post_id": 1}

    service = VKService(VKConfig(access_token="token", group_id=1))
    service._vk = DummyVK()  # type: ignore[assignment]

    service._wall_post(message="hello", attachments=None, publish_date_ts=None)
    assert "publish_date" not in service._vk.calls[0][1]

    service._wall_post(message="hello", attachments=None, publish_date_ts=123)
    assert service._vk.calls[1][1]["publish_date"] == 123


def test_vk_service_skips_audio_when_photo_upload_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    service = VKService(VKConfig(access_token="token", group_id=1))

    class DummyRecord:
        pk = 1
        cover_image = type("Cover", (), {"path": "C:/cover.jpg"})()
        tracks = []

    monkeypatch.setattr(
        "records.services.social.vk_service._record_cover_path",
        lambda record: Path("C:/cover.jpg"),
    )
    audio_called = {"value": False}
    monkeypatch.setattr(service, "_upload_photo", lambda image_path: None)

    def _unexpected_audio(*args, **kwargs):
        audio_called["value"] = True
        return "audio1_1"

    monkeypatch.setattr(service, "_upload_audio", _unexpected_audio)

    attachments = service._collect_release_attachments(DummyRecord(), with_audio=True)

    assert attachments.attachments == []
    assert audio_called["value"] is False


def test_vk_service_retries_photo_upload(monkeypatch: pytest.MonkeyPatch):
    service = VKService(VKConfig(access_token="token", group_id=1))
    image_path = Path(__file__).resolve()

    monkeypatch.setattr(service, "_get_wall_upload_url", lambda: "https://upload.test")
    monkeypatch.setattr(
        service, "_save_wall_photo", lambda payload: {"owner_id": 1, "id": 2}
    )
    monkeypatch.setattr("records.services.social.vk_service.time.sleep", lambda _: None)

    call_count = {"value": 0}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"photo": "p", "server": 1, "hash": "h"}

    def _post(*args, **kwargs):
        call_count["value"] += 1
        if call_count["value"] < 3:
            raise requests.ConnectionError("connection refused")
        return DummyResponse()

    monkeypatch.setattr("records.services.social.vk_service.requests.post", _post)

    attachment = service._upload_photo(image_path)

    assert attachment == "photo1_2"
    assert call_count["value"] == 3


def test_vk_service_retries_photo_save(monkeypatch: pytest.MonkeyPatch):
    service = VKService(VKConfig(access_token="token", group_id=1))
    image_path = Path(__file__).resolve()

    monkeypatch.setattr(service, "_get_wall_upload_url", lambda: "https://upload.test")
    monkeypatch.setattr("records.services.social.vk_service.time.sleep", lambda _: None)

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"photo": "p", "server": 1, "hash": "h"}

    monkeypatch.setattr(
        "records.services.social.vk_service.requests.post",
        lambda *args, **kwargs: DummyResponse(),
    )

    save_calls = {"value": 0}
    api_error = ApiError(
        None,
        "photos.saveWallPhoto",
        {},
        {
            "error": {
                "error_code": 100,
                "error_msg": "One of the parameters specified was missing or invalid: photo is undefined",
            }
        },
        {
            "error_code": 100,
            "error_msg": "One of the parameters specified was missing or invalid: photo is undefined",
        },
    )

    def _save(payload):
        save_calls["value"] += 1
        if save_calls["value"] < 3:
            raise api_error
        return {"owner_id": 1, "id": 2}

    monkeypatch.setattr(service, "_save_wall_photo", _save)

    attachment = service._upload_photo(image_path)

    assert attachment == "photo1_2"
    assert save_calls["value"] == 3


def test_vk_service_retries_audio_upload(monkeypatch: pytest.MonkeyPatch):
    service = VKService(VKConfig(access_token="token", group_id=1))
    audio_path = Path(__file__).resolve()

    monkeypatch.setattr(service, "_get_audio_upload_url", lambda: "https://upload.test")
    monkeypatch.setattr(
        service,
        "_save_audio",
        lambda payload, artist, title: {"owner_id": 1, "id": 3},
    )
    monkeypatch.setattr("records.services.social.vk_service.time.sleep", lambda _: None)

    call_count = {"value": 0}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"audio": "a", "server": 1, "hash": "h"}

    def _post(*args, **kwargs):
        call_count["value"] += 1
        if call_count["value"] < 3:
            raise requests.ConnectionError("connection refused")
        return DummyResponse()

    monkeypatch.setattr("records.services.social.vk_service.requests.post", _post)

    result = service._upload_audio(audio_path, "Artist", "Title")

    assert result.attachment == "audio1_3"
    assert result.failure_reason == ""
    assert call_count["value"] == 3


def test_vk_service_marks_copyright_blocked_audio(monkeypatch: pytest.MonkeyPatch):
    service = VKService(VKConfig(access_token="token", group_id=1))
    audio_path = Path(__file__).resolve()

    monkeypatch.setattr(service, "_get_audio_upload_url", lambda: "https://upload.test")
    monkeypatch.setattr("records.services.social.vk_service.time.sleep", lambda _: None)

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"audio": "a", "server": 1, "hash": "h"}

    monkeypatch.setattr(
        "records.services.social.vk_service.requests.post",
        lambda *args, **kwargs: DummyResponse(),
    )
    api_error = ApiError(
        None,
        "audio.save",
        {},
        {
            "error": {
                "error_code": 270,
                "error_msg": (
                    "The audio file was removed by the copyright holder "
                    "and cannot be reuploaded."
                ),
            }
        },
        {
            "error_code": 270,
            "error_msg": (
                "The audio file was removed by the copyright holder "
                "and cannot be reuploaded."
            ),
        },
    )
    monkeypatch.setattr(
        service,
        "_save_audio",
        lambda payload, artist, title: (_ for _ in ()).throw(api_error),
    )

    result = service._upload_audio(audio_path, "Artist", "Title")

    assert result.attachment is None
    assert result.failure_reason == "copyright_blocked"
    assert result.failure_code == "270"


@pytest.mark.django_db
def test_enqueue_vk_publication_deduplicates_audio_source_summary(
    settings,
    monkeypatch: pytest.MonkeyPatch,
):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    delayed_payloads = _patch_vk_enqueue(monkeypatch)
    admin = RecordAdmin(Record, AdminSite())

    User = get_user_model()
    user = User.objects.create_superuser(
        username="vk-audio-summary-admin",
        email="vk-audio-summary-admin@example.com",
        password="pass",
    )
    record = Record.objects.create(
        title="Many Youtube Tracks",
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    for index in range(16):
        record.tracks.create(
            title=f"Track {index + 1}",
            position_index=index + 1,
            audio_source=Track.AudioSource.YOUTUBE,
        )

    request = RequestFactory().post("/admin/records/record/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    post_to_vk(admin, request, Record.objects.filter(pk=record.pk))

    assert len(delayed_payloads) == 1
    job = VKPublicationJob.objects.get(pk=delayed_payloads[0]["job_id"])
    job_record = job.job_records.get(record=record)
    assert job_record.audio_source_summary == "YouTube"


@pytest.mark.django_db
def test_post_to_vk_action_enqueues_job(settings, monkeypatch: pytest.MonkeyPatch):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    delayed_payloads = _patch_vk_enqueue(monkeypatch)
    admin = RecordAdmin(Record, AdminSite())

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    record = Record.objects.create(
        title="R1",
        release_year=2000,
        release_month=1,
        release_day=1,
    )

    request = RequestFactory().post("/admin/records/record/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    post_to_vk(admin, request, Record.objects.filter(pk=record.pk))

    assert len(delayed_payloads) == 1
    job = VKPublicationJob.objects.get(pk=delayed_payloads[0]["job_id"])
    assert job.source == VKPublicationJob.Source.MANUAL_LIST
    job_record = job.job_records.get(record=record)
    assert job_record.mode == VKPublicationJobRecord.Mode.IMMEDIATE
    assert job_record.status == VKPublicationJobRecord.Status.QUEUED
    messages_list = list(messages.get_messages(request))
    rendered_messages = [str(msg) for msg in messages_list]
    assert any(
        "Релиз «R1» отправлен на публикацию на стену VK." in msg
        for msg in rendered_messages
    )
    assert any("Открыть лог" in msg for msg in rendered_messages)
    assert any(
        "/admin/records/vkpublicationreport/" in msg for msg in rendered_messages
    )
    assert job_record.operation_name == "Публикация в VK"
    assert job_record.result == "Ожидает выполнения"


@pytest.mark.django_db
def test_vk_schedule_view_enqueues_even_times(
    settings,
    monkeypatch: pytest.MonkeyPatch,
):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    delayed_payloads = _patch_vk_enqueue(monkeypatch)
    admin = RecordAdmin(Record, AdminSite())

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )

    r1 = Record.objects.create(
        title="R1", release_year=2000, release_month=1, release_day=1
    )
    r2 = Record.objects.create(
        title="R2", release_year=2001, release_month=1, release_day=1
    )
    r3 = Record.objects.create(
        title="R3", release_year=2002, release_month=1, release_day=1
    )

    current_tz = timezone.get_current_timezone()
    start_at = timezone.make_aware(datetime(2025, 1, 1, 10, 0), current_tz)
    end_at = timezone.make_aware(datetime(2025, 1, 1, 12, 0), current_tz)

    data = {
        "ids": [str(r1.pk), str(r2.pk), str(r3.pk)],
        "publish_from": start_at.strftime("%Y-%m-%dT%H:%M"),
        "publish_to": end_at.strftime("%Y-%m-%dT%H:%M"),
        "timezone": timezone.get_current_timezone_name(),
    }
    request = RequestFactory().post("/admin/records/record/vk-schedule/", data=data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    response = admin.vk_schedule_view(request)

    assert response.status_code == 302
    assert len(delayed_payloads) == 1
    job = VKPublicationJob.objects.get(pk=delayed_payloads[0]["job_id"])
    planned_times = list(
        job.job_records.order_by("created", "id").values_list(
            "planned_publish_at", flat=True
        )
    )
    assert planned_times == build_even_schedule(
        start_at.astimezone(ZoneInfo("UTC")),
        end_at.astimezone(ZoneInfo("UTC")),
        3,
    )
    rendered_messages = [str(msg) for msg in messages.get_messages(request)]
    assert any(
        "3 релизов отправлены в отложенную публикацию на стену VK." in msg
        for msg in rendered_messages
    )
    assert any("Открыть лог" in msg for msg in rendered_messages)
    assert any(
        f"/admin/records/vkpublicationreport/?job__id__exact={job.id}" in msg
        for msg in rendered_messages
    )


@pytest.mark.django_db
def test_vk_schedule_view_single_record_uses_publish_from(
    settings,
    monkeypatch: pytest.MonkeyPatch,
):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    delayed_payloads = _patch_vk_enqueue(monkeypatch)
    admin = RecordAdmin(Record, AdminSite())

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    record = Record.objects.create(
        title="R1",
        release_year=2000,
        release_month=1,
        release_day=1,
    )

    current_tz = timezone.get_current_timezone()
    publish_at = timezone.make_aware(datetime(2025, 1, 1, 10, 15), current_tz)
    data = {
        "ids": [str(record.pk)],
        "publish_at": publish_at.strftime("%Y-%m-%dT%H:%M"),
        "timezone": timezone.get_current_timezone_name(),
    }
    request = RequestFactory().post("/admin/records/record/vk-schedule/", data=data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    response = admin.vk_schedule_view(request)

    assert response.status_code == 302
    assert len(delayed_payloads) == 1
    job = VKPublicationJob.objects.get(pk=delayed_payloads[0]["job_id"])
    job_record = job.job_records.get(record=record)
    assert job.source == VKPublicationJob.Source.SCHEDULED_SINGLE
    assert job_record.mode == VKPublicationJobRecord.Mode.SCHEDULED
    assert job_record.planned_publish_at == publish_at.astimezone(ZoneInfo("UTC"))
    assert job_record.operation_name == "Отложенная публикация в VK"
    rendered_messages = [str(msg) for msg in messages.get_messages(request)]
    assert any(
        "Релиз «R1» отправлен в отложенную публикацию на стену VK." in msg
        for msg in rendered_messages
    )
    assert any("Открыть лог" in msg for msg in rendered_messages)
    assert any(
        f"/admin/records/vkpublicationreport/{job_record.pk}/change/" in msg
        for msg in rendered_messages
    )


@pytest.mark.django_db
def test_vk_schedule_view_single_record_timezone_local(
    settings,
    monkeypatch: pytest.MonkeyPatch,
):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    delayed_payloads = _patch_vk_enqueue(monkeypatch)
    admin = RecordAdmin(Record, AdminSite())

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    record = Record.objects.create(
        title="R1",
        release_year=2000,
        release_month=1,
        release_day=1,
    )

    tz_name = "Europe/Amsterdam"
    data = {
        "ids": [str(record.pk)],
        "publish_at": "2025-01-01T10:00",
        "timezone": tz_name,
    }
    request = RequestFactory().post("/admin/records/record/vk-schedule/", data=data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    response = admin.vk_schedule_view(request)

    assert response.status_code == 302
    assert len(delayed_payloads) == 1
    job = VKPublicationJob.objects.get(pk=delayed_payloads[0]["job_id"])
    job_record = job.job_records.get(record=record)
    expected_dt = timezone.make_aware(
        datetime(2025, 1, 1, 10, 0),
        ZoneInfo(tz_name),
    ).astimezone(ZoneInfo("UTC"))
    assert job_record.planned_publish_at == expected_dt


@pytest.mark.django_db
def test_vk_schedule_view_single_record_requires_publish_at(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin = RecordAdmin(Record, AdminSite())

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass",
    )
    record = Record.objects.create(
        title="R1",
        release_year=2000,
        release_month=1,
        release_day=1,
    )

    data = {
        "ids": [str(record.pk)],
        "publish_at": "",
        "timezone": timezone.get_current_timezone_name(),
    }
    request = RequestFactory().post("/admin/records/record/vk-schedule/", data=data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    response = admin.vk_schedule_view(request)

    assert response.status_code == 200
    assert not VKPublicationJob.objects.exists()
    messages_list = list(messages.get_messages(request))
    assert any("Укажите корректную дату и время" in str(msg) for msg in messages_list)


@pytest.mark.django_db
def test_vk_report_notification_is_shown_once():
    user = get_user_model().objects.create_superuser(
        username="vk-notify-admin",
        email="vk-notify-admin@example.com",
        password="pass",
    )
    record = Record.objects.create(title="Notify VK")
    job = VKPublicationJob.objects.create(
        source=VKPublicationJob.Source.MANUAL_LIST,
        status=VKPublicationJob.Status.COMPLETED_WITH_ERRORS,
        requested_by_user=user,
        total_records=1,
    )
    job_record = VKPublicationJobRecord.objects.create(
        job=job,
        record=record,
        mode=VKPublicationJobRecord.Mode.IMMEDIATE,
        status=VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS,
        operation_name="Публикация в VK",
        result="Пост опубликован только текстом",
        result_message=(
            "Пост опубликован только текстом: изображение релиза не загружено."
        ),
        notify_in_admin=True,
        finished_at=timezone.now(),
    )

    request = RequestFactory().get("/admin/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    django_admin.site.each_context(request)

    rendered_messages = [str(msg) for msg in messages.get_messages(request)]
    assert any(
        "Публикация на стену VK завершена для релиза «Notify VK»" in msg
        for msg in rendered_messages
    )
    job_record.refresh_from_db()
    assert job_record.admin_notification_shown_at is not None

    second_request = RequestFactory().get("/admin/")
    second_request.user = user
    second_request.session = {}
    second_request._messages = FallbackStorage(second_request)

    django_admin.site.each_context(second_request)

    second_messages = [str(msg) for msg in messages.get_messages(second_request)]
    assert second_messages == []


@pytest.mark.django_db
def test_vk_report_full_success_notification_uses_success_level():
    user = get_user_model().objects.create_superuser(
        username="vk-success-admin",
        email="vk-success-admin@example.com",
        password="pass",
    )
    record = Record.objects.create(title="Girlcatcher")
    job = VKPublicationJob.objects.create(
        source=VKPublicationJob.Source.MANUAL_LIST,
        status=VKPublicationJob.Status.COMPLETED,
        requested_by_user=user,
        total_records=1,
    )
    VKPublicationJobRecord.objects.create(
        job=job,
        record=record,
        mode=VKPublicationJobRecord.Mode.IMMEDIATE,
        status=VKPublicationJobRecord.Status.COMPLETED,
        operation_name="Публикация в VK",
        result="Пост опубликован с изображением и аудио",
        result_message="Пост опубликован с изображением и аудио.",
        notify_in_admin=True,
        finished_at=timezone.now(),
    )

    request = RequestFactory().get("/admin/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    django_admin.site.each_context(request)

    rendered_messages = list(messages.get_messages(request))
    assert len(rendered_messages) == 1
    assert rendered_messages[0].level == messages.SUCCESS
    assert "Публикация на стену VK завершена для релиза «Girlcatcher»" in str(
        rendered_messages[0]
    )


@pytest.mark.django_db
def test_process_vk_publication_record_logs_success(
    settings, monkeypatch: pytest.MonkeyPatch
):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    record = Record.objects.create(
        title="R1",
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    job = VKPublicationJob.objects.create(
        source=VKPublicationJob.Source.MANUAL_LIST,
        status=VKPublicationJob.Status.QUEUED,
        total_records=1,
    )
    job_record = VKPublicationJobRecord.objects.create(
        job=job,
        record=record,
        mode=VKPublicationJobRecord.Mode.IMMEDIATE,
    )

    class DummyVKService:
        def prepare_record_publication(self, record, **kwargs):
            return VKPreparedPublication(
                message="hello",
                attachments=["photo1_2", "audio1_3"],
                photo_expected=True,
                photo_uploaded=True,
                audio_expected_count=1,
                audio_uploaded_count=1,
                audio_failed_count=0,
                failed_track_titles=[],
                audio_failure_details=[],
            )

        def publish_prepared_publication(self, record, prepared, publish_at=None):
            return VKPublicationResult(
                post_id=123,
                attachments=prepared.attachments,
                photo_expected=prepared.photo_expected,
                photo_uploaded=prepared.photo_uploaded,
                audio_expected_count=prepared.audio_expected_count,
                audio_uploaded_count=prepared.audio_uploaded_count,
                audio_failed_count=prepared.audio_failed_count,
                failed_track_titles=prepared.failed_track_titles,
                audio_failure_details=prepared.audio_failure_details,
            )

    class DummyVKServiceFactory:
        @classmethod
        def from_settings(cls):
            return DummyVKService()

    monkeypatch.setattr(tasks_module, "VKService", DummyVKServiceFactory)

    payload = tasks_module.process_vk_publication_record(
        {"job_id": str(job.id), "job_record_id": str(job_record.id)}
    )

    job_record.refresh_from_db()
    record.refresh_from_db()
    job.refresh_from_db()
    assert payload["status"] == VKPublicationJobRecord.Status.COMPLETED
    assert job_record.status == VKPublicationJobRecord.Status.COMPLETED
    assert job_record.vk_post_id == 123
    assert record.vk_published_at is not None
    assert job.status == VKPublicationJob.Status.COMPLETED
    assert job_record.result == "Пост опубликован с изображением и аудио"
    assert job_record.photo_uploaded is True
    assert job_record.audio_uploaded_count == 1
    assert job_record.audio_failed_count == 0
    assert job_record.audio_failure_details == []


@pytest.mark.django_db
def test_process_vk_publication_record_retries_collision(
    settings,
    monkeypatch: pytest.MonkeyPatch,
):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    record = Record.objects.create(
        title="R1",
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    planned_at = timezone.make_aware(datetime(2025, 1, 1, 10, 0), ZoneInfo("UTC"))
    job = VKPublicationJob.objects.create(
        source=VKPublicationJob.Source.SCHEDULED_LIST,
        status=VKPublicationJob.Status.QUEUED,
        total_records=2,
    )
    job_record = VKPublicationJobRecord.objects.create(
        job=job,
        record=record,
        mode=VKPublicationJobRecord.Mode.SCHEDULED,
        planned_publish_at=planned_at,
    )
    call_times: list[datetime | None] = []
    prepare_calls = {"value": 0}

    class DummyVKService:
        def prepare_record_publication(self, record, **kwargs):
            prepare_calls["value"] += 1
            return VKPreparedPublication(
                message="hello",
                attachments=["photo1_2", "audio1_3"],
                photo_expected=True,
                photo_uploaded=True,
                audio_expected_count=1,
                audio_uploaded_count=1,
                audio_failed_count=0,
                failed_track_titles=[],
                audio_failure_details=[],
            )

        def publish_prepared_publication(self, record, prepared, publish_at=None):
            call_times.append(publish_at)
            if len(call_times) == 1:
                raise ApiError(
                    None,
                    "wall.post",
                    {},
                    {},
                    {
                        "error_code": 214,
                        "error_msg": "a post is already scheduled for this time",
                    },
                )
            return VKPublicationResult(
                post_id=321,
                attachments=prepared.attachments,
                photo_expected=prepared.photo_expected,
                photo_uploaded=prepared.photo_uploaded,
                audio_expected_count=prepared.audio_expected_count,
                audio_uploaded_count=prepared.audio_uploaded_count,
                audio_failed_count=prepared.audio_failed_count,
                failed_track_titles=prepared.failed_track_titles,
                audio_failure_details=prepared.audio_failure_details,
            )

    class DummyVKServiceFactory:
        @classmethod
        def from_settings(cls):
            return DummyVKService()

    monkeypatch.setattr(tasks_module, "VKService", DummyVKServiceFactory)

    payload = tasks_module.process_vk_publication_record(
        {"job_id": str(job.id), "job_record_id": str(job_record.id)}
    )

    job_record.refresh_from_db()
    assert payload["status"] == VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS
    assert call_times == [planned_at, planned_at + timedelta(minutes=30)]
    assert prepare_calls["value"] == 1
    assert job_record.effective_publish_at == planned_at + timedelta(minutes=30)
    assert job_record.warning_message == "Время публикации автоматически изменено."


@pytest.mark.django_db
def test_process_vk_publication_record_marks_text_only_warning(
    settings, monkeypatch: pytest.MonkeyPatch
):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    record = Record.objects.create(
        title="R1",
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    job = VKPublicationJob.objects.create(
        source=VKPublicationJob.Source.MANUAL_LIST,
        status=VKPublicationJob.Status.QUEUED,
        total_records=1,
    )
    job_record = VKPublicationJobRecord.objects.create(
        job=job,
        record=record,
        mode=VKPublicationJobRecord.Mode.IMMEDIATE,
    )

    class DummyVKService:
        def prepare_record_publication(self, record, **kwargs):
            return VKPreparedPublication(
                message="hello",
                attachments=[],
                photo_expected=True,
                photo_uploaded=False,
                audio_expected_count=2,
                audio_uploaded_count=0,
                audio_failed_count=0,
                failed_track_titles=[],
                audio_failure_details=[],
            )

        def publish_prepared_publication(self, record, prepared, publish_at=None):
            return VKPublicationResult(
                post_id=555,
                attachments=prepared.attachments,
                photo_expected=prepared.photo_expected,
                photo_uploaded=prepared.photo_uploaded,
                audio_expected_count=prepared.audio_expected_count,
                audio_uploaded_count=prepared.audio_uploaded_count,
                audio_failed_count=prepared.audio_failed_count,
                failed_track_titles=prepared.failed_track_titles,
                audio_failure_details=prepared.audio_failure_details,
            )

    class DummyVKServiceFactory:
        @classmethod
        def from_settings(cls):
            return DummyVKService()

    monkeypatch.setattr(tasks_module, "VKService", DummyVKServiceFactory)

    payload = tasks_module.process_vk_publication_record(
        {"job_id": str(job.id), "job_record_id": str(job_record.id)}
    )

    job_record.refresh_from_db()
    job.refresh_from_db()
    assert payload["status"] == VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS
    assert job_record.status == VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS
    assert (
        job_record.result
        == "Изображение релиза не загружено, поэтому аудио не добавлялось"
    )
    assert job.status == VKPublicationJob.Status.COMPLETED_WITH_ERRORS


@pytest.mark.django_db
def test_process_vk_publication_record_saves_audio_failure_details(
    settings, monkeypatch: pytest.MonkeyPatch
):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    record = Record.objects.create(
        title="R1",
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    job = VKPublicationJob.objects.create(
        source=VKPublicationJob.Source.MANUAL_LIST,
        status=VKPublicationJob.Status.QUEUED,
        total_records=1,
    )
    job_record = VKPublicationJobRecord.objects.create(
        job=job,
        record=record,
        mode=VKPublicationJobRecord.Mode.IMMEDIATE,
    )

    class DummyVKService:
        def prepare_record_publication(self, record, **kwargs):
            return VKPreparedPublication(
                message="hello",
                attachments=["photo1_2"],
                photo_expected=True,
                photo_uploaded=True,
                audio_expected_count=1,
                audio_uploaded_count=0,
                audio_failed_count=1,
                failed_track_titles=["Track 1"],
                audio_failure_details=[
                    {
                        "track_id": "10",
                        "track_title": "Track 1",
                        "reason": "copyright_blocked",
                        "message": (
                            "VK отклонил сохранение аудио: файл заблокирован "
                            "правообладателем и не может быть повторно загружен."
                        ),
                        "error_code": "270",
                    }
                ],
            )

        def publish_prepared_publication(self, record, prepared, publish_at=None):
            return VKPublicationResult(
                post_id=999,
                attachments=prepared.attachments,
                photo_expected=prepared.photo_expected,
                photo_uploaded=prepared.photo_uploaded,
                audio_expected_count=prepared.audio_expected_count,
                audio_uploaded_count=prepared.audio_uploaded_count,
                audio_failed_count=prepared.audio_failed_count,
                failed_track_titles=prepared.failed_track_titles,
                audio_failure_details=prepared.audio_failure_details,
            )

    class DummyVKServiceFactory:
        @classmethod
        def from_settings(cls):
            return DummyVKService()

    monkeypatch.setattr(tasks_module, "VKService", DummyVKServiceFactory)

    tasks_module.process_vk_publication_record(
        {"job_id": str(job.id), "job_record_id": str(job_record.id)}
    )

    job_record.refresh_from_db()
    assert job_record.audio_failure_details == [
        {
            "track_id": "10",
            "track_title": "Track 1",
            "reason": "copyright_blocked",
            "message": (
                "VK отклонил сохранение аудио: файл заблокирован "
                "правообладателем и не может быть повторно загружен."
            ),
            "error_code": "270",
        }
    ]


def test_parse_datetime_uses_client_timezone():
    tz_moscow = ZoneInfo("Europe/Moscow")
    tz_ny = ZoneInfo("America/New_York")
    raw = "2025-01-01T10:00"

    moscow_dt = RecordAdmin._parse_datetime_local(raw, tz_moscow)
    ny_dt = RecordAdmin._parse_datetime_local(raw, tz_ny)

    assert moscow_dt == timezone.make_aware(
        datetime(2025, 1, 1, 10, 0),
        tz_moscow,
    ).astimezone(ZoneInfo("UTC"))
    assert ny_dt == timezone.make_aware(
        datetime(2025, 1, 1, 10, 0),
        tz_ny,
    ).astimezone(ZoneInfo("UTC"))
    assert moscow_dt != ny_dt


@pytest.mark.django_db
def test_record_admin_vk_published_at_display(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin = RecordAdmin(Record, AdminSite())
    record = Record.objects.create(
        title="R1",
        release_year=2000,
        release_month=1,
        release_day=1,
    )

    assert admin.vk_published_at_display(record) == "-"

    published_at = timezone.make_aware(datetime(2025, 1, 1, 10, 0), ZoneInfo("UTC"))
    record.vk_published_at = published_at
    rendered = str(admin.vk_published_at_display(record))

    assert "js-vk-published-at" in rendered
    assert "data-utc=" in rendered


def test_record_admin_list_per_page_is_20():
    assert RecordAdmin.list_per_page == 20
