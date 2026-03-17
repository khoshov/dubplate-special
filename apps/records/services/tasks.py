from __future__ import annotations

import logging
import uuid
from typing import Any

from celery import shared_task
from django.conf import settings
from django.db.models import Count, Sum
from django.utils import timezone

from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Record,
    Track,
)
from config.logging import NOTICE_LEVEL, build_log_extra, log_event
from records.services.audio.audio_service import AudioService
from records.services.audio.providers.youtube_audio_enrichment import (
    YouTubeAuthenticationRequiredError,
    YouTubeAudioEnrichmentProvider,
)

logger = logging.getLogger(__name__)
_YOUTUBE_AUDIO_COMPONENT = "youtube_audio"
_YOUTUBE_SESSION_COMPONENT = "youtube_session"


def _log_youtube_audio_event(
    level: int,
    event: str,
    message: str,
    **context: Any,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_YOUTUBE_AUDIO_COMPONENT,
        event=event,
        **context,
    )


def _log_youtube_session_event(
    level: int,
    event: str,
    message: str,
    **context: Any,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_YOUTUBE_SESSION_COMPONENT,
        event=event,
        **context,
    )


def _maybe_refresh_youtube_session(
    *,
    job_id: uuid.UUID | str | None,
    track: Track,
    overwrite_existing: bool,
    audio_service: AudioService,
) -> tuple[str | None, int, Exception | None]:
    if not bool(getattr(settings, "YOUTUBE_SESSION_RECOVERY_RETRY_ENABLED", True)):
        return None, 0, None

    _log_youtube_session_event(
        NOTICE_LEVEL,
        "auth_recovery_start",
        "YouTube запросил повторную авторизацию. Запущено обновление сессии.",
        job_id=job_id,
        record_id=track.record_id,
        track_id=track.id,
        overwrite=overwrite_existing,
        youtube_url=track.youtube_url,
    )
    refresh_result = audio_service.refresh_youtube_session()
    _log_youtube_session_event(
        logging.DEBUG,
        "auth_recovery_result",
        "Получен результат обновления YouTube-сессии.",
        job_id=job_id,
        record_id=track.record_id,
        track_id=track.id,
        refreshed=refresh_result.refreshed,
        profile_ready=refresh_result.profile_ready,
        waited=refresh_result.waited_for_existing_refresh,
        seeded=refresh_result.seeded_from_cookie_file,
        details=refresh_result.message or "—",
    )
    if not (refresh_result.refreshed or refresh_result.waited_for_existing_refresh):
        _log_youtube_session_event(
            logging.WARNING,
            "auth_recovery_failed",
            "Не удалось обновить YouTube-сессию после запроса авторизации.",
            job_id=job_id,
            record_id=track.record_id,
            track_id=track.id,
            overwrite=overwrite_existing,
            details=refresh_result.message or "—",
        )
        return (
            None,
            0,
            RuntimeError(
                refresh_result.message or "Не удалось обновить YouTube-сессию."
            ),
        )

    def _download_after_refresh() -> str | None:
        return audio_service.download_audio_from_youtube(
            track=track,
            overwrite=overwrite_existing,
        )

    return YouTubeAudioEnrichmentProvider.download_with_retry(
        operation=_download_after_refresh,
        max_attempts=1,
        base_delay_sec=0,
    )


