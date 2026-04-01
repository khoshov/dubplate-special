from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path() -> Path:
    """
    Возвращает изолированную временную директорию без зависимости от pytest tmpdir.

    На текущем Windows-окружении builtin tmp_path падает из-за проблем с
    внутренним temp-root pytest, поэтому тесты используют обычный mkdtemp.
    """

    temp_root = Path.cwd() / "runtime" / "test-tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    path = temp_root / f"dubplate-special-tests-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _configure_test_runtime_paths(settings, tmp_path) -> None:
    """
    Настраивает безопасные локальные runtime-пути для тестов.

    Это изолирует YouTube/media тесты от docker-like путей вида /app/... и
    от системного temp каталога пользователя.
    """

    runtime_root = tmp_path / "runtime"
    media_root = tmp_path / "media"
    browser_profile_dir = runtime_root / "youtube-browser-profile"
    ytdlp_cache_dir = runtime_root / "yt-dlp-cache"
    session_lock_file = runtime_root / "youtube-session.lock"

    browser_profile_dir.mkdir(parents=True, exist_ok=True)
    ytdlp_cache_dir.mkdir(parents=True, exist_ok=True)
    media_root.mkdir(parents=True, exist_ok=True)

    settings.YOUTUBE_BROWSER_PROFILE_DIR = str(browser_profile_dir)
    settings.YOUTUBE_YTDLP_CACHE_DIR = str(ytdlp_cache_dir)
    settings.YOUTUBE_SESSION_LOCK_FILE = str(session_lock_file)
    settings.MEDIA_ROOT = str(media_root)
