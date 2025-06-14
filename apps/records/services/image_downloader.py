import logging

import requests

from django.conf import settings
from django.core.files.base import ContentFile

from records.models import Record

logger = logging.getLogger(__name__)


class DiscogsImageDownloader:
    """Загрузчик обложек релизов с Discogs."""

    @staticmethod
    def download_cover(release, record: Record) -> bool:
        """Загружает и сохраняет обложку для указанного релиза."""
        if record.cover_image:
            return False

        if not hasattr(release, "images") or not release.images:
            return False

        try:
            image_url = release.images[0]["uri"]
            response = requests.get(
                image_url,
                headers={"User-Agent": settings.DISCOGS_USER_AGENT},
                timeout=20,
            )
            response.raise_for_status()

            filename = f"cover_{record.discogs_id}.jpeg"
            record.cover_image.save(filename, ContentFile(response.content))
            return True
        except Exception as e:
            logger.error(f"Error downloading cover: {str(e)}")
            return False
