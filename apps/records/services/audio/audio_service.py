from __future__ import annotations

import logging
from typing import Optional

from playwright.sync_api import Browser

from records.models import AudioEnrichmentJob, AudioEnrichmentJobRecord, Record, Track
from records.services.audio.common.downloader import (
    download_audio_to_track as _download_audio_to_track,
)
from records.services.audio.providers.youtube_audio_enrichment import (
    ProcessRecordPayload,
    RunJobPayload,
    YouTubeAudioEnrichmentProvider,
)
from records.services.audio.providers.redeye.redeye_audio_player import (
    attach_audio_from_redeye_player,
)
from records.constants import (
    AUDIO_DEFAULT_TIMEOUT,
    AUDIO_DEFAULT_MAX_BYTES,
)

logger = logging.getLogger(__name__)


class AudioService:
    """
    Сервис выполняет операции с аудио-файлами треков.

    Задачи:
      • Прикрепляет аудио к трекам записи из конкретных источников (Redeye и др.).
      • Делегирует провайдер-специфичную логику соответствующим модулям.
      • Выполняет потоковую загрузку аудио-файла для отдельного трека по-прямому URL.
    """

    @staticmethod
    def attach_audio_from_redeye(
        record: Record,
        *,
        force: bool = False,
        per_click_timeout_sec: Optional[int] = None,
        page_url: Optional[str] = None,
        browser: Optional[Browser] = None,
    ) -> int:
        """
        Метод прикрепляет аудио-превью к трекам записи из источника Redeye.

        Логика:
          1) Определяет эффективный таймаут клика по плееру:
             если `per_click_timeout_sec` не задан, берётся константа
             `REDEYE_PLAYER_DEFAULT_CLICK_TIMEOUT_SEC`.
          2) Вызывает модуль для получения URL и привязки аудио.

        Args:
            • record: Запись, треки которой нужно заполнить аудио.
            • force: Принудительно перезаписывать уже существующие файлы у треков.
            • per_click_timeout_sec: Таймаут ожидания появления URL после клика по кнопке плеера (сек).
            • page_url: Явный URL карточки Redeye (если не указан — будет определён ниже).
            • browser: Внешний экземпляр Playwright Browser для массовой обработки.
              Если передан — будет использован без запуска/остановки нового браузера.

        Returns:
            Количество треков, у которых аудио появилось или обновилось.
        """

        logger.info(
            "Старт прикрепления аудио из Redeye: record=%s, force=%s, click_timeout=%s",
            record.pk,
            force,
            per_click_timeout_sec,
        )

        updated = attach_audio_from_redeye_player(
            record=record,
            page_url=page_url,
            force=force,
            per_click_timeout_sec=per_click_timeout_sec,
            browser=browser,
        )

        logger.info(
            "Завершено прикрепление аудио из Redeye: record=%s, обновлено=%d",
            record.pk,
            updated,
        )
        return updated

    @staticmethod
    def download_audio_to_track(
        track: Track,
        url: str,
        *,
        overwrite: bool = False,
        referer: Optional[str] = None,
        timeout: int = AUDIO_DEFAULT_TIMEOUT,
        max_bytes: int = AUDIO_DEFAULT_MAX_BYTES,
    ) -> Optional[str]:
        """
        Метод выполняет потоковую загрузку аудио по прямому URL и сохраняет файл
        в поле `track.audio_preview`.

        Args:
            • track: Трек, к которому нужно прикрепить файл.
            • url: Прямой URL на аудио (mp3/aac/...).
            • overwrite: Перезаписывать ли существующий файл (если он уже прикреплён).
            • referer: Значение заголовка Referer (например, URL карточки товара).
            • timeout: Таймаут сетевого запроса в секундах. По умолчанию —
              `AUDIO_DEFAULT_TIMEOUT`.
            • max_bytes: Максимально допустимый размер скачиваемого файла в байтах.
              По умолчанию — `AUDIO_DEFAULT_MAX_BYTES`.

        Returns:
            Относительный путь к файлу (`FieldFile.name`) при успехе, либо `None` при неуспехе.
        """
        return _download_audio_to_track(
            track,
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            overwrite=overwrite,
            referer=referer,
        )

    @staticmethod
    def parse_run_job_payload(payload: dict[str, object]) -> RunJobPayload:
        """Метод валидирует payload запуска асинхронной job."""
        return RunJobPayload.from_dict(payload)

    @staticmethod
    def parse_process_record_payload(
        payload: dict[str, object],
    ) -> ProcessRecordPayload:
        """Метод валидирует payload обработки одной записи."""
        return ProcessRecordPayload.from_dict(payload)

    @staticmethod
    def acquire_youtube_record_lock(
        *,
        job: AudioEnrichmentJob,
        record: Record,
    ) -> tuple[AudioEnrichmentJobRecord, bool]:
        """Метод захватывает lock на обработку записи для YouTube enrichment."""
        return YouTubeAudioEnrichmentProvider.acquire_record_lock(
            job=job, record=record
        )

    @staticmethod
    def mark_youtube_record_running(job_record: AudioEnrichmentJobRecord) -> None:
        """Метод переводит job-record в состояние running."""
        YouTubeAudioEnrichmentProvider.mark_record_running(job_record)

    @staticmethod
    def mark_youtube_record_finished(
        *,
        job_record: AudioEnrichmentJobRecord,
        updated_count: int,
        skipped_count: int,
        error_count: int,
        force_failed: bool = False,
        reason_code: str = AudioEnrichmentJobRecord.Reason.NONE,
    ) -> AudioEnrichmentJobRecord:
        """Метод фиксирует итог обработки job-record."""
        return YouTubeAudioEnrichmentProvider.mark_record_finished(
            job_record=job_record,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
            force_failed=force_failed,
            reason_code=reason_code,
        )
