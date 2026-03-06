from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.utils import timezone

from records.admin.actions import post_to_vk
from records.admin.record_admin import RecordAdmin
from records.models import Record, VKPublicationLog
from records.services.social.schedule import build_even_schedule
from records.services.social.vk_service import VKConfig, VKService
from vk_api.exceptions import ApiError


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


@pytest.mark.django_db
def test_post_to_vk_action_ignores_timezone(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin_site = AdminSite()
    admin = RecordAdmin(Record, admin_site)

    calls: list[datetime | None] = []

    class DummyVKService:
        def post_record_with_audio(self, record, publish_at=None, **kwargs):
            calls.append(publish_at)
            return 1

    admin.vk_service = DummyVKService()

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )

    record = Record.objects.create(
        title="R1", release_year=2000, release_month=1, release_day=1
    )

    request = RequestFactory().post("/admin/records/record/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    previous_tz = timezone.get_current_timezone()
    timezone.activate(ZoneInfo("America/New_York"))
    try:
        post_to_vk(admin, request, Record.objects.filter(pk=record.pk))
    finally:
        timezone.activate(previous_tz)

    assert calls == [None]
    record.refresh_from_db()
    assert record.vk_published_at is not None

    event = record.vk_publication_logs.order_by("-created").first()
    assert event is not None
    assert event.mode == VKPublicationLog.Mode.IMMEDIATE
    assert event.status == VKPublicationLog.Status.SUCCESS
    assert event.vk_post_id == 1


@pytest.mark.django_db
def test_post_to_vk_action_logs_failed_event(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin_site = AdminSite()
    admin = RecordAdmin(Record, admin_site)

    class DummyVKService:
        def post_record_with_audio(self, record, publish_at=None, **kwargs):
            raise RuntimeError("vk unavailable")

    admin.vk_service = DummyVKService()

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )

    record = Record.objects.create(
        title="R1", release_year=2000, release_month=1, release_day=1
    )

    request = RequestFactory().post("/admin/records/record/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    post_to_vk(admin, request, Record.objects.filter(pk=record.pk))

    record.refresh_from_db()
    assert record.vk_published_at is None

    event = record.vk_publication_logs.order_by("-created").first()
    assert event is not None
    assert event.mode == VKPublicationLog.Mode.IMMEDIATE
    assert event.status == VKPublicationLog.Status.FAILED
    assert "vk unavailable" in event.error_message


@pytest.mark.django_db
def test_vk_schedule_view_posts_even_times(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin_site = AdminSite()
    admin = RecordAdmin(Record, admin_site)

    calls: list[datetime] = []

    class DummyVKService:
        def post_record_with_audio(self, record, publish_at=None, **kwargs):
            calls.append(publish_at)
            return 1

    admin.vk_service = DummyVKService()

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
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

    start_utc = start_at.astimezone(ZoneInfo("UTC"))
    end_utc = end_at.astimezone(ZoneInfo("UTC"))

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
    assert calls == build_even_schedule(start_utc, end_utc, 3)


@pytest.mark.django_db
def test_vk_schedule_view_collision_shifts_time(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin_site = AdminSite()
    admin = RecordAdmin(Record, admin_site)

    calls: list[datetime] = []
    call_count = {"n": 0}

    class DummyVKService:
        def post_record_with_audio(self, record, publish_at=None, **kwargs):
            calls.append(publish_at)
            call_count["n"] += 1
            if call_count["n"] == 1:
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
            return 1

    admin.vk_service = DummyVKService()

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )

    r1 = Record.objects.create(
        title="R1", release_year=2000, release_month=1, release_day=1
    )
    r2 = Record.objects.create(
        title="R2", release_year=2001, release_month=1, release_day=1
    )

    current_tz = timezone.get_current_timezone()
    start_at = timezone.make_aware(datetime(2025, 1, 1, 10, 0), current_tz)
    end_at = timezone.make_aware(datetime(2025, 1, 1, 12, 0), current_tz)
    start_utc = start_at.astimezone(ZoneInfo("UTC"))
    end_utc = end_at.astimezone(ZoneInfo("UTC"))
    delta = (end_utc - start_utc) / 2

    data = {
        "ids": [str(r1.pk), str(r2.pk)],
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
    assert calls[0] == start_utc
    assert calls[1] == start_utc + delta
    assert calls[2] == end_utc

    messages_list = list(messages.get_messages(request))
    assert any("время смещено" in str(msg) for msg in messages_list)


@pytest.mark.django_db
def test_vk_schedule_view_single_record_uses_publish_from(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin_site = AdminSite()
    admin = RecordAdmin(Record, admin_site)

    calls: list[datetime] = []

    class DummyVKService:
        def post_record_with_audio(self, record, publish_at=None, **kwargs):
            calls.append(publish_at)
            return 1

    admin.vk_service = DummyVKService()

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )

    record = Record.objects.create(
        title="R1", release_year=2000, release_month=1, release_day=1
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
    expected_utc = publish_at.astimezone(ZoneInfo("UTC"))
    assert calls == [expected_utc]

    record.refresh_from_db()
    assert record.vk_published_at == expected_utc

    event = record.vk_publication_logs.order_by("-created").first()
    assert event is not None
    assert event.mode == VKPublicationLog.Mode.SCHEDULED
    assert event.status == VKPublicationLog.Status.SUCCESS
    assert event.planned_publish_at == expected_utc
    assert event.effective_publish_at == expected_utc


@pytest.mark.django_db
def test_vk_schedule_view_single_record_timezone_local(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin_site = AdminSite()
    admin = RecordAdmin(Record, admin_site)

    calls: list[datetime] = []

    class DummyVKService:
        def post_record_with_audio(self, record, publish_at=None, **kwargs):
            calls.append(publish_at)
            return 1

    admin.vk_service = DummyVKService()

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )

    record = Record.objects.create(
        title="R1", release_year=2000, release_month=1, release_day=1
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
    assert calls
    expected_ts = int(
        timezone.make_aware(datetime(2025, 1, 1, 10, 0), ZoneInfo(tz_name))
        .astimezone(ZoneInfo("UTC"))
        .timestamp()
    )
    assert int(calls[0].timestamp()) == expected_ts


@pytest.mark.django_db
def test_vk_schedule_view_single_record_requires_publish_at(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin_site = AdminSite()
    admin = RecordAdmin(Record, admin_site)

    calls: list[datetime] = []

    class DummyVKService:
        def post_record_with_audio(self, record, publish_at=None, **kwargs):
            calls.append(publish_at)
            return 1

    admin.vk_service = DummyVKService()

    User = get_user_model()
    user = User.objects.create_superuser(
        username="admin", email="admin@example.com", password="pass"
    )

    record = Record.objects.create(
        title="R1", release_year=2000, release_month=1, release_day=1
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
    assert calls == []
    messages_list = list(messages.get_messages(request))
    assert any("Укажите корректную дату и время" in str(msg) for msg in messages_list)


def test_parse_datetime_uses_client_timezone():
    tz_moscow = ZoneInfo("Europe/Moscow")
    tz_ny = ZoneInfo("America/New_York")
    raw = "2025-01-01T10:00"

    moscow_dt = RecordAdmin._parse_datetime_local(raw, tz_moscow)
    ny_dt = RecordAdmin._parse_datetime_local(raw, tz_ny)

    assert moscow_dt == timezone.make_aware(
        datetime(2025, 1, 1, 10, 0), tz_moscow
    ).astimezone(ZoneInfo("UTC"))
    assert ny_dt == timezone.make_aware(datetime(2025, 1, 1, 10, 0), tz_ny).astimezone(
        ZoneInfo("UTC")
    )
    assert moscow_dt != ny_dt


@pytest.mark.django_db
def test_record_admin_vk_published_at_display(settings):
    settings.VK_ACCESS_TOKEN = "token"
    settings.VK_GROUP_ID = "1"

    admin_site = AdminSite()
    admin = RecordAdmin(Record, admin_site)

    record = Record.objects.create(
        title="R1", release_year=2000, release_month=1, release_day=1
    )
    assert admin.vk_published_at_display(record) == "-"

    published_at = timezone.make_aware(datetime(2025, 1, 1, 10, 0), ZoneInfo("UTC"))
    record.vk_published_at = published_at
    rendered = str(admin.vk_published_at_display(record))

    assert "js-vk-published-at" in rendered
    assert "data-utc=" in rendered


def test_record_admin_list_per_page_is_20():
    assert RecordAdmin.list_per_page == 20
