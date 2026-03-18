from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from django.conf import settings
from django.core.files import File
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from config.logging import log_event
from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Record,
    Track,
)
from records.services.audio.providers.youtube_session import YouTubeSessionService

logger = logging.getLogger(__name__)
_DURATION_STRING_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
_YOUTUBE_AUDIO_COMPONENT = "youtube_audio"


class PayloadValidationError(ValueError):
    """Ошибка валидации payload для Celery-задач."""


class YouTubeAuthenticationRequiredError(RuntimeError):
    """YouTube запросил cookies или ручное подтверждение."""


def _ensure_int(value: Any, *, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PayloadValidationError(f"Поле '{field}' должно быть числом.") from exc
    return result


def _ensure_uuid(value: Any, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PayloadValidationError(f"Поле '{field}' должно быть UUID.") from exc


class _YTDLPLogger:
    """Адаптер логгера для yt-dlp."""

    def debug(self, message: str) -> None:
        log_event(
            logger,
            logging.DEBUG,
            f"yt-dlp: {message}",
            component=_YOUTUBE_AUDIO_COMPONENT,
            event="ytdlp_raw",
        )

    def info(self, message: str) -> None:
        log_event(
            logger,
            logging.DEBUG,
            f"yt-dlp: {message}",
            component=_YOUTUBE_AUDIO_COMPONENT,
            event="ytdlp_raw",
        )

    def warning(self, message: str) -> None:
        log_event(
            logger,
            logging.DEBUG,
            f"yt-dlp: {message}",
            component=_YOUTUBE_AUDIO_COMPONENT,
            event="ytdlp_raw",
        )

    def error(self, message: str) -> None:
        log_event(
            logger,
            logging.DEBUG,
            f"yt-dlp: {message}",
            component=_YOUTUBE_AUDIO_COMPONENT,
            event="ytdlp_raw",
        )


@dataclass(frozen=True)
class RunJobPayload:
    """Контракт payload для fan-out задачи по списку записей."""

    job_id: uuid.UUID
    record_ids: tuple[int, ...]
    overwrite_existing: bool
    requested_by_user_id: int | None
    source: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunJobPayload:
        job_id = _ensure_uuid(payload.get("job_id"), field="job_id")
        raw_record_ids = payload.get("record_ids")
        if not isinstance(raw_record_ids, list) or not raw_record_ids:
            raise PayloadValidationError(
                "Поле 'record_ids' должно быть непустым списком ID."
            )

        record_ids = tuple(
            _ensure_int(value, field="record_ids") for value in raw_record_ids
        )
        requested_by_user_id_raw = payload.get("requested_by_user_id")
        requested_by_user_id = (
            None
            if requested_by_user_id_raw in (None, "")
            else _ensure_int(
                requested_by_user_id_raw,
                field="requested_by_user_id",
            )
        )
        source = str(payload.get("source", "")).strip()
        if source not in set(AudioEnrichmentJob.Source.values):
            raise PayloadValidationError(
                "Поле 'source' содержит неподдерживаемое значение."
            )

        return cls(
            job_id=job_id,
            record_ids=record_ids,
            overwrite_existing=bool(payload.get("overwrite_existing", False)),
            requested_by_user_id=requested_by_user_id,
            source=source,
        )


@dataclass(frozen=True)
class ProcessRecordPayload:
    """Контракт payload для задачи обработки одной записи."""

    job_id: uuid.UUID
    record_id: int
    overwrite_existing: bool

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProcessRecordPayload:
        return cls(
            job_id=_ensure_uuid(payload.get("job_id"), field="job_id"),
            record_id=_ensure_int(payload.get("record_id"), field="record_id"),
            overwrite_existing=bool(payload.get("overwrite_existing", False)),
        )


@dataclass(frozen=True)
class ProcessTrackPayload:
    """Контракт payload для задачи обработки одного трека."""

    job_id: uuid.UUID
    track_id: int
    overwrite_existing: bool

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProcessTrackPayload:
        return cls(
            job_id=_ensure_uuid(payload.get("job_id"), field="job_id"),
            track_id=_ensure_int(payload.get("track_id"), field="track_id"),
            overwrite_existing=bool(payload.get("overwrite_existing", False)),
        )


class YouTubeAudioEnrichmentProvider:
    """Набор provider-level операций для YouTube enrichment."""

    ACTIVE_RECORD_STATUSES = (
        AudioEnrichmentJobRecord.Status.QUEUED,
        AudioEnrichmentJobRecord.Status.RUNNING,
    )
    VALID_YOUTUBE_HOSTS = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }

    @classmethod
    def is_valid_youtube_url(cls, value: str | None) -> bool:
        """Проверяет, что строка похожа на поддерживаемый YouTube URL."""
        if not value:
            return False
        try:
            parsed = urlparse(value.strip())
        except ValueError:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        return (parsed.hostname or "").lower() in cls.VALID_YOUTUBE_HOSTS

    @staticmethod
    def download_with_retry(
        *,
        operation: Callable[[], str | None],
        max_attempts: int = 3,
        base_delay_sec: float = 1.0,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> tuple[str | None, int, Exception | None]:
        """Выполняет download operation с ограниченным числом попыток."""
        attempt = 0
        last_error: Exception | None = None
        while attempt < max_attempts:
            attempt += 1
            try:
                result = operation()
                if result:
                    return result, attempt, None
                raise RuntimeError("YouTube download returned empty result.")
            except YouTubeAuthenticationRequiredError as exc:
                return None, attempt, exc
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= max_attempts:
                    break
                sleep_func(base_delay_sec * (2 ** (attempt - 1)))
        return None, max_attempts, last_error

    @staticmethod
    def serialize_track_result(
        *,
        status: str,
        reason_code: str = AudioEnrichmentTrackResult.Reason.NONE,
        attempts: int = 1,
        previous_audio_present: bool = False,
        final_audio_name: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        """Готовит payload для AudioEnrichmentTrackResult.update_or_create."""
        safe_attempts = max(1, min(3, int(attempts)))
        return {
            "status": status,
            "reason_code": reason_code or AudioEnrichmentTrackResult.Reason.NONE,
            "attempts": safe_attempts,
            "previous_audio_present": previous_audio_present,
            "final_audio_name": final_audio_name or "",
            "error_message": error_message or "",
        }

    @classmethod
    def log_track_outcome(
        cls,
        *,
        record_id: int,
        track_id: int,
        status: str,
        reason_code: str,
        attempts: int,
        previous_audio_present: bool,
    ) -> None:
        """Пишет структурированный лог итога по треку."""
        log_event(
            logger,
            logging.INFO,
            "Зафиксирован итог обработки трека YouTube-задачей.",
            component=_YOUTUBE_AUDIO_COMPONENT,
            event="track_outcome",
            record_id=record_id,
            track_id=track_id,
            status=status,
            reason=reason_code or "none",
            attempts=attempts,
            previous_audio_present=previous_audio_present,
        )

    @staticmethod
    def serialize_record_counters(
        *,
        updated_count: int,
        skipped_count: int,
        error_count: int,
    ) -> dict[str, int]:
        """Нормализует counters результата по записи."""
        return {
            "updated_count": max(0, int(updated_count)),
            "skipped_count": max(0, int(skipped_count)),
            "error_count": max(0, int(error_count)),
        }

    @classmethod
    def acquire_record_lock(
        cls,
        *,
        job: AudioEnrichmentJob,
        record: Record,
    ) -> tuple[AudioEnrichmentJobRecord, bool]:
        """Фиксирует, что запись либо уже обрабатывается, либо доступна для job."""
        existing = AudioEnrichmentJobRecord.objects.filter(
            job=job,
            record=record,
        ).first()
        if existing is not None:
            allowed = existing.status in cls.ACTIVE_RECORD_STATUSES
            return existing, allowed

        with transaction.atomic():
            conflict_exists = (
                AudioEnrichmentJobRecord.objects.select_for_update()
                .filter(record=record, status__in=cls.ACTIVE_RECORD_STATUSES)
                .exclude(job=job)
                .exists()
            )
            if conflict_exists:
                skipped_record, _ = AudioEnrichmentJobRecord.objects.get_or_create(
                    job=job,
                    record=record,
                    defaults={
                        "status": AudioEnrichmentJobRecord.Status.SKIPPED,
                        "reason_code": AudioEnrichmentJobRecord.Reason.ALREADY_RUNNING,
                        "finished_at": timezone.now(),
                    },
                )
                return skipped_record, False

            try:
                job_record = AudioEnrichmentJobRecord.objects.create(
                    job=job,
                    record=record,
                    status=AudioEnrichmentJobRecord.Status.QUEUED,
                )
            except IntegrityError:
                skipped_record, _ = AudioEnrichmentJobRecord.objects.get_or_create(
                    job=job,
                    record=record,
                    defaults={
                        "status": AudioEnrichmentJobRecord.Status.SKIPPED,
                        "reason_code": AudioEnrichmentJobRecord.Reason.ALREADY_RUNNING,
                        "finished_at": timezone.now(),
                    },
                )
                return skipped_record, False

        return job_record, True

    @staticmethod
    def mark_record_running(job_record: AudioEnrichmentJobRecord) -> None:
        """Переводит job-record в running."""
        if job_record.status == AudioEnrichmentJobRecord.Status.RUNNING:
            return
        job_record.status = AudioEnrichmentJobRecord.Status.RUNNING
        if job_record.started_at is None:
            job_record.started_at = timezone.now()
        job_record.save(update_fields=["status", "started_at", "modified"])

    @staticmethod
    def mark_record_finished(
        *,
        job_record: AudioEnrichmentJobRecord,
        updated_count: int,
        skipped_count: int,
        error_count: int,
        force_failed: bool = False,
        reason_code: str = AudioEnrichmentJobRecord.Reason.NONE,
    ) -> AudioEnrichmentJobRecord:
        """Фиксирует итог по записи и статус выполнения."""
        counters = YouTubeAudioEnrichmentProvider.serialize_record_counters(
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
        )

        if force_failed:
            status = AudioEnrichmentJobRecord.Status.FAILED
        elif counters["error_count"] > 0 or counters["skipped_count"] > 0:
            status = AudioEnrichmentJobRecord.Status.COMPLETED_WITH_ERRORS
        else:
            status = AudioEnrichmentJobRecord.Status.COMPLETED

        job_record.status = status
        job_record.reason_code = reason_code
        job_record.updated_count = counters["updated_count"]
        job_record.skipped_count = counters["skipped_count"]
        job_record.error_count = counters["error_count"]
        job_record.finished_at = timezone.now()
        job_record.save(
            update_fields=[
                "status",
                "reason_code",
                "updated_count",
                "skipped_count",
                "error_count",
                "finished_at",
                "modified",
            ]
        )
        return job_record

    @staticmethod
    def upsert_track_result(
        *,
        job_record: AudioEnrichmentJobRecord,
        track: Track,
        payload: dict[str, Any],
    ) -> AudioEnrichmentTrackResult:
        """Создаёт или обновляет track-result."""
        result, _ = AudioEnrichmentTrackResult.objects.update_or_create(
            job_record=job_record,
            track=track,
            defaults=payload,
        )
        return result

    @staticmethod
    def _build_output_template(temp_dir: str) -> str:
        return os.path.join(temp_dir, "%(id)s.%(ext)s")

    @staticmethod
    def _find_downloaded_mp3(temp_dir: str) -> Path:
        candidates = sorted(
            Path(temp_dir).glob("*.mp3"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError("yt-dlp не создал итоговый mp3-файл.")
        return candidates[0]

    @staticmethod
    def _build_file_name(track: Track) -> str:
        base_name = slugify(track.title or "audio") or "audio"
        return f"{base_name}.mp3"

    @staticmethod
    def _format_duration_seconds(total_seconds: int) -> str:
        total_seconds = max(0, int(total_seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    @classmethod
    def _extract_track_duration(cls, info: dict[str, Any] | None) -> str | None:
        if not isinstance(info, dict):
            return None

        raw_duration = info.get("duration")
        if isinstance(raw_duration, (int, float)):
            return cls._format_duration_seconds(round(raw_duration))
        if isinstance(raw_duration, str):
            try:
                return cls._format_duration_seconds(round(float(raw_duration)))
            except ValueError:
                pass

        raw_duration_string = str(info.get("duration_string") or "").strip()
        if _DURATION_STRING_RE.match(raw_duration_string):
            return raw_duration_string
        return None

    @classmethod
    def _resolve_cookie_file(cls) -> str | None:
        configured_path = str(
            getattr(settings, "YOUTUBE_COOKIE_FILE", "") or ""
        ).strip()
        if not configured_path:
            return None
        cookie_path = Path(configured_path)
        if cookie_path.is_file():
            return str(cookie_path)
        return None

    @classmethod
    def _resolve_cookies_from_browser(
        cls,
    ) -> tuple[str, str, str | None, None] | None:
        return YouTubeSessionService.resolve_cookies_from_browser()

    @classmethod
    def _resolve_js_runtimes(cls) -> dict[str, dict[str, str]]:
        runtime_name = (
            str(getattr(settings, "YOUTUBE_JS_RUNTIME", "") or "").strip().lower()
        )
        if not runtime_name:
            return {}

        configured_path = str(
            getattr(settings, "YOUTUBE_JS_RUNTIME_PATH", "") or ""
        ).strip()
        runtime_path = ""
        if configured_path:
            candidate = Path(configured_path)
            if candidate.is_file():
                runtime_path = str(candidate)
        if not runtime_path:
            runtime_path = shutil.which(runtime_name) or ""
        if not runtime_path:
            return {}

        return {runtime_name: {"path": runtime_path}}

    @classmethod
    def _resolve_remote_components(cls) -> list[str]:
        configured_value = getattr(settings, "YOUTUBE_REMOTE_COMPONENTS", []) or []
        if not isinstance(configured_value, list):
            return []

        allowed_components = {"ejs:github", "ejs:npm"}
        resolved: list[str] = []
        for raw_value in configured_value:
            value = str(raw_value or "").strip().lower()
            if value in allowed_components and value not in resolved:
                resolved.append(value)
        return resolved

    @classmethod
    def _resolve_cache_dir(cls) -> str | None:
        configured_path = str(
            getattr(settings, "YOUTUBE_YTDLP_CACHE_DIR", "") or ""
        ).strip()
        if not configured_path:
            return None
        cache_path = Path(configured_path)
        cache_path.mkdir(parents=True, exist_ok=True)
        return str(cache_path)

    @classmethod
    def _build_ydl_options(cls, temp_dir: str) -> dict[str, Any]:
        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "outtmpl": cls._build_output_template(temp_dir),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "restrictfilenames": True,
            "overwrites": True,
            "nopart": True,
            "prefer_ffmpeg": True,
            "logger": _YTDLPLogger(),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        cookies_from_browser = cls._resolve_cookies_from_browser()
        if cookies_from_browser:
            options["cookiesfrombrowser"] = cookies_from_browser
        else:
            cookie_file = cls._resolve_cookie_file()
            if cookie_file:
                options["cookiefile"] = cookie_file

        js_runtimes = cls._resolve_js_runtimes()
        if js_runtimes:
            options["js_runtimes"] = js_runtimes

        remote_components = cls._resolve_remote_components()
        if remote_components:
            options["remote_components"] = remote_components

        cache_dir = cls._resolve_cache_dir()
        if cache_dir:
            options["cachedir"] = cache_dir

        return options

    @classmethod
    def _build_authentication_error(cls) -> YouTubeAuthenticationRequiredError:
        configured_path = str(
            getattr(settings, "YOUTUBE_COOKIE_FILE", "") or ""
        ).strip()
        profile_dir = str(
            getattr(settings, "YOUTUBE_BROWSER_PROFILE_DIR", "") or ""
        ).strip()
        if configured_path:
            message = (
                "YouTube потребовал cookies для доступа к ролику. "
                f"Проверьте профиль браузера {profile_dir or '—'} "
                f"или файл cookies.txt: {configured_path}."
            )
        else:
            message = (
                "YouTube потребовал cookies для доступа к ролику. "
                "Укажите путь к browser profile или к YOUTUBE_COOKIE_FILE."
            )
        return YouTubeAuthenticationRequiredError(message)

    @classmethod
    def download_audio_to_track(
        cls,
        *,
        track: Track,
        overwrite: bool = False,
    ) -> str | None:
        """Скачивает аудио из YouTube и сохраняет его в `track.audio_preview`."""
        youtube_url = str(track.youtube_url or "").strip()
        if not youtube_url:
            return None

        existing_name = str(getattr(track.audio_preview, "name", "") or "").strip()
        if existing_name and not overwrite:
            return existing_name

        with tempfile.TemporaryDirectory(prefix="yt-audio-") as temp_dir:
            ydl_options = cls._build_ydl_options(temp_dir)
            try:
                with YoutubeDL(ydl_options) as ydl:
                    info = ydl.extract_info(youtube_url, download=True)
            except DownloadError as exc:
                error_text = str(exc)
                if "Sign in to confirm you" in error_text:
                    YouTubeSessionService.mark_state_auth_required(error_text)
                    log_event(
                        logger,
                        logging.DEBUG,
                        "YouTube запросил повторную авторизацию во время скачивания.",
                        component=_YOUTUBE_AUDIO_COMPONENT,
                        event="download_auth_required",
                        record_id=track.record_id,
                        track_id=track.id,
                        youtube_url=youtube_url,
                    )
                    raise cls._build_authentication_error() from exc
                raise RuntimeError(f"yt-dlp не смог скачать аудио: {exc}") from exc
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "Не найден ffmpeg. Установите ffmpeg в runtime worker."
                ) from exc

            mp3_path = cls._find_downloaded_mp3(temp_dir)
            old_name = existing_name
            file_name = cls._build_file_name(track)
            if not str(track.duration or "").strip():
                resolved_duration = cls._extract_track_duration(info)
                if resolved_duration:
                    track.duration = resolved_duration
            with mp3_path.open("rb") as file_handle:
                track.audio_preview.save(file_name, File(file_handle), save=True)
            saved_name = str(getattr(track.audio_preview, "name", "") or "").strip()
            YouTubeSessionService.mark_state_healthy(
                "YouTube-сессия подтверждена успешной загрузкой аудио."
            )
            if overwrite and old_name and old_name != saved_name:
                try:
                    track.audio_preview.storage.delete(old_name)
                except Exception as exc:  # noqa: BLE001
                    log_event(
                        logger,
                        logging.WARNING,
                        "Не удалось удалить прежний mp3 после обновления трека.",
                        component=_YOUTUBE_AUDIO_COMPONENT,
                        event="old_audio_delete_failed",
                        record_id=track.record_id,
                        track_id=track.pk,
                        old_audio=old_name,
                        error=str(exc),
                    )
            return saved_name or None
