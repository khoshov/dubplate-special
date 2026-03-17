from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from django.core.files.base import ContentFile

from records.models import Record, Track
from records.services.audio.common import downloader


class _FakeResponse:
    headers = {
        "Content-Type": "audio/mpeg",
        "Content-Length": "8",
    }

    def iter_content(self, chunk_size: int):  # noqa: ARG002
        yield b"mp3-data"


@pytest.mark.django_db
def test_download_audio_to_track_deletes_old_file_on_overwrite(settings, monkeypatch):
    runtime_dir = Path("runtime")
    runtime_dir.mkdir(exist_ok=True)
    media_root = runtime_dir / f"media-audio-overwrite-{uuid.uuid4().hex}"
    media_root.mkdir()

    monkeypatch.setattr(
        downloader,
        "http_get",
        lambda *args, **kwargs: _FakeResponse(),
    )

    try:
        settings.MEDIA_ROOT = str(media_root)
        record = Record.objects.create(title="Overwrite Record")
        track = Track.objects.create(
            record=record,
            position="A1",
            position_index=1,
            title="Overwrite Track",
        )
        track.audio_preview.save("old-file.mp3", ContentFile(b"old-mp3"), save=True)
        old_name = track.audio_preview.name
        old_exists_before = track.audio_preview.storage.exists(old_name)

        saved_name = downloader.download_audio_to_track(
            track,
            "https://example.com/audio.mp3",
            overwrite=True,
            allow_http=False,
        )

        track.refresh_from_db()

        assert old_exists_before is True
        assert saved_name
        assert saved_name == track.audio_preview.name
        assert saved_name != old_name
        assert track.audio_preview.storage.exists(saved_name) is True
        assert track.audio_preview.storage.exists(old_name) is False
    finally:
        shutil.rmtree(media_root, ignore_errors=True)