def _process_track_for_youtube_enrichment(
    *,
    job_id: uuid.UUID | str | None = None,
    source: str = AudioEnrichmentJob.Source.MANUAL_RECORD,
    track: Track,
    overwrite_existing: bool,
) -> dict[str, Any]:
    provider = YouTubeAudioEnrichmentProvider
    previous_audio_present = bool(track.audio_preview)
    previous_audio_name = str(getattr(track.audio_preview, "name", "") or "").strip()
    previous_duration = str(track.duration or "").strip() or "—"
    youtube_url = str(track.youtube_url or "").strip()
    log_context = {
        "job_id": job_id,
        "record_id": track.record_id,
        "track_id": track.id,
        "source": source,
        "overwrite": overwrite_existing,
        "title": track.title,
    }

    if not youtube_url:
        payload = provider.serialize_track_result(
            status=AudioEnrichmentTrackResult.Status.SKIPPED,
            reason_code=AudioEnrichmentTrackResult.Reason.MISSING_YOUTUBE_URL,
            previous_audio_present=previous_audio_present,
        )
        _log_youtube_audio_event(
            NOTICE_LEVEL,
            "track_skip",
            "Трек пропущен: ссылка на YouTube отсутствует.",
            reason=payload["reason_code"],
            **log_context,
        )
        return payload

    if not provider.is_valid_youtube_url(youtube_url):
        payload = provider.serialize_track_result(
            status=AudioEnrichmentTrackResult.Status.SKIPPED,
            reason_code=AudioEnrichmentTrackResult.Reason.INVALID_URL,
            previous_audio_present=previous_audio_present,
        )
        _log_youtube_audio_event(
            logging.WARNING,
            "track_skip",
            "Трек пропущен: ссылка на YouTube не прошла валидацию.",
            reason=payload["reason_code"],
            youtube_url=youtube_url,
            **log_context,
        )
        return payload

    if previous_audio_present and not overwrite_existing:
        payload = provider.serialize_track_result(
            status=AudioEnrichmentTrackResult.Status.SKIPPED,
            reason_code=AudioEnrichmentTrackResult.Reason.ALREADY_PRESENT,
            previous_audio_present=previous_audio_present,
        )
        _log_youtube_audio_event(
            NOTICE_LEVEL,
            "track_skip",
            "Трек пропущен: локальный mp3 уже прикреплён, повторная загрузка отключена.",
            reason=payload["reason_code"],
            **log_context,
        )
        _log_youtube_audio_event(
            logging.DEBUG,
            "track_skip_details",
            "Детали пропуска трека YouTube-задачей.",
            attempts=payload["attempts"],
            previous_audio_present=payload["previous_audio_present"],
            old_audio=previous_audio_name or "—",
            **log_context,
        )
        return payload

    audio_service = AudioService()
    _log_youtube_audio_event(
        logging.DEBUG,
        "track_download_start",
        (
            "Запущена загрузка аудио из YouTube."
            if not previous_audio_present
            else "Запущена замена существующего mp3 данными из YouTube."
        ),
        previous_audio_present=previous_audio_present,
        old_audio=previous_audio_name or "—",
        duration_before=previous_duration,
        youtube_url=youtube_url,
        **log_context,
    )

    def _download() -> str | None:
        return audio_service.download_audio_from_youtube(
            track=track,
            overwrite=overwrite_existing,
        )

    final_audio_name, attempts, last_error = provider.download_with_retry(
        operation=_download,
        max_attempts=3,
        base_delay_sec=1.0,
    )
    if final_audio_name is None and isinstance(
        last_error, YouTubeAuthenticationRequiredError
    ):
        refreshed_audio_name, refresh_attempts, refresh_error = (
            _maybe_refresh_youtube_session(
                job_id=job_id,
                track=track,
                overwrite_existing=overwrite_existing,
                audio_service=audio_service,
            )
        )
        attempts += refresh_attempts
        if refreshed_audio_name:
            final_audio_name = refreshed_audio_name
            last_error = None
        elif refresh_error is not None:
            last_error = refresh_error

    if final_audio_name:
        track.refresh_from_db(fields=["audio_preview", "duration"])
        duration_after = str(track.duration or "").strip() or "—"
        payload = provider.serialize_track_result(
            status=AudioEnrichmentTrackResult.Status.UPDATED,
            attempts=attempts,
            previous_audio_present=previous_audio_present,
            final_audio_name=final_audio_name,
        )
        _log_youtube_audio_event(
            logging.INFO,
            "track_updated",
            "Трек обновлён: mp3 сохранён из YouTube.",
            attempts=payload["attempts"],
            **log_context,
        )
        _log_youtube_audio_event(
            logging.DEBUG,
            "track_updated_details",
            "Детали успешного обновления трека из YouTube.",
            status=payload["status"],
            attempts=payload["attempts"],
            previous_audio_present=payload["previous_audio_present"],
            old_audio=previous_audio_name or "—",
            new_audio=final_audio_name,
            duration_before=previous_duration,
            duration_after=duration_after,
            **log_context,
        )
        return payload

    failure_reason = AudioEnrichmentTrackResult.Reason.RETRY_EXHAUSTED
    if isinstance(last_error, YouTubeAuthenticationRequiredError) or attempts < 3:
        failure_reason = AudioEnrichmentTrackResult.Reason.DOWNLOAD_ERROR

    payload = provider.serialize_track_result(
        status=AudioEnrichmentTrackResult.Status.FAILED,
        reason_code=failure_reason,
        attempts=attempts,
        previous_audio_present=previous_audio_present,
        error_message=str(last_error or "YouTube download failed"),
    )
    _log_youtube_audio_event(
        logging.ERROR,
        "track_failed",
        "Не удалось обновить трек аудио из YouTube.",
        reason=payload["reason_code"],
        attempts=payload["attempts"],
        **log_context,
    )
    _log_youtube_audio_event(
        logging.DEBUG,
        "track_failed_details",
        "Детали ошибки обновления трека из YouTube.",
        reason=payload["reason_code"],
        attempts=payload["attempts"],
        previous_audio_present=payload["previous_audio_present"],
        old_audio=previous_audio_name or "—",
        error=payload["error_message"],
        **log_context,
    )
    return payload


