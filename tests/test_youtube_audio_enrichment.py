from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Record,
    Track,
)
from records.services import record_service as record_service_module
from records.services import tasks as tasks_module
from records.services.audio.audio_service import AudioService
from records.services.audio.providers.youtube_audio_enrichment import (
    YouTubeAuthenticationRequiredError,
    YouTubeAudioEnrichmentProvider,
)
from records.services.audio.providers.youtube_session import (
    YouTubeSessionLoginResult,
    YouTubeSessionRefreshResult,
    YouTubeSessionService,
)
from records.services.record_service import RecordService


class DummyImageService:
    def download_cover(self, record, url):
        return False


class DummyRedeyeService:
    pass


class DummyAudioService:
    pass


class DiscogsImportStub:
    def __init__(self, release):
        self.release = release

    def search_by_barcode(self, barcode):
        return self.release

    def search_by_catalog_number(self, catalog_number):
        return self.release


class DiscogsUpdateStub:
    def __init__(self, release):
        self.release = release

    def get_release(self, discogs_id):
        return self.release


class DummyRelease:
    def __init__(self, release_id=None, resource_url=None, data=None):
        self.id = release_id
        self.resource_url = resource_url
        self.data = data or {}
        self.images = []


@pytest.mark.django_db
def test_import_from_discogs_enqueues_youtube_job(monkeypatch):
    user = get_user_model().objects.create_user(
        username="youtube-import",
        email="youtube-import@example.com",
        password="password123",
    )
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Discogs Import",
            "discogs_id": 4321,
            "release_year": 2025,
            "release_month": 9,
            "release_day": 26,
            "structured_formats": [],
            "tracks": [
                {
                    "position": "A1",
                    "title": "Track From Discogs",
                    "duration": "03:00",
                    "youtube_url": "https://www.youtube.com/watch?v=discogs-track",
                }
            ],
        },
    )

    captured_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(
        record_service_module.transaction,
        "on_commit",
        lambda callback: callback(),
    )
    monkeypatch.setattr(
        record_service_module.run_youtube_enrichment_job,
        "delay",
        lambda payload: captured_payloads.append(payload),
    )

    service = RecordService(
        discogs_service=DiscogsImportStub(
            DummyRelease(
                release_id=4321,
                resource_url="https://api.discogs.com/releases/4321",
            )
        ),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_discogs(
        barcode="1234567890123",
        save_image=False,
        requested_by_user_id=user.id,
    )

    job = AudioEnrichmentJob.objects.get()

    assert created is True
    assert str(job.id) == record._discogs_enrichment_job_id
    assert job.source == AudioEnrichmentJob.Source.DISCOGS_IMPORT
    assert job.status == AudioEnrichmentJob.Status.QUEUED
    assert job.requested_by_user_id == user.id
    assert job.overwrite_existing is False
    assert job.total_records == 1
    assert captured_payloads == [
        {
            "job_id": str(job.id),
            "record_ids": [record.id],
            "overwrite_existing": False,
            "requested_by_user_id": user.id,
            "source": AudioEnrichmentJob.Source.DISCOGS_IMPORT,
        }
    ]


@pytest.mark.django_db
def test_update_from_discogs_does_not_enqueue_youtube_job(monkeypatch):
    monkeypatch.setattr(
        record_service_module,
        "adapt_discogs_release",
        lambda _release: {
            "title": "Updated from Discogs",
            "discogs_id": 987,
            "release_year": 2025,
            "release_month": 9,
            "release_day": 26,
            "structured_formats": [],
            "tracks": [],
        },
    )

    called = {"delay": 0}
    monkeypatch.setattr(
        record_service_module.run_youtube_enrichment_job,
        "delay",
        lambda payload: called.__setitem__("delay", called["delay"] + 1),
    )

    record = Record.objects.create(
        title="Initial Title",
        discogs_id=987,
        release_year=2000,
        release_month=1,
        release_day=1,
    )
    service = RecordService(
        discogs_service=DiscogsUpdateStub(
            DummyRelease(release_id=987, data={"id": 987})
        ),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    updated_record = service.update_from_discogs(record, update_image=False)

    assert updated_record.title == "Updated from Discogs"
    assert called["delay"] == 0
    assert AudioEnrichmentJob.objects.count() == 0


@pytest.mark.django_db
def test_run_youtube_enrichment_job_queues_record_tasks(monkeypatch):
    job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_LIST,
        overwrite_existing=True,
    )
    record = Record.objects.create(title="Queued Record")
    queued_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(
        tasks_module.process_youtube_enrichment_record,
        "delay",
        lambda payload: queued_payloads.append(payload),
    )

    result = tasks_module.run_youtube_enrichment_job(
        {
            "job_id": str(job.id),
            "record_ids": [record.id],
            "overwrite_existing": True,
            "requested_by_user_id": None,
            "source": AudioEnrichmentJob.Source.MANUAL_LIST,
        }
    )

    job.refresh_from_db()
    job_record = AudioEnrichmentJobRecord.objects.get(job=job, record=record)

    assert result == {
        "job_id": str(job.id),
        "status": AudioEnrichmentJob.Status.RUNNING,
        "queued_records": 1,
        "skipped_records": 0,
        "missing_records": 0,
    }
    assert job.status == AudioEnrichmentJob.Status.RUNNING
    assert job.started_at is not None
    assert job_record.status == AudioEnrichmentJobRecord.Status.QUEUED
    assert queued_payloads == [
        {
            "job_id": str(job.id),
            "record_id": record.id,
            "overwrite_existing": True,
        }
    ]


@pytest.mark.django_db
def test_process_youtube_enrichment_record_updates_track(monkeypatch):
    job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_RECORD,
        overwrite_existing=True,
    )
    record = Record.objects.create(title="Process Record")
    track = Track.objects.create(
        record=record,
        position="A1",
        position_index=1,
        title="Ready Track",
        youtube_url="https://www.youtube.com/watch?v=updated-track",
    )
    monkeypatch.setattr(
        AudioService,
        "download_audio_from_youtube",
        staticmethod(
            lambda track, overwrite=False: (
                f"records/track/audio_preview/{track.pk}/ready-track.mp3"
            )
        ),
    )

    result = tasks_module.process_youtube_enrichment_record(
        {
            "job_id": str(job.id),
            "record_id": record.id,
            "overwrite_existing": True,
        }
    )

    job.refresh_from_db()
    job_record = AudioEnrichmentJobRecord.objects.get(job=job, record=record)
    track_result = AudioEnrichmentTrackResult.objects.get(
        job_record=job_record,
        track=track,
    )

    assert result == {
        "job_id": str(job.id),
        "record_id": record.id,
        "status": AudioEnrichmentJobRecord.Status.COMPLETED,
        "updated_count": 1,
        "skipped_count": 0,
        "error_count": 0,
    }
    assert job.status == AudioEnrichmentJob.Status.COMPLETED
    assert job.total_records == 1
    assert job.total_tracks == 1
    assert job.updated_count == 1
    assert job.skipped_count == 0
    assert job.error_count == 0
    assert job_record.status == AudioEnrichmentJobRecord.Status.COMPLETED
    assert track_result.status == AudioEnrichmentTrackResult.Status.UPDATED
    assert track_result.final_audio_name.endswith("ready-track.mp3")
    assert track_result.attempts == 1


