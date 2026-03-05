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
def test_record_form_youtube_refresh_requires_change_permission():
    User = get_user_model()
    user = User.objects.create_user(
        username="readonly",
        email="readonly@example.com",
        password="pass",
    )
    record = Record.objects.create(title="Permission test")
    admin = RecordAdmin(Record, AdminSite())
    request = _build_post_request(
        user=user,
        path=f"/admin/records/record/{record.pk}/youtube-refresh/",
    )

    response = admin._refresh_youtube_audio_view(request, str(record.pk))

    assert response.status_code == 302
    assert response.url.endswith(f"/admin/records/record/{record.pk}/change/")
    assert AudioEnrichmentJob.objects.count() == 0

    messages_list = [str(message) for message in messages.get_messages(request)]
    assert any("Недостаточно прав" in msg for msg in messages_list)
