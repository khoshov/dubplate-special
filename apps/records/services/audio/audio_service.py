from __future__ import annotations

import logging
from typing import Optional

from playwright.sync_api import Browser

from config.logging import log_event
from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    Record,
    Track,
)
from records.services.audio.common.downloader import (
    download_audio_to_track as _download_audio_to_track,
)
from records.services.audio.providers.youtube_audio_enrichment import (
    ProcessRecordPayload,
    RunJobPayload,
    YouTubeAudioEnrichmentProvider,
)
from records.services.audio.providers.youtube_session import (
    YouTubeSessionLoginResult,
    YouTubeSessionRefreshResult,
    YouTubeSessionService,
)
from records.services.audio.providers.redeye.redeye_audio_player import (
    attach_audio_from_redeye_player,
)
from records.constants import (
    AUDIO_DEFAULT_TIMEOUT,
    AUDIO_DEFAULT_MAX_BYTES,
)

logger = logging.getLogger(__name__)
_AUDIO_SERVICE_COMPONENT = "audio_service"


def _log_audio_service_event(
    level: int,
    event: str,
    message: str,
    **context: object,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_AUDIO_SERVICE_COMPONENT,
        event=event,
        **context,
    )


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

        _log_audio_service_event(
            logging.INFO,
            "redeye_attach_start",
            "Старт прикрепления аудио из Redeye.",
            record_id=getattr(record, "pk", None),
            overwrite=force,
            click_timeout_sec=per_click_timeout_sec,
        )

        updated = attach_audio_from_redeye_player(
            record=record,
            page_url=page_url,
            force=force,
            per_click_timeout_sec=per_click_timeout_sec,
            browser=browser,
        )

        _log_audio_service_event(
            logging.INFO,
            "redeye_attach_finish",
            "Завершено прикрепление аудио из Redeye.",
            record_id=getattr(record, "pk", None),
            updated_count=updated,
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
    def download_audio_from_youtube(
        track: Track,
        *,
        overwrite: bool = False,
    ) -> Optional[str]:
        """Скачивает mp3 из `track.youtube_url` через YouTube provider."""
        return YouTubeAudioEnrichmentProvider.download_audio_to_track(
            track=track,
            overwrite=overwrite,
        )

    @staticmethod
    def bootstrap_youtube_session() -> YouTubeSessionRefreshResult:
        """Создаёт persistent browser profile из текущего cookies.txt."""
        return YouTubeSessionService.bootstrap_from_cookie_file()

    @staticmethod
    def refresh_youtube_session() -> YouTubeSessionRefreshResult:
        """Обновляет persistent browser profile для YouTube."""
        return YouTubeSessionService.refresh_profile()

    @staticmethod
    def login_youtube_session(
        *, timeout_ms: int | None = None
    ) -> YouTubeSessionLoginResult:
        """Открывает интерактивный логин в persistent browser profile YouTube."""
        return YouTubeSessionService.interactive_login(timeout_ms=timeout_ms)

    @staticmethod
    def parse_run_job_payload(payload: dict[str, object]) -> RunJobPayload:
        """Валидирует payload запуска async job."""
        return RunJobPayload.from_dict(payload)

    @staticmethod
    def parse_process_record_payload(
        payload: dict[str, object],
    ) -> ProcessRecordPayload:
        """Валидирует payload обработки одной записи."""
        return ProcessRecordPayload.from_dict(payload)

    @staticmethod
    def acquire_youtube_record_lock(
        *,
        job: AudioEnrichmentJob,
        record: Record,
    ) -> tuple[AudioEnrichmentJobRecord, bool]:
        """Захватывает обработку записи для YouTube enrichment."""
        return YouTubeAudioEnrichmentProvider.acquire_record_lock(
            job=job,
            record=record,
        )

    @staticmethod
    def mark_youtube_record_running(job_record: AudioEnrichmentJobRecord) -> None:
        """Переводит job-record в состояние running."""
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
        """Фиксирует итог обработки записи в рамках YouTube job."""
        return YouTubeAudioEnrichmentProvider.mark_record_finished(
            job_record=job_record,
            updated_count=updated_count,
            skipped_count=skipped_count,
            error_count=error_count,
            force_failed=force_failed,
            reason_code=reason_code,
        )