@pytest.mark.django_db
def test_process_youtube_enrichment_record_skips_track_without_youtube_url():
    job = AudioEnrichmentJob.objects.create(
        source=AudioEnrichmentJob.Source.MANUAL_RECORD,
        overwrite_existing=True,
    )
    record = Record.objects.create(title="Missing URL Record")
    track = Track.objects.create(
        record=record,
        position="A1",
        position_index=1,
        title="No URL Track",
    )

    result = tasks_module.process_youtube_enrichment_record(
        {
            "job_id": str(job.id),
            "record_id": record.id,
            "overwrite_existing": True,
        }
    )

    job.refresh_from_db()
    job_record = AudioEnrichmentJobRecord.objects.get(job=job, record=record)
    track_result = AudioEnrichmentTrackResult.objects.get(
        job_record=job_record,
        track=track,
    )

    assert result == {
        "job_id": str(job.id),
        "record_id": record.id,
        "status": AudioEnrichmentJobRecord.Status.COMPLETED_WITH_ERRORS,
        "updated_count": 0,
        "skipped_count": 1,
        "error_count": 0,
    }
    assert job.status == AudioEnrichmentJob.Status.COMPLETED_WITH_ERRORS
    assert job_record.status == AudioEnrichmentJobRecord.Status.COMPLETED_WITH_ERRORS
    assert track_result.status == AudioEnrichmentTrackResult.Status.SKIPPED
    assert (
        track_result.reason_code
        == AudioEnrichmentTrackResult.Reason.MISSING_YOUTUBE_URL
    )