def _refresh_job_status(job: AudioEnrichmentJob) -> AudioEnrichmentJob:
    aggregate = job.job_records.aggregate(
        updated_sum=Sum("updated_count"),
        skipped_sum=Sum("skipped_count"),
        error_sum=Sum("error_count"),
        record_total=Count("id"),
    )
    track_total = job.job_records.aggregate(track_total=Count("track_results"))[
        "track_total"
    ]
    statuses = set(job.job_records.values_list("status", flat=True))

    job.total_records = int(aggregate["record_total"] or 0)
    job.total_tracks = int(track_total or 0)
    job.updated_count = int(aggregate["updated_sum"] or 0)
    job.skipped_count = int(aggregate["skipped_sum"] or 0)
    job.error_count = int(aggregate["error_sum"] or 0)

    active_statuses = {
        AudioEnrichmentJobRecord.Status.QUEUED,
        AudioEnrichmentJobRecord.Status.RUNNING,
    }
    if statuses & active_statuses:
        job.status = AudioEnrichmentJob.Status.RUNNING
        if job.started_at is None:
            job.started_at = timezone.now()
        job.finished_at = None
    elif not statuses:
        job.status = AudioEnrichmentJob.Status.QUEUED
        job.finished_at = None
    elif AudioEnrichmentJobRecord.Status.FAILED in statuses:
        job.status = AudioEnrichmentJob.Status.COMPLETED_WITH_ERRORS
        job.finished_at = timezone.now()
    elif {
        AudioEnrichmentJobRecord.Status.COMPLETED_WITH_ERRORS,
        AudioEnrichmentJobRecord.Status.SKIPPED,
    } & statuses:
        job.status = AudioEnrichmentJob.Status.COMPLETED_WITH_ERRORS
        job.finished_at = timezone.now()
    else:
        job.status = AudioEnrichmentJob.Status.COMPLETED
        job.finished_at = timezone.now()

    job.save(
        update_fields=[
            "status",
            "total_records",
            "total_tracks",
            "updated_count",
            "skipped_count",
            "error_count",
            "started_at",
            "finished_at",
            "modified",
        ]
    )
    return job


