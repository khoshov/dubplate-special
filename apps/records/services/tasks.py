from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.db.models import Count, Sum
from django.utils import timezone

from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Record,
    Track,
)
from records.services.audio.audio_service import AudioService
from records.services.audio.providers.youtube_audio_enrichment import (
    YouTubeAudioEnrichmentProvider,
)

logger = logging.getLogger(__name__)


def _process_track_for_discogs_enrichment(
    *,
    track: Track,
    overwrite_existing: bool,
) -> dict[str, Any]:
    """
    Обрабатывает один трек в Discogs-flow enrichment.

    На текущем этапе candidate metadata берётся из данных самого трека/записи.
    """
    provider = YouTubeAudioEnrichmentProvider
    previous_audio_present = bool(track.audio_preview)
    track_artists = list(track.record.artists.values_list("name", flat=True))

    if not track.youtube_url:
        payload = provider.serialize_track_result(
            status=AudioEnrichmentTrackResult.Status.SKIPPED,
            reason_code=AudioEnrichmentTrackResult.Reason.MISSING_YOUTUBE_URL,
            attempts=1,
            matched_title=False,
            matched_artist=False,
            previous_audio_present=previous_audio_present,
        )
        provider.log_track_outcome(
            record_id=track.record_id,
            track_id=track.id,
            status=payload["status"],
            reason_code=payload["reason_code"],
            attempts=payload["attempts"],
            matched_title=payload["matched_title"],
            matched_artist=payload["matched_artist"],
            previous_audio_present=payload["previous_audio_present"],
        )
        return payload

    if not provider.is_valid_youtube_url(track.youtube_url):
        payload = provider.serialize_track_result(
            status=AudioEnrichmentTrackResult.Status.SKIPPED,
            reason_code=AudioEnrichmentTrackResult.Reason.INVALID_URL,
            attempts=1,
            matched_title=False,
            matched_artist=False,
            previous_audio_present=previous_audio_present,
        )
        provider.log_track_outcome(
            record_id=track.record_id,
            track_id=track.id,
            status=payload["status"],
            reason_code=payload["reason_code"],
            attempts=payload["attempts"],
            matched_title=payload["matched_title"],
            matched_artist=payload["matched_artist"],
            previous_audio_present=payload["previous_audio_present"],
        )
        return payload

    matched, matched_title, matched_artist = provider.strict_match(
        track_title=track.title,
        candidate_title=track.title,
        track_artists=track_artists,
        candidate_artists=track_artists,
    )
    if not matched:
        payload = provider.serialize_track_result(
            status=AudioEnrichmentTrackResult.Status.SKIPPED,
            reason_code=AudioEnrichmentTrackResult.Reason.MISMATCH,
            attempts=1,
            matched_title=matched_title,
            matched_artist=matched_artist,
            previous_audio_present=previous_audio_present,
        )
        provider.log_track_outcome(
            record_id=track.record_id,
            track_id=track.id,
            status=payload["status"],
            reason_code=payload["reason_code"],
            attempts=payload["attempts"],
            matched_title=payload["matched_title"],
            matched_artist=payload["matched_artist"],
            previous_audio_present=payload["previous_audio_present"],
        )
        return payload

    audio_service = AudioService()

    def _download() -> str | None:
        return audio_service.download_audio_to_track(
            track=track,
            url=track.youtube_url or "",
            overwrite=overwrite_existing,
        )

    final_audio_name, attempts, last_error = provider.download_with_retry(
        operation=_download,
        max_attempts=3,
        base_delay_sec=1.0,
        sleep_func=lambda _delay: None,
    )
    if final_audio_name:
        payload = provider.serialize_track_result(
            status=AudioEnrichmentTrackResult.Status.UPDATED,
            reason_code=AudioEnrichmentTrackResult.Reason.NONE,
            attempts=attempts,
            matched_title=matched_title,
            matched_artist=matched_artist,
            previous_audio_present=previous_audio_present,
            final_audio_name=final_audio_name,
        )
        provider.log_track_outcome(
            record_id=track.record_id,
            track_id=track.id,
            status=payload["status"],
            reason_code=payload["reason_code"],
            attempts=payload["attempts"],
            matched_title=payload["matched_title"],
            matched_artist=payload["matched_artist"],
            previous_audio_present=payload["previous_audio_present"],
        )
        return payload

    payload = provider.serialize_track_result(
        status=AudioEnrichmentTrackResult.Status.FAILED,
        reason_code=AudioEnrichmentTrackResult.Reason.RETRY_EXHAUSTED,
        attempts=attempts,
        matched_title=matched_title,
        matched_artist=matched_artist,
        previous_audio_present=previous_audio_present,
        error_message=str(last_error or "Download failed"),
    )
    provider.log_track_outcome(
        record_id=track.record_id,
        track_id=track.id,
        status=payload["status"],
        reason_code=payload["reason_code"],
        attempts=payload["attempts"],
        matched_title=payload["matched_title"],
        matched_artist=payload["matched_artist"],
        previous_audio_present=payload["previous_audio_present"],
    )
    return payload


def _refresh_job_status(job: AudioEnrichmentJob) -> AudioEnrichmentJob:
    """Пересчитывает агрегаты и итоговый статус job по текущим job-record."""
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
    """Запускает обработку job и fan-out по записям."""
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

    queued_records = 0
    skipped_records = 0
    missing_records = 0
    for record_id in parsed.record_ids:
        record = Record.objects.filter(pk=record_id).first()
        if record is None:
            missing_records += 1
            logger.warning("YouTube enrichment: запись id=%s не найдена.", record_id)
            continue

        job_record, can_process = audio_service.acquire_youtube_record_lock(
            job=job,
            record=record,
        )
        if not can_process:
            skipped_records += 1
            logger.info(
                "YouTube enrichment: record_id=%s пропущена (%s).",
                record.pk,
                job_record.reason_code or "already_running",
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
    return {
        "job_id": str(refreshed.id),
        "status": refreshed.status,
        "queued_records": queued_records,
        "skipped_records": skipped_records,
        "missing_records": missing_records,
    }


@shared_task(name="records.youtube_enrichment.process_record")
def process_youtube_enrichment_record(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Обрабатывает одну запись в рамках job.

    В foundation-версии помечает запись как completed без enrichment-логики треков.
    """
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
        return {
            "job_id": str(job.id),
            "record_id": record.pk,
            "status": AudioEnrichmentJobRecord.Status.SKIPPED,
            "reason_code": job_record.reason_code
            or AudioEnrichmentJobRecord.Reason.ALREADY_RUNNING,
        }

    audio_service.mark_youtube_record_running(job_record)

    updated_count = skipped_count = error_count = 0
    try:
        tracks = list(record.tracks.order_by("position_index", "id"))
        for track in tracks:
            track_payload = _process_track_for_discogs_enrichment(
                track=track,
                overwrite_existing=parsed.overwrite_existing,
            )
            YouTubeAudioEnrichmentProvider.upsert_track_result(
                job_record=job_record,
                track=track,
                payload=track_payload,
            )
            status = track_payload["status"]
            if status == AudioEnrichmentTrackResult.Status.UPDATED:
                updated_count += 1
            elif status == AudioEnrichmentTrackResult.Status.SKIPPED:
                skipped_count += 1
            else:
                error_count += 1

        audio_service.mark_youtube_record_finished(
            job_record=job_record,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "YouTube enrichment: ошибка обработки record_id=%s в job=%s: %s",
            record.pk,
            job.id,
            exc,
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
