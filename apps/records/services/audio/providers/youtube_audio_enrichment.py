from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

from django.db import IntegrityError, transaction
from django.utils import timezone

from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Record,
    Track,
)


class PayloadValidationError(ValueError):
    """Ошибка валидации payload для очереди задач."""


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


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


@dataclass(frozen=True)
class RunJobPayload:
    """Контракт payload для задачи запуска обработки job."""

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
                "Поле 'record_ids' должно быть непустым списком целых ID."
            )

        record_ids = tuple(
            _ensure_int(value, field="record_ids") for value in raw_record_ids
        )
        overwrite_existing = bool(payload.get("overwrite_existing", False))

        requested_by_user_id_raw = payload.get("requested_by_user_id")
        requested_by_user_id = (
            None
            if requested_by_user_id_raw in (None, "")
            else _ensure_int(requested_by_user_id_raw, field="requested_by_user_id")
        )

        source = str(payload.get("source", "")).strip()
        if source not in set(AudioEnrichmentJob.Source.values):
            raise PayloadValidationError(
                "Поле 'source' должно быть одним из значений AudioEnrichmentJob.Source."
            )

        return cls(
            job_id=job_id,
            record_ids=record_ids,
            overwrite_existing=overwrite_existing,
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


class YouTubeAudioEnrichmentProvider:
    """Провайдер foundation-операций для YouTube-аудио-обогащения."""

    logger = logging.getLogger(__name__)

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
    def strict_match(
        cls,
        *,
        track_title: str,
        candidate_title: str,
        track_artists: Sequence[str],
        candidate_artists: Sequence[str],
    ) -> tuple[bool, bool, bool]:
        """Проверяет строгий матчинг: title и минимум один артист."""
        title_matched = _normalize_text(track_title) == _normalize_text(candidate_title)
        track_artist_set = {_normalize_text(value) for value in track_artists if value}
        candidate_artist_set = {
            _normalize_text(value) for value in candidate_artists if value
        }
        artist_matched = bool(track_artist_set & candidate_artist_set)
        return title_matched and artist_matched, title_matched, artist_matched

    @classmethod
    def is_valid_youtube_url(cls, value: str | None) -> bool:
        """Проверяет, что строка является допустимой YouTube-ссылкой."""
        if not value:
            return False
        try:
            parsed = urlparse(value.strip())
        except ValueError:
            return False

        if parsed.scheme not in {"http", "https"}:
            return False
        hostname = (parsed.hostname or "").lower()
        return hostname in cls.VALID_YOUTUBE_HOSTS

    @staticmethod
    def download_with_retry(
        *,
        operation: Callable[[], str | None],
        max_attempts: int = 3,
        base_delay_sec: float = 1.0,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> tuple[str | None, int, Exception | None]:
        """Выполняет operation с retry и экспоненциальным backoff."""
        attempt = 0
        last_error: Exception | None = None
        while attempt < max_attempts:
            attempt += 1
            try:
                result = operation()
                if result:
                    return result, attempt, None
                raise RuntimeError("Audio download returned empty result.")
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
        matched_title: bool = False,
        matched_artist: bool = False,
        previous_audio_present: bool = False,
        final_audio_name: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        """Сериализует payload результата трека для update_or_create."""
        safe_attempts = max(1, min(3, int(attempts)))
        return {
            "status": status,
            "reason_code": reason_code or AudioEnrichmentTrackResult.Reason.NONE,
            "attempts": safe_attempts,
            "matched_title": matched_title,
            "matched_artist": matched_artist,
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
        matched_title: bool,
        matched_artist: bool,
        previous_audio_present: bool,
    ) -> None:
        """Логирует структурированный итог обработки трека."""
        cls.logger.info(
            "youtube_enrichment_track_outcome "
            "record_id=%s track_id=%s status=%s reason=%s attempts=%s "
            "matched_title=%s matched_artist=%s previous_audio_present=%s",
            record_id,
            track_id,
            status,
            reason_code or "none",
            attempts,
            matched_title,
            matched_artist,
            previous_audio_present,
        )

    @staticmethod
    def serialize_record_counters(
        *,
        updated_count: int,
        skipped_count: int,
        error_count: int,
    ) -> dict[str, int]:
        """Сериализует счётчики результатов записи."""
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
        """
        Захватывает обработку записи в рамках job.

        Возвращает:
          - job_record
          - bool: True, если запись можно обрабатывать; False, если `already_running`.
        """
        existing = AudioEnrichmentJobRecord.objects.filter(
            job=job, record=record
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
        """Переводит запись job в состояние running."""
        if job_record.status != AudioEnrichmentJobRecord.Status.RUNNING:
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
        """Фиксирует завершение обработки записи с итоговым статусом и счётчиками."""
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
        """Создаёт или обновляет результат обработки трека."""
        result, _ = AudioEnrichmentTrackResult.objects.update_or_create(
            job_record=job_record,
            track=track,
            defaults=payload,
        )
        return result
