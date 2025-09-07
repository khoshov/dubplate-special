import logging

import requests

from django.core.files.base import ContentFile

from records.models import Record
from django.conf import settings

logger = logging.getLogger(__name__)


class ImageService:
    """Сервис для работы с изображениями обложек.

    Предоставляет функциональность для загрузки и управления
    изображениями обложек пластинок.

    Attributes:
        TIMEOUT: Таймаут для HTTP запросов (сек).
        USER_AGENT: User-Agent для HTTP запросов.
    """

    TIMEOUT = 20
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
            logger.info(f"Record {record.id} already has cover")
            return False

        try:
            response = requests.get(
                image_url, headers={"User-Agent": self.USER_AGENT}, timeout=self.TIMEOUT
            )
            response.raise_for_status()

            filename = f"cover_{record.discogs_id or record.id}.jpeg"
            record.cover_image.save(filename, ContentFile(response.content))
            logger.info(f"Cover downloaded for record {record.id}")
            return True

        except requests.RequestException as e:
            logger.error(f"Failed to download cover for record {record.id}: {e}")
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error downloading cover for record {record.id}: {e}"
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
            logger.info(f"Cover deleted for record {record.id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete cover for record {record.id}: {e}")
            return False
