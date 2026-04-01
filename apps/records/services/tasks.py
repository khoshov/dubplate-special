from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

import yt_dlp
import requests
from celery import shared_task
from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Record,
    Track,
    VKPublicationJob,
    VKPublicationJobRecord,
)
from config.logging import NOTICE_LEVEL, build_log_extra, log_event
from records.services.audio.audio_service import AudioService
from records.services.audio.providers.youtube_audio_enrichment import (
    YouTubeAuthenticationRequiredError,
    YouTubeAudioEnrichmentProvider,
)
from records.services.social.vk_service import VKService
from vk_api.exceptions import ApiError

logger = logging.getLogger(__name__)
_YOUTUBE_AUDIO_COMPONENT = "youtube_audio"
_YOUTUBE_SESSION_COMPONENT = "youtube_session"
_YOUTUBE_SEARCH_COMPONENT = "youtube_search"
_VK_PUBLICATION_COMPONENT = "vk_publication"


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


def _log_youtube_search_event(
    level: int,
    event: str,
    message: str,
    **context: Any,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_YOUTUBE_SEARCH_COMPONENT,
        event=event,
        **context,
    )


def _log_vk_publication_event(
    level: int,
    event: str,
    message: str,
    **context: Any,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_VK_PUBLICATION_COMPONENT,
        event=event,
        **context,
    )


def _build_release_report_track_item(
    *,
    track: Track,
    status: str,
    message: str,
    source_label: str | None = None,
) -> dict[str, str]:
    resolved_source_label = (
        source_label or track.get_audio_source_display() or "Не указан"
    )
    return {
        "track_id": str(track.pk),
        "track_title": str(track.title),
        "action": "Добавление аудио к треку",
        "status": status,
        "source": resolved_source_label,
        "message": message,
    }


def _refresh_vk_publication_job_status(job: VKPublicationJob) -> VKPublicationJob:
    aggregate = job.job_records.aggregate(
        success_sum=Count(
            "id",
            filter=Q(
                status__in=(
                    VKPublicationJobRecord.Status.COMPLETED,
                    VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS,
                )
            ),
        ),
        error_sum=Count("id", filter=Q(status=VKPublicationJobRecord.Status.FAILED)),
        record_total=Count("id"),
    )
    statuses = set(job.job_records.values_list("status", flat=True))

    job.total_records = int(aggregate["record_total"] or 0)
    job.success_count = int(aggregate["success_sum"] or 0)
    job.error_count = int(aggregate["error_sum"] or 0)
    job.skipped_count = 0

    active_statuses = {
        VKPublicationJobRecord.Status.QUEUED,
        VKPublicationJobRecord.Status.RUNNING,
    }
    if statuses & active_statuses:
        job.status = VKPublicationJob.Status.RUNNING
        if job.started_at is None:
            job.started_at = timezone.now()
        job.finished_at = None
    elif not statuses:
        job.status = VKPublicationJob.Status.QUEUED
        job.finished_at = None
    elif (
        VKPublicationJobRecord.Status.FAILED in statuses
        or VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS in statuses
    ):
        job.status = VKPublicationJob.Status.COMPLETED_WITH_ERRORS
        job.finished_at = timezone.now()
    else:
        job.status = VKPublicationJob.Status.COMPLETED
        job.finished_at = timezone.now()

    job.save(
        update_fields=[
            "status",
            "total_records",
            "success_count",
            "skipped_count",
            "error_count",
            "started_at",
            "finished_at",
            "modified",
        ]
    )
    return job


def _parse_vk_run_job_payload(
    payload: dict[str, Any],
) -> tuple[uuid.UUID, int | None, str]:
    return (
        uuid.UUID(str(payload["job_id"])),
        (
            int(payload["requested_by_user_id"])
            if payload.get("requested_by_user_id") is not None
            else None
        ),
        str(payload["source"]),
    )


def _parse_vk_process_record_payload(
    payload: dict[str, Any],
) -> tuple[uuid.UUID, uuid.UUID]:
    return (
        uuid.UUID(str(payload["job_id"])),
        uuid.UUID(str(payload["job_record_id"])),
    )


def _get_vk_retry_delta(
    planned_publish_at: datetime | None,
    job: VKPublicationJob,
) -> timedelta:
    if planned_publish_at is None:
        return timedelta(minutes=5)
    if job.total_records <= 1:
        return timedelta(minutes=5)
    return timedelta(minutes=30)


def _resolve_vk_release_source_name(record: Record) -> str:
    if record.sources.filter(provider="redeye").exists():
        return "Redeye"
    if record.sources.filter(provider="discogs").exists() or bool(record.discogs_id):
        return "Discogs"
    return "Не указан"


def _resolve_vk_audio_source_summary(record: Record) -> str:
    source_values = list(
        record.tracks.exclude(audio_source="unknown")
        .exclude(audio_source="")
        .values_list("audio_source", flat=True)
        .distinct()
    )
    labels_map = dict(Track.AudioSource.choices)
    labels = [str(labels_map.get(value, "")).strip() for value in source_values]
    labels = [label for label in labels if label]
    return ", ".join(labels) if labels else "Не указан"