def test_youtube_provider_validates_supported_hosts():
    assert (
        YouTubeAudioEnrichmentProvider.is_valid_youtube_url(
            "https://www.youtube.com/watch?v=abc123"
        )
        is True
    )
    assert (
        YouTubeAudioEnrichmentProvider.is_valid_youtube_url("https://youtu.be/abc123")
        is True
    )
    assert (
        YouTubeAudioEnrichmentProvider.is_valid_youtube_url(
            "https://artist.bandcamp.com/track/demo"
        )
        is True
    )
    assert (
        YouTubeAudioEnrichmentProvider.is_valid_youtube_url(
            "https://bandcamp.com/track/demo"
        )
        is True
    )
    assert (
        YouTubeAudioEnrichmentProvider.is_valid_youtube_url(
            "https://example.com/watch?v=abc123"
        )
        is False
    )
    assert (
        YouTubeAudioEnrichmentProvider.is_valid_youtube_url("ftp://youtu.be/abc")
        is False
    )


def test_youtube_provider_retries_until_success():
    attempts = {"count": 0}

    def _operation() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary")
        return "ready-track.mp3"

    result, used_attempts, last_error = (
        YouTubeAudioEnrichmentProvider.download_with_retry(
            operation=_operation,
            max_attempts=3,
            base_delay_sec=0,
            sleep_func=lambda _delay: None,
        )
    )

    assert result == "ready-track.mp3"
    assert used_attempts == 3
    assert last_error is None


def test_youtube_provider_stops_retrying_when_authentication_is_required():
    attempts = {"count": 0}

    def _operation() -> str:
        attempts["count"] += 1
        raise YouTubeAuthenticationRequiredError("cookies required")

    result, used_attempts, last_error = (
        YouTubeAudioEnrichmentProvider.download_with_retry(
            operation=_operation,
            max_attempts=3,
            base_delay_sec=0,
            sleep_func=lambda _delay: None,
        )
    )

    assert result is None
    assert used_attempts == 1
    assert attempts["count"] == 1
    assert isinstance(last_error, YouTubeAuthenticationRequiredError)


def test_youtube_provider_builds_ydl_options_with_cookie_runtime_and_remote_components(
    settings, monkeypatch
):
    runtime_dir = Path("runtime")
    runtime_dir.mkdir(exist_ok=True)
    cookie_file = runtime_dir / "test-youtube-cookies.txt"
    cache_dir = runtime_dir / "test-yt-dlp-cache"
    try:
        cookie_file.write_text("# Netscape HTTP Cookie File", encoding="utf-8")
        settings.YOUTUBE_COOKIE_FILE = str(cookie_file)
        settings.YOUTUBE_YTDLP_CACHE_DIR = str(cache_dir)
        settings.YOUTUBE_JS_RUNTIME = "node"
        settings.YOUTUBE_JS_RUNTIME_PATH = ""
        settings.YOUTUBE_REMOTE_COMPONENTS = ["ejs:github", "ejs:npm", "invalid"]
        monkeypatch.setattr(
            "records.services.audio.providers.youtube_audio_enrichment.shutil.which",
            lambda name: "/usr/bin/node" if name == "node" else "",
        )
        monkeypatch.setattr(
            "records.services.audio.providers.youtube_audio_enrichment.YouTubeSessionService.resolve_cookies_from_browser",
            lambda: None,
        )

        options = YouTubeAudioEnrichmentProvider._build_ydl_options(str(runtime_dir))
    finally:
        if cookie_file.exists():
            cookie_file.unlink()
        if cache_dir.exists():
            cache_dir.rmdir()

    assert options["cookiefile"] == str(cookie_file)
    assert options["js_runtimes"] == {"node": {"path": "/usr/bin/node"}}
    assert options["remote_components"] == ["ejs:github", "ejs:npm"]
    assert options["cachedir"] == str(cache_dir)


