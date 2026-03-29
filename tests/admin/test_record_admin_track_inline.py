from __future__ import annotations

import uuid
import re

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import reverse

from records.admin.inlines import TrackInline
from records.models import AudioEnrichmentJobRecord, Record, Track


def _make_superuser():
    suffix = uuid.uuid4().hex[:8]
    return get_user_model().objects.create_superuser(
        username=f"admin-track-{suffix}",
        email=f"admin-track-{suffix}@example.com",
        password="password123",
    )


def test_track_inline_readonly_and_editable_fields_contract() -> None:
    inline = TrackInline(Record, AdminSite())

    assert inline.fields == (
        "position_index",
        "position",
        "title",
        "duration",
        "youtube_url",
        "audio_preview",
        "audio_preview_actions",
    )
    assert inline.readonly_fields == (
        "position_index",
        "audio_preview",
        "audio_preview_actions",
    )
    assert inline.template == "admin/edit_inline/track_tabular.html"
    assert "position" not in inline.readonly_fields


@pytest.mark.django_db
def test_change_page_renders_mp3_delete_button_and_editable_fields(client) -> None:
    admin_user = _make_superuser()
    client.force_login(admin_user)
    record = Record.objects.create(title="Track inline")
    track = Track.objects.create(
        record=record,
        position_index=1,
        position="A1",
        title="Intro",
        youtube_url="https://www.youtube.com/watch?v=test123",
    )
    track.audio_preview.save("intro.mp3", ContentFile(b"demo"), save=True)

    response = client.get(reverse("admin:records_record_change", args=[record.pk]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    expected_url = reverse(
        "admin:records_record_track_delete_mp3",
        args=[record.pk, track.pk],
    )
    assert expected_url in content
    assert "Удалить mp3" in content
    assert 'id="id_tracks-0-position"' in content
    assert 'id="id_tracks-0-title"' in content
    assert content.count('id="id_tracks-0-youtube_url"') == 1
    assert '<col class="col-title">' in content
    assert '<col class="col-duration">' in content
    assert "Currently:" not in content
    assert "Change:" not in content
    assert 'id="id_tracks-0-position_index"' not in content


@pytest.mark.django_db
def test_change_page_renders_mp3_upload_button_with_new_label(client) -> None:
    admin_user = _make_superuser()
    client.force_login(admin_user)
    record = Record.objects.create(title="Upload mp3")
    track = Track.objects.create(
        record=record,
        position_index=1,
        position="A1",
        title="Preview",
        youtube_url="https://www.youtube.com/watch?v=test123",
    )

    response = client.get(reverse("admin:records_record_change", args=[record.pk]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    expected_url = reverse(
        "admin:records_record_track_enqueue_mp3",
        args=[record.pk, track.pk],
    )
    assert expected_url in content
    assert "Загрузить mp3 по URL" in content


@pytest.mark.django_db
def test_change_page_uses_uniform_input_size_per_track_column(client) -> None:
    admin_user = _make_superuser()
    client.force_login(admin_user)
    record = Record.objects.create(title="Track sizes")
    Track.objects.create(
        record=record,
        position_index=1,
        position="A",
        title="Short",
        youtube_url="",
    )
    Track.objects.create(
        record=record,
        position_index=2,
        position="B",
        title="Very long track title for sizing",
        youtube_url="https://example.com/very/long/url",
    )

    response = client.get(reverse("admin:records_record_change", args=[record.pk]))

    assert response.status_code == 200
    content = response.content.decode("utf-8")

    title_sizes = re.findall(r'name="tracks-\d+-title"[^>]*\ssize="(\d+)"', content)
    youtube_sizes = re.findall(
        r'name="tracks-\d+-youtube_url"[^>]*\ssize="(\d+)"',
        content,
    )

    assert len(set(title_sizes)) == 1
    assert len(set(youtube_sizes)) == 1


@pytest.mark.django_db
def test_delete_track_mp3_view_deletes_file_and_clears_field(client) -> None:
    admin_user = _make_superuser()
    client.force_login(admin_user)
    record = Record.objects.create(title="Delete mp3")
    track = Track.objects.create(
        record=record,
        position_index=1,
        position="A1",
        title="Preview",
    )
    track.audio_preview.save("preview.mp3", ContentFile(b"demo"), save=True)
    saved_name = track.audio_preview.name
    assert track.audio_preview.storage.exists(saved_name) is True

    response = client.post(
        reverse(
            "admin:records_record_track_delete_mp3",
            args=[record.pk, track.pk],
        )
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "deleted": True}

    track.refresh_from_db()
    assert not track.audio_preview
    assert track.audio_preview.storage.exists(saved_name) is False

    report = AudioEnrichmentJobRecord.objects.get(record=record)
    assert report.operation_name == "Удаление аудио у трека"
    assert report.scope == AudioEnrichmentJobRecord.Scope.TRACK
    assert report.result == "Аудио у трека удалено"
    assert report.track_results_json == [
        {
            "track_id": str(track.pk),
            "track_title": track.title,
            "action": "Удаление аудио у трека",
            "status": "Удалено",
            "source": "Не указан",
            "message": "Аудио у трека удалено.",
        }
    ]