def _build_vk_report_result(
    *,
    publication_result: Any,
    shifted: bool,
) -> tuple[str, str, str, str]:
    warning_parts: list[str] = []

    if shifted:
        warning_parts.append("Время публикации автоматически изменено.")

    if publication_result.photo_expected and not publication_result.photo_uploaded:
        warning_parts.append(
            "Изображение релиза не загружено, поэтому аудио не добавлялось."
        )
        return (
            VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS,
            "Изображение релиза не загружено, поэтому аудио не добавлялось",
            "Публикация выполнена только текстом: изображение релиза не загружено, аудио не добавлялось.",
            " ".join(warning_parts).strip(),
        )

    if publication_result.audio_failed_count > 0:
        warning_parts.append("Не все аудио удалось загрузить.")
        uploaded = int(publication_result.audio_uploaded_count or 0)
        expected = int(publication_result.audio_expected_count or 0)
        return (
            VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS,
            "Пост опубликован с изображением, но не со всеми аудио",
            f"Пост опубликован с изображением, аудио загружено {uploaded} из {expected}.",
            " ".join(warning_parts).strip(),
        )

    if (
        publication_result.photo_uploaded
        and publication_result.audio_uploaded_count > 0
    ):
        result_message = "Пост опубликован с изображением и аудио."
        return (
            (
                VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS
                if shifted
                else VKPublicationJobRecord.Status.COMPLETED
            ),
            "Пост опубликован с изображением и аудио",
            result_message,
            " ".join(warning_parts).strip(),
        )

    if publication_result.photo_uploaded:
        result_message = "Пост опубликован с изображением."
        return (
            (
                VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS
                if shifted
                else VKPublicationJobRecord.Status.COMPLETED
            ),
            "Пост опубликован с изображением",
            result_message,
            " ".join(warning_parts).strip(),
        )

    result_message = "Пост опубликован только текстом."
    return (
        VKPublicationJobRecord.Status.COMPLETED_WITH_WARNINGS,
        "Пост опубликован только текстом",
        result_message,
        " ".join(warning_parts).strip(),
    )


def _post_to_vk_with_retry(
    *,
    vk_service: VKService,
    record: Record,
    publish_at: datetime | None,
    delta: timedelta,
    max_retries: int,
) -> tuple[Any, datetime | None, bool]:
    prepared = vk_service.prepare_record_publication(record=record)
    attempts = 0
    current_at = publish_at
    shifted = False

    while True:
        try:
            publication_result = _publish_prepared_post_with_retry(
                vk_service=vk_service,
                record=record,
                prepared=prepared,
                publish_at=current_at,
            )
            return publication_result, current_at, shifted
        except ApiError as exc:
            if exc.code != 214 or current_at is None:
                raise
            attempts += 1
            shifted = True
            if attempts > max_retries:
                raise
            current_at = current_at + delta


def _publish_prepared_post_with_retry(
    *,
    vk_service: VKService,
    record: Record,
    prepared: Any,
    publish_at: datetime | None,
) -> Any:
    last_error: Exception | None = None
    max_attempts = len((2, 5, 10))

    for attempt, sleep_seconds in enumerate((2, 5, 10), start=1):
        try:
            return vk_service.publish_prepared_publication(
                record=record,
                prepared=prepared,
                publish_at=publish_at,
            )
        except ApiError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            _log_vk_publication_event(
                logging.WARNING,
                "wall_post_retry",
                "Попытка публикации поста в VK завершилась неудачно.",
                record_id=record.pk,
                publish_at=publish_at.isoformat() if publish_at else None,
                wall_post_attempt=attempt,
                wall_post_attempts_total=max_attempts,
                error=str(exc),
            )
            if attempt == max_attempts:
                break
            tasks_module_time_sleep(sleep_seconds)

    if last_error is None:
        raise RuntimeError("Не удалось опубликовать пост в VK: неизвестная ошибка.")
    raise last_error


def tasks_module_time_sleep(seconds: int) -> None:
    """Изолирует sleep, чтобы его было проще стабильно мокать в тестах."""
    import time

    time.sleep(seconds)