@shared_task(name="records.youtube_enrichment.run_job")
def run_youtube_enrichment_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Ставит fan-out обработку job по списку записей."""
    audio_service = AudioService()
    parsed = audio_service.parse_run_job_payload(payload)

    job = AudioEnrichmentJob.objects.get(pk=parsed.job_id)
    job.source = parsed.source
    job.overwrite_existing = parsed.overwrite_existing
    job.requested_by_user_id = parsed.requested_by_user_id
    job.status = AudioEnrichmentJob.Status.RUNNING
    if job.started_at is None:
        job.started_at = timezone.now()
    job.save(
        update_fields=[
            "source",
            "overwrite_existing",
            "requested_by_user",
            "status",
            "started_at",
            "modified",
        ]
    )
    _log_youtube_audio_event(
        logging.INFO,
        "job_start",
        "===== Запущена задача обновления аудио из YouTube =====",
        job_id=job.id,
        source=parsed.source,
        overwrite=parsed.overwrite_existing,
        records_total=len(parsed.record_ids),
        requested_by_user_id=parsed.requested_by_user_id,
    )

    queued_records = 0
    skipped_records = 0
    missing_records = 0
    for record_id in parsed.record_ids:
        record = Record.objects.filter(pk=record_id).first()
        if record is None:
            missing_records += 1
            _log_youtube_audio_event(
                logging.WARNING,
                "job_record_missing",
                "Запись для YouTube-обработки не найдена.",
                job_id=job.id,
                record_id=record_id,
                source=parsed.source,
                overwrite=parsed.overwrite_existing,
            )
            continue

        job_record, can_process = audio_service.acquire_youtube_record_lock(
            job=job,
            record=record,
        )
        if not can_process:
            skipped_records += 1
            _log_youtube_audio_event(
                NOTICE_LEVEL,
                "record_skip",
                "Запись пропущена: уже обрабатывается другой YouTube-задачей.",
                job_id=job.id,
                record_id=record.pk,
                source=parsed.source,
                overwrite=parsed.overwrite_existing,
                reason=job_record.reason_code or "already_running",
            )
            continue

        queued_records += 1
        process_youtube_enrichment_record.delay(
            {
                "job_id": str(parsed.job_id),
                "record_id": record.pk,
                "overwrite_existing": parsed.overwrite_existing,
            }
        )

    refreshed = _refresh_job_status(job)
    _log_youtube_audio_event(
        logging.INFO,
        "job_finish",
        (
            "===== Задача обновления аудио из YouTube поставлена на обработку: "
            f"queued={queued_records}, skipped={skipped_records}, missing={missing_records} ====="
        ),
        job_id=refreshed.id,
        source=parsed.source,
        overwrite=parsed.overwrite_existing,
        status=refreshed.status,
    )
    return {
        "job_id": str(refreshed.id),
        "status": refreshed.status,
        "queued_records": queued_records,
        "skipped_records": skipped_records,
        "missing_records": missing_records,
    }


@shared_task(name="records.youtube_enrichment.process_record")
def process_youtube_enrichment_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Обрабатывает одну запись и обновляет детальный job report."""
    audio_service = AudioService()
    parsed = audio_service.parse_process_record_payload(payload)

    job = AudioEnrichmentJob.objects.get(pk=parsed.job_id)
    record = Record.objects.get(pk=parsed.record_id)
    job_record, can_process = audio_service.acquire_youtube_record_lock(
        job=job,
        record=record,
    )
    if not can_process:
        _refresh_job_status(job)
        _log_youtube_audio_event(
            NOTICE_LEVEL,
            "record_skip",
            "Запись пропущена: уже выполняется другой job-record.",
            job_id=job.id,
            record_id=record.pk,
            source=job.source,
            overwrite=parsed.overwrite_existing,
            reason=job_record.reason_code
            or AudioEnrichmentJobRecord.Reason.ALREADY_RUNNING,
        )
        return {
            "job_id": str(job.id),
            "record_id": record.pk,
            "status": AudioEnrichmentJobRecord.Status.SKIPPED,
            "reason_code": job_record.reason_code
            or AudioEnrichmentJobRecord.Reason.ALREADY_RUNNING,
        }

    audio_service.mark_youtube_record_running(job_record)
    _log_youtube_audio_event(
        logging.INFO,
        "record_start",
        "----- Запущена обработка записи для обновления аудио из YouTube -----",
        job_id=job.id,
        record_id=record.pk,
        source=job.source,
        overwrite=parsed.overwrite_existing,
        title=record.title,
        tracks_total=record.tracks.count(),
    )

    updated_count = skipped_count = error_count = 0
    try:
        tracks = list(record.tracks.order_by("position_index", "id"))
        for track in tracks:
            track_payload = _process_track_for_youtube_enrichment(
                job_id=job.id,
                source=job.source,
                track=track,
                overwrite_existing=parsed.overwrite_existing,
            )
            YouTubeAudioEnrichmentProvider.upsert_track_result(
                job_record=job_record,
                track=track,
                payload=track_payload,
            )

            if track_payload["status"] == AudioEnrichmentTrackResult.Status.UPDATED:
                updated_count += 1
            elif track_payload["status"] == AudioEnrichmentTrackResult.Status.SKIPPED:
                skipped_count += 1
            else:
                error_count += 1

        audio_service.mark_youtube_record_finished(
            job_record=job_record,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
        )
        refreshed_job_record = AudioEnrichmentJobRecord.objects.get(pk=job_record.pk)
        _log_youtube_audio_event(
            logging.INFO,
            "record_finish",
            (
                "----- Обработка записи для YouTube завершена: "
                f"updated={refreshed_job_record.updated_count}, "
                f"skipped={refreshed_job_record.skipped_count}, "
                f"failed={refreshed_job_record.error_count} -----"
            ),
            job_id=job.id,
            record_id=record.pk,
            source=job.source,
            overwrite=parsed.overwrite_existing,
            status=refreshed_job_record.status,
        )
    except Exception as exc:  # noqa: BLE001
        _log_youtube_audio_event(
            logging.ERROR,
            "record_failed",
            "Во время обработки записи произошла ошибка YouTube-блока.",
            job_id=job.id,
            record_id=record.pk,
            source=job.source,
            overwrite=parsed.overwrite_existing,
            error=str(exc),
        )
        logger.exception(
            "Стектрейс ошибки обработки записи YouTube-блоком.",
            extra=build_log_extra(
                component=_YOUTUBE_AUDIO_COMPONENT,
                event="record_failed_traceback",
                job_id=job.id,
                record_id=record.pk,
                source=job.source,
                overwrite=parsed.overwrite_existing,
            ),
        )
        error_count = max(error_count, 1)
        audio_service.mark_youtube_record_finished(
            job_record=job_record,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
            force_failed=True,
            reason_code=AudioEnrichmentJobRecord.Reason.VALIDATION_ERROR,
        )

    refreshed = _refresh_job_status(job)
    refreshed_job_record = AudioEnrichmentJobRecord.objects.get(pk=job_record.pk)
    return {
        "job_id": str(refreshed.id),
        "record_id": record.pk,
        "status": refreshed_job_record.status,
        "updated_count": refreshed_job_record.updated_count,
        "skipped_count": refreshed_job_record.skipped_count,
        "error_count": refreshed_job_record.error_count,
    }


