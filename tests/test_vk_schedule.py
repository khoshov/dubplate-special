from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib import messages
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.utils import timezone

from records.admin.record_admin import RecordAdmin
from records.models import Record
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

    data = {
        "ids": [str(r1.pk), str(r2.pk), str(r3.pk)],
        "publish_from": start_at.strftime("%Y-%m-%dT%H:%M"),
        "publish_to": end_at.strftime("%Y-%m-%dT%H:%M"),
    }

    request = RequestFactory().post("/admin/records/record/vk-schedule/", data=data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    response = admin.vk_schedule_view(request)
    assert response.status_code == 302
    assert calls == build_even_schedule(start_at, end_at, 3)


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
    delta = (end_at - start_at) / 2

    data = {
        "ids": [str(r1.pk), str(r2.pk)],
        "publish_from": start_at.strftime("%Y-%m-%dT%H:%M"),
        "publish_to": end_at.strftime("%Y-%m-%dT%H:%M"),
    }

    request = RequestFactory().post("/admin/records/record/vk-schedule/", data=data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    response = admin.vk_schedule_view(request)
    assert response.status_code == 302
    assert calls[0] == start_at
    assert calls[1] == start_at + delta
    assert calls[2] == end_at

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
    }

    request = RequestFactory().post("/admin/records/record/vk-schedule/", data=data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    response = admin.vk_schedule_view(request)
    assert response.status_code == 302
    assert calls == [publish_at]


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

    data = {
        "ids": [str(record.pk)],
        "publish_at": "2025-01-01T10:00",
    }

    request = RequestFactory().post("/admin/records/record/vk-schedule/", data=data)
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)

    response = admin.vk_schedule_view(request)
    assert response.status_code == 302
    assert calls
    current_tz = timezone.get_current_timezone()
    expected_ts = int(
        timezone.make_aware(datetime(2025, 1, 1, 10, 0), current_tz).timestamp()
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