@shared_task(name="records.vk_publication.run_job")
def run_vk_publication_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Ставит fan-out обработку VK job по списку записей."""
    job_id, requested_by_user_id, source = _parse_vk_run_job_payload(payload)

    job = VKPublicationJob.objects.get(pk=job_id)
    job.source = source
    job.requested_by_user_id = requested_by_user_id
    job.status = VKPublicationJob.Status.RUNNING
    if job.started_at is None:
        job.started_at = timezone.now()
    job.save(
        update_fields=[
            "source",
            "requested_by_user",
            "status",
            "started_at",
            "modified",
        ]
    )
    _log_vk_publication_event(
        logging.INFO,
        "job_start",
        "===== Запущена задача публикации записей в VK =====",
        job_id=job.id,
        source=source,
        records_total=job.total_records,
        requested_by_user_id=requested_by_user_id,
    )

    queued_records = 0
    for job_record in job.job_records.select_related("record").order_by(
        "created", "id"
    ):
        queued_records += 1
        process_vk_publication_record.delay(
            {
                "job_id": str(job.id),
                "job_record_id": str(job_record.id),
            }
        )

    refreshed = _refresh_vk_publication_job_status(job)
    _log_vk_publication_event(
        logging.INFO,
        "job_enqueued",
        "===== Задача публикации записей в VK поставлена на обработку =====",
        job_id=refreshed.id,
        source=source,
        status=refreshed.status,
        queued_records=queued_records,
    )
    return {
        "job_id": str(refreshed.id),
        "status": refreshed.status,
        "queued_records": queued_records,
    }


@shared_task(name="records.vk_publication.process_record")
def process_vk_publication_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Публикует одну запись в VK и обновляет job report."""
    job_id, job_record_id = _parse_vk_process_record_payload(payload)

    job = VKPublicationJob.objects.get(pk=job_id)
    job_record = VKPublicationJobRecord.objects.select_related("record", "job").get(
        pk=job_record_id
    )
    record = job_record.record

    if job_record.status == VKPublicationJobRecord.Status.RUNNING:
        return {
            "job_id": str(job.id),
            "record_id": record.pk,
            "status": job_record.status,
        }

    job_record.status = VKPublicationJobRecord.Status.RUNNING
    job_record.stage = "Подготовка данных публикации"
    job_record.release_source_name = _resolve_vk_release_source_name(record)
    job_record.audio_source_summary = _resolve_vk_audio_source_summary(record)
    if job_record.started_at is None:
        job_record.started_at = timezone.now()
    job_record.save(
        update_fields=[
            "status",
            "stage",
            "release_source_name",
            "audio_source_summary",
            "started_at",
            "modified",
        ]
    )

    _log_vk_publication_event(
        logging.INFO,
        "record_start",
        "----- Запущена публикация записи в VK -----",
        job_id=job.id,
        job_record_id=job_record.id,
        record_id=record.pk,
        mode=job_record.mode,
        planned_publish_at=job_record.planned_publish_at.isoformat()
        if job_record.planned_publish_at
        else None,
    )

    try:
        vk_service = VKService.from_settings()
        delta = _get_vk_retry_delta(job_record.planned_publish_at, job)
        job_record.stage = "Публикация поста в VK"
        job_record.save(update_fields=["stage", "modified"])
        publication_result, final_publish_at, shifted = _post_to_vk_with_retry(
            vk_service=vk_service,
            record=record,
            publish_at=job_record.planned_publish_at,
            delta=delta,
            max_retries=10,
        )
        published_at = (
            final_publish_at or job_record.planned_publish_at or timezone.now()
        )
        Record.objects.filter(pk=record.pk).update(vk_published_at=published_at)
        result_status, result, result_message, warning_message = (
            _build_vk_report_result(
                publication_result=publication_result,
                shifted=shifted,
            )
        )
        job_record.status = result_status
        job_record.operation_name = (
            "Отложенная публикация в VK"
            if job_record.mode == VKPublicationJobRecord.Mode.SCHEDULED
            else "Публикация в VK"
        )
        job_record.result = result
        job_record.result_message = result_message
        job_record.warning_message = warning_message
        job_record.stage = "Завершение операции"
        job_record.photo_expected = publication_result.photo_expected
        job_record.photo_uploaded = publication_result.photo_uploaded
        job_record.audio_expected_count = publication_result.audio_expected_count
        job_record.audio_uploaded_count = publication_result.audio_uploaded_count
        job_record.audio_failed_count = publication_result.audio_failed_count
        job_record.failed_track_titles = publication_result.failed_track_titles
        job_record.audio_failure_details = publication_result.audio_failure_details
        job_record.effective_publish_at = final_publish_at
        job_record.vk_post_id = publication_result.post_id
        job_record.error_message = ""
        job_record.finished_at = timezone.now()
        job_record.save(
            update_fields=[
                "status",
                "operation_name",
                "result",
                "result_message",
                "warning_message",
                "stage",
                "photo_expected",
                "photo_uploaded",
                "audio_expected_count",
                "audio_uploaded_count",
                "audio_failed_count",
                "failed_track_titles",
                "audio_failure_details",
                "effective_publish_at",
                "vk_post_id",
                "error_message",
                "finished_at",
                "modified",
            ]
        )
        _log_vk_publication_event(
            logging.INFO,
            "record_finish",
            "----- Публикация записи в VK завершена -----",
            job_id=job.id,
            job_record_id=job_record.id,
            record_id=record.pk,
            vk_post_id=publication_result.post_id,
            shifted=shifted,
            effective_publish_at=final_publish_at.isoformat()
            if final_publish_at
            else None,
            result=result,
            warning_message=warning_message or "—",
        )
    except Exception as exc:  # noqa: BLE001
        job_record.status = VKPublicationJobRecord.Status.FAILED
        job_record.operation_name = (
            "Отложенная публикация в VK"
            if job_record.mode == VKPublicationJobRecord.Mode.SCHEDULED
            else "Публикация в VK"
        )
        job_record.result = "Публикация завершилась с ошибкой"
        job_record.result_message = f"Публикация на стену VK не удалась. Причина: {exc}"
        job_record.error_message = str(exc)
        job_record.stage = "Завершение операции"
        job_record.finished_at = timezone.now()
        job_record.save(
            update_fields=[
                "status",
                "operation_name",
                "result",
                "result_message",
                "error_message",
                "stage",
                "finished_at",
                "modified",
            ]
        )
        _log_vk_publication_event(
            logging.ERROR,
            "record_failed",
            "Во время публикации записи в VK произошла ошибка.",
            job_id=job.id,
            job_record_id=job_record.id,
            record_id=record.pk,
            error=str(exc),
        )
        logger.exception(
            "Стектрейс ошибки публикации записи в VK.",
            extra=build_log_extra(
                component=_VK_PUBLICATION_COMPONENT,
                event="record_failed_traceback",
                job_id=job.id,
                job_record_id=job_record.id,
                record_id=record.pk,
            ),
        )

    refreshed = _refresh_vk_publication_job_status(job)
    refreshed_job_record = VKPublicationJobRecord.objects.get(pk=job_record.pk)
    return {
        "job_id": str(refreshed.id),
        "record_id": record.pk,
        "status": refreshed_job_record.status,
        "vk_post_id": refreshed_job_record.vk_post_id,
        "effective_publish_at": refreshed_job_record.effective_publish_at.isoformat()
        if refreshed_job_record.effective_publish_at
        else None,
    }


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
            "Трек пропущен: ссылка на YouTube/Bandcamp отсутствует.",
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
            "Трек пропущен: ссылка на YouTube/Bandcamp не прошла валидацию.",
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
            "Запущена загрузка аудио из YouTube/Bandcamp."
            if not previous_audio_present
            else "Запущена замена существующего mp3 данными из YouTube/Bandcamp."
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
            "Трек обновлён: mp3 сохранён из YouTube/Bandcamp.",
            attempts=payload["attempts"],
            **log_context,
        )
        _log_youtube_audio_event(
            logging.DEBUG,
            "track_updated_details",
            "Детали успешного обновления трека из YouTube/Bandcamp.",
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
        "Не удалось обновить трек аудио из YouTube/Bandcamp.",
        reason=payload["reason_code"],
        attempts=payload["attempts"],
        youtube_url=youtube_url,
        error=payload["error_message"],
        **log_context,
    )
    _log_youtube_audio_event(
        logging.DEBUG,
        "track_failed_details",
        "Детали ошибки обновления трека из YouTube/Bandcamp.",
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
    track_total = (
        int(aggregate["updated_sum"] or 0)
        + int(aggregate["skipped_sum"] or 0)
        + int(aggregate["error_sum"] or 0)
    )
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


def _parse_redeye_run_job_payload(
    payload: dict[str, Any],
) -> tuple[uuid.UUID, list[int], bool, int | None, str]:
    job_id = uuid.UUID(str(payload["job_id"]))
    record_ids = sorted({int(record_id) for record_id in payload.get("record_ids", [])})
    if not record_ids:
        raise ValueError("record_ids для Redeye enrichment не должен быть пустым.")

    return (
        job_id,
        record_ids,
        bool(payload.get("overwrite_existing", False)),
        (
            int(payload["requested_by_user_id"])
            if payload.get("requested_by_user_id") is not None
            else None
        ),
        str(
            payload.get(
                "source",
                AudioEnrichmentJob.Source.REDEYE_MANUAL_LIST,
            )
        ),
    )


def _parse_redeye_process_record_payload(
    payload: dict[str, Any],
) -> tuple[uuid.UUID, int, bool]:
    return (
        uuid.UUID(str(payload["job_id"])),
        int(payload["record_id"]),
        bool(payload.get("overwrite_existing", False)),
    )


@shared_task(name="records.redeye_audio_enrichment.run_job")
def run_redeye_audio_enrichment_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Ставит fan-out обработку Redeye job по списку записей."""
    audio_service = AudioService()
    (
        job_id,
        record_ids,
        overwrite_existing,
        requested_by_user_id,
        source,
    ) = _parse_redeye_run_job_payload(payload)

    job = AudioEnrichmentJob.objects.get(pk=job_id)
    job.source = source
    job.overwrite_existing = overwrite_existing
    job.requested_by_user_id = requested_by_user_id
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
        "redeye_job_start",
        "===== Запущена задача обновления аудио из Redeye =====",
        job_id=job.id,
        source=source,
        overwrite=overwrite_existing,
        records_total=len(record_ids),
        requested_by_user_id=requested_by_user_id,
    )

    queued_records = 0
    skipped_records = 0
    missing_records = 0
    for record_id in record_ids:
        record = Record.objects.filter(pk=record_id).first()
        if record is None:
            missing_records += 1
            _log_youtube_audio_event(
                logging.WARNING,
                "redeye_job_record_missing",
                "Запись для Redeye-обработки не найдена.",
                job_id=job.id,
                record_id=record_id,
                source=source,
                overwrite=overwrite_existing,
            )
            continue

        job_record, can_process = audio_service.acquire_audio_record_lock(
            job=job,
            record=record,
        )
        if not can_process:
            skipped_records += 1
            _log_youtube_audio_event(
                NOTICE_LEVEL,
                "redeye_record_skip",
                "Запись пропущена: уже обрабатывается другой audio-enrichment задачей.",
                job_id=job.id,
                record_id=record.pk,
                source=source,
                overwrite=overwrite_existing,
                reason=job_record.reason_code or "already_running",
            )
            continue

        queued_records += 1
        process_redeye_audio_enrichment_record.delay(
            {
                "job_id": str(job.id),
                "record_id": record.pk,
                "overwrite_existing": overwrite_existing,
            }
        )

    refreshed = _refresh_job_status(job)
    _log_youtube_audio_event(
        logging.INFO,
        "redeye_job_finish",
        (
            "===== Задача обновления аудио из Redeye поставлена на обработку: "
            f"queued={queued_records}, skipped={skipped_records}, missing={missing_records} ====="
        ),
        job_id=refreshed.id,
        source=source,
        overwrite=overwrite_existing,
        status=refreshed.status,
    )
    return {
        "job_id": str(refreshed.id),
        "status": refreshed.status,
        "queued_records": queued_records,
        "skipped_records": skipped_records,
        "missing_records": missing_records,
    }


@shared_task(name="records.redeye_audio_enrichment.process_record")
def process_redeye_audio_enrichment_record(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Обрабатывает одну запись и обновляет job report для Redeye."""
    audio_service = AudioService()
    job_id, record_id, overwrite_existing = _parse_redeye_process_record_payload(
        payload
    )

    job = AudioEnrichmentJob.objects.get(pk=job_id)
    record = Record.objects.get(pk=record_id)
    job_record, can_process = audio_service.acquire_audio_record_lock(
        job=job,
        record=record,
    )
    if not can_process:
        _refresh_job_status(job)
        _log_youtube_audio_event(
            NOTICE_LEVEL,
            "redeye_record_skip",
            "Запись пропущена: уже выполняется другой job-record.",
            job_id=job.id,
            record_id=record.pk,
            source=job.source,
            overwrite=overwrite_existing,
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

    audio_service.mark_audio_record_running(job_record)
    _log_youtube_audio_event(
        logging.INFO,
        "redeye_record_start",
        "----- Запущена обработка записи для обновления аудио из Redeye -----",
        job_id=job.id,
        record_id=record.pk,
        source=job.source,
        overwrite=overwrite_existing,
        title=record.title,
        tracks_total=record.tracks.count(),
    )

    updated_count = skipped_count = error_count = 0
    tracks_total = record.tracks.count()
    try:
        updated_count = int(
            audio_service.attach_audio_from_redeye(
                record=record,
                force=overwrite_existing,
            )
        )
        skipped_count = max(tracks_total - updated_count, 0)
        result_message = (
            f"Добавление аудио из Redeye завершено: добавлено {updated_count} из "
            f"{tracks_total} треков."
        )
        warning_message = ""
        if skipped_count > 0:
            warning_message = (
                f"Не для всех треков удалось добавить аудио из Redeye: "
                f"пропущено {skipped_count}."
            )
        audio_service.mark_audio_record_finished(
            job_record=job_record,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
            result_message=result_message,
            warning_message=warning_message,
            audio_source_summary="Redeye",
        )
        refreshed_job_record = AudioEnrichmentJobRecord.objects.get(pk=job_record.pk)
        _log_youtube_audio_event(
            logging.INFO,
            "redeye_record_finish",
            (
                "----- Обработка записи для Redeye завершена: "
                f"updated={refreshed_job_record.updated_count}, "
                f"skipped={refreshed_job_record.skipped_count}, "
                f"failed={refreshed_job_record.error_count} -----"
            ),
            job_id=job.id,
            record_id=record.pk,
            source=job.source,
            overwrite=overwrite_existing,
            status=refreshed_job_record.status,
        )
    except Exception as exc:  # noqa: BLE001
        _log_youtube_audio_event(
            logging.ERROR,
            "redeye_record_failed",
            "Во время обработки записи произошла ошибка Redeye-блока.",
            job_id=job.id,
            record_id=record.pk,
            source=job.source,
            overwrite=overwrite_existing,
            error=str(exc),
        )
        logger.exception(
            "Стектрейс ошибки обработки записи Redeye-блоком.",
            extra=build_log_extra(
                component=_YOUTUBE_AUDIO_COMPONENT,
                event="redeye_record_failed_traceback",
                job_id=job.id,
                record_id=record.pk,
                source=job.source,
                overwrite=overwrite_existing,
            ),
        )
        error_count = max(tracks_total - updated_count, 1)
        audio_service.mark_audio_record_finished(
            job_record=job_record,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
            force_failed=True,
            reason_code=AudioEnrichmentJobRecord.Reason.VALIDATION_ERROR,
            result_message="Добавление аудио из Redeye завершилось с ошибкой.",
            error_message=str(exc),
            audio_source_summary="Redeye",
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
    track_results_summary: list[dict[str, str]] = []
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
                track_results_summary.append(
                    _build_release_report_track_item(
                        track=track,
                        status="Добавлено",
                        message="Аудио добавлено.",
                        source_label=(
                            "YouTube"
                            if YouTubeAudioEnrichmentProvider.is_youtube_url(
                                track.youtube_url
                            )
                            else "Bandcamp"
                        ),
                    )
                )
            elif track_payload["status"] == AudioEnrichmentTrackResult.Status.SKIPPED:
                skipped_count += 1
                track_results_summary.append(
                    _build_release_report_track_item(
                        track=track,
                        status="Пропущено",
                        message=str(track_payload["reason_code"] or "Пропущено."),
                    )
                )
            else:
                error_count += 1
                track_results_summary.append(
                    _build_release_report_track_item(
                        track=track,
                        status="Ошибка",
                        message=str(
                            track_payload["error_message"] or "Ошибка обработки."
                        ),
                    )
                )

        result_message = (
            "Добавление аудио по URL завершено: "
            f"добавлено {updated_count}, пропущено {skipped_count}, ошибок {error_count}."
        )
        audio_sources = sorted(
            {
                track.get_audio_source_display()
                for track in tracks
                if track.audio_source != Track.AudioSource.UNKNOWN
            }
        )
        warning_message = ""
        if skipped_count > 0 or error_count > 0:
            warning_message = (
                "Не для всех треков удалось добавить аудио по URL "
                f"(пропущено {skipped_count}, ошибок {error_count})."
            )

        audio_service.mark_youtube_record_finished(
            job_record=job_record,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
            track_results_json=track_results_summary,
            result_message=result_message,
            warning_message=warning_message,
            audio_source_summary=", ".join(audio_sources)
            if audio_sources
            else "Не указан",
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
            track_results_json=track_results_summary,
            result_message="Добавление аудио по URL завершилось с ошибкой.",
            error_message=str(exc),
            audio_source_summary=job_record.audio_source_summary or "Не указан",
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


@shared_task(name="records.youtube_enrichment.process_track")
def process_youtube_enrichment_track(payload: dict[str, Any]) -> dict[str, Any]:
    """Обрабатывает один трек и обновляет job report."""
    audio_service = AudioService()
    parsed = audio_service.parse_process_track_payload(payload)

    job = AudioEnrichmentJob.objects.get(pk=parsed.job_id)
    track = Track.objects.select_related("record").get(pk=parsed.track_id)
    record = track.record

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
            "track_id": track.pk,
            "status": AudioEnrichmentJobRecord.Status.SKIPPED,
            "reason_code": job_record.reason_code
            or AudioEnrichmentJobRecord.Reason.ALREADY_RUNNING,
        }

    job_record.operation_name = "Добавление аудио к треку"
    job_record.scope = AudioEnrichmentJobRecord.Scope.TRACK
    job_record.stage = "Ожидает выполнения"
    job_record.tracks_total = 1
    if not job_record.queued_at:
        job_record.queued_at = timezone.now()
    job_record.save(
        update_fields=[
            "operation_name",
            "scope",
            "stage",
            "tracks_total",
            "queued_at",
            "modified",
        ]
    )
    audio_service.mark_youtube_record_running(job_record)
    _log_youtube_audio_event(
        logging.INFO,
        "record_start",
        "----- Запущена обработка записи для обновления одного трека из YouTube -----",
        job_id=job.id,
        record_id=record.pk,
        source=job.source,
        overwrite=parsed.overwrite_existing,
        title=record.title,
        tracks_total=1,
    )

    updated_count = skipped_count = error_count = 0
    track_results_summary: list[dict[str, str]] = []
    try:
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
            track_results_summary.append(
                _build_release_report_track_item(
                    track=track,
                    status="Добавлено",
                    message="Аудио добавлено.",
                    source_label=(
                        "YouTube"
                        if YouTubeAudioEnrichmentProvider.is_youtube_url(
                            track.youtube_url
                        )
                        else "Bandcamp"
                    ),
                )
            )
        elif track_payload["status"] == AudioEnrichmentTrackResult.Status.SKIPPED:
            skipped_count += 1
            track_results_summary.append(
                _build_release_report_track_item(
                    track=track,
                    status="Пропущено",
                    message=str(track_payload["reason_code"] or "Пропущено."),
                )
            )
        else:
            error_count += 1
            track_results_summary.append(
                _build_release_report_track_item(
                    track=track,
                    status="Ошибка",
                    message=str(track_payload["error_message"] or "Ошибка обработки."),
                )
            )

        audio_service.mark_youtube_record_finished(
            job_record=job_record,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
            track_results_json=track_results_summary,
            result_message=(
                "Добавление аудио к треку завершено: "
                f"добавлено {updated_count}, пропущено {skipped_count}, ошибок {error_count}."
            ),
            warning_message=(
                "Не удалось добавить аудио к треку."
                if skipped_count > 0 or error_count > 0
                else ""
            ),
            audio_source_summary=track.get_audio_source_display() or "Не указан",
        )
        refreshed_job_record = AudioEnrichmentJobRecord.objects.get(pk=job_record.pk)
        _log_youtube_audio_event(
            logging.INFO,
            "record_finish",
            (
                "----- Обработка одного трека из YouTube завершена: "
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
            "Во время обработки трека произошла ошибка YouTube-блока.",
            job_id=job.id,
            record_id=record.pk,
            source=job.source,
            overwrite=parsed.overwrite_existing,
            error=str(exc),
        )
        logger.exception(
            "Стектрейс ошибки обработки трека YouTube-блоком.",
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
            track_results_json=track_results_summary,
            result_message="Добавление аудио к треку завершилось с ошибкой.",
            error_message=str(exc),
            audio_source_summary=job_record.audio_source_summary or "Не указан",
        )

    refreshed = _refresh_job_status(job)
    refreshed_job_record = AudioEnrichmentJobRecord.objects.get(pk=job_record.pk)
    return {
        "job_id": str(refreshed.id),
        "record_id": record.pk,
        "track_id": track.pk,
        "status": refreshed_job_record.status,
        "updated_count": refreshed_job_record.updated_count,
        "skipped_count": refreshed_job_record.skipped_count,
        "error_count": refreshed_job_record.error_count,
    }


@shared_task(name="records.youtube_search.find_track_urls")
def find_youtube_audio_urls_for_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Ищет YouTube-ссылки для треков записи и заполняет пустые поля."""
    record_id = int(payload.get("record_id"))
    requested_by_user_id = payload.get("requested_by_user_id")
    job_id_raw = payload.get("job_id")

    record = Record.objects.prefetch_related("artists", "tracks").get(pk=record_id)
    artist_names = ", ".join(artist.name for artist in record.artists.all()) or "—"
    job = None
    job_record = None
    track_results_summary: list[dict[str, str]] = []

    if job_id_raw:
        job = AudioEnrichmentJob.objects.get(pk=uuid.UUID(str(job_id_raw)))
        if job.status != AudioEnrichmentJob.Status.RUNNING:
            job.status = AudioEnrichmentJob.Status.RUNNING
            if job.started_at is None:
                job.started_at = timezone.now()
            job.save(update_fields=["status", "started_at", "modified"])

        job_record = AudioEnrichmentJobRecord.objects.get(job=job, record=record)
        if job_record.status != AudioEnrichmentJobRecord.Status.RUNNING:
            job_record.status = AudioEnrichmentJobRecord.Status.RUNNING
            job_record.stage = "Поиск аудио для треков"
            if job_record.started_at is None:
                job_record.started_at = timezone.now()
            if job_record.queued_at is None:
                job_record.queued_at = timezone.now()
            job_record.save(
                update_fields=[
                    "status",
                    "stage",
                    "started_at",
                    "queued_at",
                    "modified",
                ]
            )

    _log_youtube_search_event(
        logging.INFO,
        "record_start",
        f"Запущен поиск аудио на YouTube для релиза «{record}».",
        record_id=record.pk,
        requested_by_user_id=requested_by_user_id,
    )

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    updated = skipped = not_found = 0

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for track in record.tracks.order_by("position_index", "id"):
            if str(track.youtube_url or "").strip():
                skipped += 1
                track_results_summary.append(
                    {
                        "track_id": str(track.pk),
                        "track_title": track.title,
                        "action": "Поиск аудио на YouTube",
                        "status": "Пропущено",
                        "source": "YouTube",
                        "message": "Ссылка уже заполнена.",
                    }
                )
                continue

            query = f"{artist_names} {record.title} {track.title}"
            try:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                entry = (info.get("entries") or [None])[0]
            except Exception as exc:  # noqa: BLE001
                _log_youtube_search_event(
                    logging.ERROR,
                    "track_search_failed",
                    f"Поиск YouTube для трека «{track.title}» завершился ошибкой.",
                    record_id=record.pk,
                    track_id=track.pk,
                )
                _log_youtube_search_event(
                    logging.DEBUG,
                    "track_search_failed",
                    "Детали ошибки поиска YouTube.",
                    record_id=record.pk,
                    track_id=track.pk,
                    query=query,
                    error=str(exc),
                )
                not_found += 1
                track_results_summary.append(
                    {
                        "track_id": str(track.pk),
                        "track_title": track.title,
                        "action": "Поиск аудио на YouTube",
                        "status": "Ошибка",
                        "source": "YouTube",
                        "message": str(exc),
                    }
                )
                continue

            if not entry:
                not_found += 1
                _log_youtube_search_event(
                    logging.INFO,
                    "track_search_empty",
                    (
                        f"Для трека «{track.title}» в релизе «{record}» "
                        "не найдено результатов YouTube."
                    ),
                    record_id=record.pk,
                    track_id=track.pk,
                )
                _log_youtube_search_event(
                    logging.DEBUG,
                    "track_search_empty",
                    "Детали пустого ответа поиска YouTube.",
                    record_id=record.pk,
                    track_id=track.pk,
                    query=query,
                )
                track_results_summary.append(
                    {
                        "track_id": str(track.pk),
                        "track_title": track.title,
                        "action": "Поиск аудио на YouTube",
                        "status": "Не найдено",
                        "source": "YouTube",
                        "message": "Поиск не вернул результатов.",
                    }
                )
                continue

            url = entry.get("url") or entry.get("webpage_url") or ""
            if url and not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"

            if not url:
                not_found += 1
                _log_youtube_search_event(
                    logging.INFO,
                    "track_search_empty",
                    (
                        f"Для трека «{track.title}» в релизе «{record}» "
                        "не удалось извлечь ссылку YouTube."
                    ),
                    record_id=record.pk,
                    track_id=track.pk,
                )
                _log_youtube_search_event(
                    logging.DEBUG,
                    "track_search_empty",
                    "Детали пустого URL в результате поиска YouTube.",
                    record_id=record.pk,
                    track_id=track.pk,
                    query=query,
                    raw_url=str(entry.get("url") or ""),
                )
                track_results_summary.append(
                    {
                        "track_id": str(track.pk),
                        "track_title": track.title,
                        "action": "Поиск аудио на YouTube",
                        "status": "Не найдено",
                        "source": "YouTube",
                        "message": "Не удалось извлечь ссылку YouTube.",
                    }
                )
                continue

            track.youtube_url = url
            track.save(update_fields=["youtube_url", "modified"])
            updated += 1
            track_results_summary.append(
                {
                    "track_id": str(track.pk),
                    "track_title": track.title,
                    "action": "Поиск аудио на YouTube",
                    "status": "Найдено",
                    "source": "YouTube",
                    "message": "Ссылка YouTube найдена.",
                }
            )

            _log_youtube_search_event(
                logging.INFO,
                "track_search_found",
                (
                    f"Для трека «{track.title}» из релиза «{record}» "
                    "найдена ссылка YouTube."
                ),
                record_id=record.pk,
                track_id=track.pk,
            )
            _log_youtube_search_event(
                logging.DEBUG,
                "track_search_found",
                "Детали найденной YouTube-ссылки.",
                record_id=record.pk,
                track_id=track.pk,
                query=query,
                youtube_url=url,
                result_title=str(entry.get("title") or ""),
            )

    _log_youtube_search_event(
        logging.INFO,
        "record_finish",
        (
            "Поиск YouTube для релиза завершён: "
            f"updated={updated}, skipped={skipped}, not_found={not_found}."
        ),
        record_id=record.pk,
    )
    if job_record is not None and job is not None:
        total_skipped = skipped + not_found
        if updated > 0 and total_skipped == 0:
            status = AudioEnrichmentJobRecord.Status.COMPLETED
            result = "Ссылки YouTube найдены"
            warning_message = ""
        elif updated > 0:
            status = AudioEnrichmentJobRecord.Status.COMPLETED_WITH_ERRORS
            result = "Ссылки YouTube найдены частично"
            warning_message = (
                f"Не для всех треков найдены ссылки YouTube: "
                f"пропущено {skipped}, не найдено {not_found}."
            )
        else:
            status = AudioEnrichmentJobRecord.Status.COMPLETED_WITH_ERRORS
            result = "Ссылки YouTube не найдены"
            warning_message = (
                f"Поиск завершён без новых ссылок: пропущено {skipped}, "
                f"не найдено {not_found}."
            )

        job_record.status = status
        job_record.stage = "Завершение операции"
        job_record.result = result
        job_record.result_message = (
            f"Поиск аудио на YouTube завершён: найдено {updated}, "
            f"пропущено {skipped}, не найдено {not_found}."
        )
        job_record.warning_message = warning_message
        job_record.error_message = ""
        job_record.audio_source_summary = "YouTube"
        job_record.updated_count = updated
        job_record.skipped_count = total_skipped
        job_record.error_count = 0
        job_record.track_results_json = track_results_summary
        job_record.finished_at = timezone.now()
        job_record.save(
            update_fields=[
                "status",
                "stage",
                "result",
                "result_message",
                "warning_message",
                "error_message",
                "audio_source_summary",
                "updated_count",
                "skipped_count",
                "error_count",
                "track_results_json",
                "finished_at",
                "modified",
            ]
        )
        _refresh_job_status(job)

    return {
        "record_id": record.pk,
        "updated": updated,
        "skipped": skipped,
        "not_found": not_found,
    }


@shared_task(name="records.youtube_session.refresh")
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
    )
    return {
        "refreshed": result.refreshed,
        "profile_ready": result.profile_ready,
        "waited_for_existing_refresh": result.waited_for_existing_refresh,
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
