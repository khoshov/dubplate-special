from __future__ import annotations

import logging
from typing import Optional

from records.models import Record, Track
from records.services.audio.common.downloader import download_audio_to_track
from records.services.audio.providers.redeye.redeye_audio_player import (
    attach_audio_from_redeye_player,
)

logger = logging.getLogger(__name__)


class AudioService:
    """
    Сервис предоставляет операции по работе с аудио треков.

    Задачи:
      - Выполняет загрузку и прикрепление аудио к трекам записи с учётом провайдера.
      - Делегирует провайдер-специфичную логику в соответствующие модули.
      - Предоставляет низкоуровневую загрузку файла в конкретный Track по URL.
    """

    @staticmethod
    def attach_audio_from_redeye(
        record: Record,
        *,
        force: bool = False,
        per_click_timeout_sec: int = 20,
        page_url: Optional[str] = None,
    ) -> int:
        """
        Метод выполняет прикрепление аудио к трекам записи из источника Redeye.

        Args:
            record (Record): Запись, треки которой нужно заполнить аудио.
            force (bool): Признак перезаписи уже существующих файлов у треков.
            per_click_timeout_sec (int): Таймаут между кликами плеера при сборе URL.
            page_url (Optional[str]): Явный URL карточки Redeye (если не указан — берётся из RecordSource).

        Returns:
            int: Количество треков, у которых аудио появилось или обновилось.
        """
        logger.info(
            "[audio] старт прикрепления аудио из Redeye для записи %s.", record.pk
        )
        updated = attach_audio_from_redeye_player(
            record,
            page_url=page_url,
            force=force,
            per_click_timeout_sec=per_click_timeout_sec,
        )
        logger.info(
            "[audio] завершено прикрепление аудио из Redeye: обновлено %d.", updated
        )
        return updated

    @staticmethod
    def download_audio_to_track(
        track: Track,
        url: str,
        *,
        overwrite: bool = False,
        referer: Optional[str] = None,
        timeout: int = 30,
        max_bytes: int = 15 * 1024 * 1024,
    ) -> Optional[str]:
        """
        Метод выполняет потоковую загрузку аудио по прямому URL и сохраняет файл в `track.audio_preview`.

        Args:
            track (Track): Трек, к которому нужно прикрепить файл.
            url (str): Прямой URL на аудио (mp3/aac/...).
            overwrite (bool): Перезаписывать ли существующий файл.
            referer (Optional[str]): Заголовок Referer (например, URL карточки).
            timeout (int): Таймаут сетевого запроса в секундах.
            max_bytes (int): Лимит размера файла в байтах.

        Returns:
            Optional[str]: Относительный путь к файлу (`FieldFile.name`) или `None` при неуспехе.
        """
        return download_audio_to_track(
            track,
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            overwrite=overwrite,
            referer=referer,
        )
