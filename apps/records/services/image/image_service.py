import logging

import requests

from django.core.files.base import ContentFile
from config.logging import log_event
from records.constants import IMAGE_DOWNLOAD_TIMEOUT_SEC

from records.models import Record
from django.conf import settings

logger = logging.getLogger(__name__)
_IMAGE_SERVICE_COMPONENT = "image_service"


def _log_image_service_event(
    level: int,
    event: str,
    message: str,
    **context,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_IMAGE_SERVICE_COMPONENT,
        event=event,
        **context,
    )


class ImageService:
    """Сервис для работы с изображениями обложек.

    Предоставляет функциональность для загрузки и управления
    изображениями обложек пластинок.

    Attributes:
        TIMEOUT: Таймаут для HTTP запросов (сек).
        USER_AGENT: User-Agent для HTTP запросов.
    """

    TIMEOUT = IMAGE_DOWNLOAD_TIMEOUT_SEC
    USER_AGENT = settings.DISCOGS_USER_AGENT

    def download_cover(self, record: Record, image_url: str) -> bool:
        """Загрузка и сохранение обложки для записи.

        Загружает изображение по указанному URL и сохраняет его
        в поле cover_image модели Record.

        Args:
            record: Запись для сохранения обложки.
            image_url: URL изображения для загрузки.

        Returns:
            True если обложка успешно загружена и сохранена,
            False в противном случае.
        """
        if record.cover_image:
            _log_image_service_event(
                logging.INFO,
                "cover_skip",
                "Загрузка обложки пропущена: файл уже существует.",
                record_id=record.id,
            )
            return False

        try:
            response = requests.get(
                image_url, headers={"User-Agent": self.USER_AGENT}, timeout=self.TIMEOUT
            )
            response.raise_for_status()

            filename = f"cover_{record.discogs_id or record.id}.jpeg"
            record.cover_image.save(filename, ContentFile(response.content))
            _log_image_service_event(
                logging.INFO,
                "cover_downloaded",
                "Обложка сохранена для записи.",
                record_id=record.id,
                image_url=image_url,
                file_name=filename,
            )
            return True

        except requests.RequestException as exc:
            _log_image_service_event(
                logging.ERROR,
                "cover_download_failed",
                "Не удалось скачать обложку записи.",
                record_id=record.id,
                image_url=image_url,
                error=str(exc),
            )
            return False
        except Exception as exc:  # noqa: BLE001
            _log_image_service_event(
                logging.ERROR,
                "cover_download_failed",
                "Произошла непредвиденная ошибка при загрузке обложки записи.",
                record_id=record.id,
                image_url=image_url,
                error=str(exc),
            )
            return False

    def delete_cover(self, record: Record) -> bool:
        """Удаление обложки записи.

        Удаляет файл обложки и очищает поле cover_image.

        Args:
            record: Запись для удаления обложки.

        Returns:
            True если обложка успешно удалена,
            False если обложки не было или произошла ошибка.
        """
        if not record.cover_image:
            return False

        try:
            record.cover_image.delete(save=True)
            _log_image_service_event(
                logging.INFO,
                "cover_deleted",
                "Обложка удалена у записи.",
                record_id=record.id,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            _log_image_service_event(
                logging.ERROR,
                "cover_delete_failed",
                "Не удалось удалить обложку записи.",
                record_id=record.id,
                error=str(exc),
            )
            return False