def test_youtube_provider_prefers_browser_profile_over_cookie_file(
    settings, monkeypatch
):
    runtime_dir = Path("runtime")
    runtime_dir.mkdir(exist_ok=True)
    cookie_file = runtime_dir / "test-youtube-cookies.txt"
    try:
        cookie_file.write_text("# Netscape HTTP Cookie File", encoding="utf-8")
        settings.YOUTUBE_COOKIE_FILE = str(cookie_file)
        monkeypatch.setattr(
            "records.services.audio.providers.youtube_audio_enrichment.YouTubeSessionService.resolve_cookies_from_browser",
            lambda: ("chromium", "/tmp/youtube-profile", "BASICTEXT", None),
        )

        options = YouTubeAudioEnrichmentProvider._build_ydl_options(str(runtime_dir))
    finally:
        if cookie_file.exists():
            cookie_file.unlink()

    assert options["cookiesfrombrowser"] == (
        "chromium",
        "/tmp/youtube-profile",
        "BASICTEXT",
        None,
    )
    assert "cookiefile" not in options


@pytest.mark.django_db
def test_youtube_provider_fills_missing_track_duration_from_metadata(
    settings, monkeypatch
):
    runtime_dir = Path("runtime")
    runtime_dir.mkdir(exist_ok=True)
    media_root = runtime_dir / f"media-duration-{uuid.uuid4().hex}"
    temp_dir = runtime_dir / f"yt-audio-{uuid.uuid4().hex}"
    media_root.mkdir()

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, _url, download=True):  # noqa: FBT002
            return {"duration": 185}

    class FakeTemporaryDirectory:
        def __init__(self, prefix=""):  # noqa: ARG002
            self.path = temp_dir

        def __enter__(self):
            self.path.mkdir(parents=True, exist_ok=True)
            (self.path / "downloaded.mp3").write_bytes(b"mp3-data")
            return str(self.path)

        def __exit__(self, exc_type, exc, tb):
            shutil.rmtree(self.path, ignore_errors=True)
            return False

    monkeypatch.setattr(
        "records.services.audio.providers.youtube_audio_enrichment.YoutubeDL",
        FakeYoutubeDL,
    )
    monkeypatch.setattr(
        "records.services.audio.providers.youtube_audio_enrichment.tempfile.TemporaryDirectory",
        FakeTemporaryDirectory,
    )

    try:
        settings.MEDIA_ROOT = str(media_root)
        record = Record.objects.create(title="Duration Record")
        track = Track.objects.create(
            record=record,
            position="A1",
            position_index=1,
            title="Duration Track",
            youtube_url="https://www.youtube.com/watch?v=duration-track",
            duration="",
        )

        saved_name = YouTubeAudioEnrichmentProvider.download_audio_to_track(track=track)

        track.refresh_from_db()

        assert saved_name
        assert track.duration == "03:05"
        assert track.audio_preview.name.endswith("duration-track.mp3")
    finally:
        shutil.rmtree(media_root, ignore_errors=True)


@pytest.mark.django_db
def test_youtube_provider_keeps_existing_track_duration_from_source(
    settings, monkeypatch
):
    runtime_dir = Path("runtime")
    runtime_dir.mkdir(exist_ok=True)
    media_root = runtime_dir / f"media-duration-{uuid.uuid4().hex}"
    temp_dir = runtime_dir / f"yt-audio-{uuid.uuid4().hex}"
    media_root.mkdir()

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, _url, download=True):  # noqa: FBT002
            return {"duration": 185}

    class FakeTemporaryDirectory:
        def __init__(self, prefix=""):  # noqa: ARG002
            self.path = temp_dir

        def __enter__(self):
            self.path.mkdir(parents=True, exist_ok=True)
            (self.path / "downloaded.mp3").write_bytes(b"mp3-data")
            return str(self.path)

        def __exit__(self, exc_type, exc, tb):
            shutil.rmtree(self.path, ignore_errors=True)
            return False

    monkeypatch.setattr(
        "records.services.audio.providers.youtube_audio_enrichment.YoutubeDL",
        FakeYoutubeDL,
    )
    monkeypatch.setattr(
        "records.services.audio.providers.youtube_audio_enrichment.tempfile.TemporaryDirectory",
        FakeTemporaryDirectory,
    )

    try:
        settings.MEDIA_ROOT = str(media_root)
        record = Record.objects.create(title="Existing Duration Record")
        track = Track.objects.create(
            record=record,
            position="A1",
            position_index=1,
            title="Existing Duration Track",
            youtube_url="https://www.youtube.com/watch?v=existing-duration-track",
            duration="04:44",
        )

        saved_name = YouTubeAudioEnrichmentProvider.download_audio_to_track(track=track)

        track.refresh_from_db()

        assert saved_name
        assert track.duration == "04:44"
    finally:
        shutil.rmtree(media_root, ignore_errors=True)