@shared_task(name="records.youtube_session.refresh", queue="youtube_session")
def refresh_youtube_session_profile() -> dict[str, Any]:
    """Обновляет persistent browser profile для YouTube."""
    result = AudioService.refresh_youtube_session()
    _log_youtube_session_event(
        logging.INFO,
        "refresh_task_finish",
        "Задача обновления YouTube-сессии завершена.",
        refreshed=result.refreshed,
        profile_ready=result.profile_ready,
        waited=result.waited_for_existing_refresh,
        seeded=result.seeded_from_cookie_file,
    )
    return {
        "refreshed": result.refreshed,
        "profile_ready": result.profile_ready,
        "waited_for_existing_refresh": result.waited_for_existing_refresh,
        "seeded_from_cookie_file": result.seeded_from_cookie_file,
        "message": result.message,
    }


@shared_task(name="records.youtube_session.login", queue="youtube_session_login")
def login_youtube_session_profile(timeout_sec: int = 600) -> dict[str, Any]:
    """Запускает интерактивную авторизацию YouTube в headful browser profile."""
    result = AudioService.login_youtube_session(timeout_ms=max(1, timeout_sec) * 1000)
    _log_youtube_session_event(
        logging.INFO if result.logged_in else logging.WARNING,
        "login_task_finish",
        (
            "Задача интерактивной авторизации YouTube-сессии завершена успешно."
            if result.logged_in
            else "Задача интерактивной авторизации YouTube-сессии завершилась без подтверждения входа."
        ),
        logged_in=result.logged_in,
        profile_ready=result.profile_ready,
        waited=result.waited_for_existing_refresh,
        timed_out=result.timed_out,
    )
    return {
        "logged_in": result.logged_in,
        "profile_ready": result.profile_ready,
        "waited_for_existing_refresh": result.waited_for_existing_refresh,
        "timed_out": result.timed_out,
        "message": result.message,
    }