def test_youtube_session_service_resolves_browser_profile(settings):
    runtime_dir = Path("runtime")
    profile_dir = runtime_dir / "test-youtube-browser-profile"
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    cookies_db = default_dir / "Cookies"
    try:
        cookies_db.write_text("", encoding="utf-8")
        settings.YOUTUBE_BROWSER_PROFILE_DIR = str(profile_dir)
        settings.YOUTUBE_BROWSER_NAME = "chromium"
        settings.YOUTUBE_BROWSER_KEYRING = "BASICTEXT"

        option = YouTubeSessionService.resolve_cookies_from_browser()
    finally:
        if cookies_db.exists():
            cookies_db.unlink()
        if default_dir.exists():
            default_dir.rmdir()
        if profile_dir.exists():
            profile_dir.rmdir()

    assert option == ("chromium", str(profile_dir), "BASICTEXT", None)


def test_youtube_session_service_detects_authenticated_cookies():
    cookies = [
        {"name": "VISITOR_INFO1_LIVE", "domain": ".youtube.com"},
        {"name": "SAPISID", "domain": ".youtube.com"},
    ]

    assert YouTubeSessionService.has_authenticated_session_cookies(cookies) is True


def test_youtube_session_service_rejects_anonymous_cookie_set():
    cookies = [
        {"name": "VISITOR_INFO1_LIVE", "domain": ".youtube.com"},
        {"name": "YSC", "domain": ".youtube.com"},
    ]

    assert YouTubeSessionService.has_authenticated_session_cookies(cookies) is False


@pytest.mark.django_db
def test_process_track_for_youtube_enrichment_recovers_after_session_refresh(
    settings, monkeypatch
):
    settings.YOUTUBE_SESSION_RECOVERY_RETRY_ENABLED = True
    record = Record.objects.create(title="Recovery Record")
    track = Track.objects.create(
        record=record,
        position="A1",
        position_index=1,
        title="Recovery Track",
        youtube_url="https://www.youtube.com/watch?v=recovery-track",
    )
    attempts = {"download": 0, "refresh": 0}

    def _download(track, overwrite=False):  # noqa: ARG001
        attempts["download"] += 1
        if attempts["download"] == 1:
            raise YouTubeAuthenticationRequiredError("cookies required")
        return "records/track/audio_preview/1/recovered.mp3"

    def _refresh():
        attempts["refresh"] += 1
        return YouTubeSessionRefreshResult(
            refreshed=True,
            profile_ready=True,
            seeded_from_cookie_file=False,
            message="session refreshed",
        )

    monkeypatch.setattr(
        AudioService, "download_audio_from_youtube", staticmethod(_download)
    )
    monkeypatch.setattr(AudioService, "refresh_youtube_session", staticmethod(_refresh))

    payload = tasks_module._process_track_for_youtube_enrichment(
        track=track,
        overwrite_existing=True,
    )

    assert payload["status"] == AudioEnrichmentTrackResult.Status.UPDATED
    assert payload["attempts"] == 2
    assert attempts == {"download": 2, "refresh": 1}


def test_login_youtube_session_command_reports_success(monkeypatch, capsys):
    monkeypatch.setattr(
        AudioService,
        "login_youtube_session",
        staticmethod(
            lambda timeout_ms=None: YouTubeSessionLoginResult(
                logged_in=True,
                profile_ready=True,
                message="ok",
            )
        ),
    )

    call_command("login_youtube_session", "--timeout-sec", "5")
    output = capsys.readouterr().out

    assert "Авторизованная YouTube-сессия сохранена" in output


def test_enqueue_manual_youtube_audio_enrichment_requires_non_empty_record_ids():
    service = RecordService(
        discogs_service=DiscogsImportStub(DummyRelease()),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    with pytest.raises(
        ValueError,
        match="Список record_ids для YouTube enrichment не должен быть пустым.",
    ):
        service.enqueue_youtube_audio_enrichment(
            record_ids=[],
            source=AudioEnrichmentJob.Source.MANUAL_LIST,
            overwrite_existing=True,
            requested_by_user_id=None,
        )
