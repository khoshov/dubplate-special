import logging
import time
from typing import Optional

import discogs_client

from django.conf import settings

from records.services.constants import DiscogsConstants
from records.services.factories import DiscogsModelFactory
from records.services.image_downloader import DiscogsImageDownloader
from records.services.importers import DiscogsReleaseImporter

logger = logging.getLogger(__name__)


class DiscogsService:
    """Фасад для работы с Discogs API и связанными сервисами.

    Attributes:
        api_client (DiscogsAPIClient): Клиент для запросов к API.
        model_factory (DiscogsModelFactory): Фабрика моделей Django.
        image_downloader (DiscogsImageDownloader): Загрузчик обложек.
        importer (DiscogsReleaseImporter): Импортер данных релизов.

    Example:
        >>> service = DiscogsService()
        >>> record = service.importer.import_release(barcode="123456789")
    """

    def __init__(self):
        self.api_client = DiscogsAPIClient(
            user_agent=settings.DISCOGS_USER_AGENT, user_token=settings.DISCOGS_TOKEN
        )
        self.model_factory = DiscogsModelFactory()
        self.image_downloader = DiscogsImageDownloader()
        self.importer = DiscogsReleaseImporter(
            api_client=self.api_client,
            model_factory=self.model_factory,
            image_downloader=self.image_downloader,
        )


class DiscogsAPIClient:
    """Клиент для работы с Discogs API.

    Args:
        user_agent (str): Идентификатор приложения.
        user_token (str): Токен аутентификации.

    Attributes:
        client: Экземпляр клиента discogs_client.
    """

    def __init__(self, user_agent: str, user_token: str):
        self.client = discogs_client.Client(
            user_agent=user_agent,
            user_token=user_token,
        )

    def _make_request(self, func, *args, **kwargs):
        """Выполняет запрос с обработкой ошибок и rate limiting.

        Args:
            func: Функция для выполнения.
            *args: Позиционные аргументы для функции.
            **kwargs: Именованные аргументы для функции.

        Returns:
            Результат выполнения функции.

        Raises:
            Exception: При ошибках аутентификации или API.
            После превышения лимита запросов делает паузу согласно константе.
        """
        try:
            return func(*args, **kwargs)
        except discogs_client.exceptions.HTTPError as e:
            if e.status_code == DiscogsConstants.HTTP_UNAUTHORIZED:
                logger.error("Discogs authentication error")
                raise Exception("Discogs authentication error. Check your token.")
            elif e.status_code == DiscogsConstants.HTTP_RATE_LIMIT_EXCEEDED:
                logger.warning("Rate limit exceeded, waiting before retry...")
                time.sleep(DiscogsConstants.RATE_LIMIT_WAIT_TIME)
                return self._make_request(func, *args, **kwargs)
            logger.error(f"Discogs API error: {str(e)}")
            raise

    def search_release(
        self, query: str, search_type: str
    ) -> Optional[discogs_client.Release]:
        """Универсальный поиск релиза.

        Args:
            query: Строка для поиска.
            search_type: Тип поиска (barcode, catno и т.д.).

        Returns:
            Optional[discogs_client.Release]: Объект релиза или None, если не найден.

        Note:
            Возвращает первый найденный релиз и обновляет его данные через refresh().
        """
        results = self._make_request(self.client.search, query, type=search_type)
        if results:
            release = results[0]
            self._make_request(release.refresh)  # Получение полных данных
            return release
        return None

    def search_release_by_barcode(
        self, barcode: str
    ) -> Optional[discogs_client.Release]:
        """Ищет релиз по штрих-коду.

        Args:
            barcode: Штрих-код для поиска.

        Returns:
            Optional[discogs_client.Release]: Объект релиза или None, если не найден.
        """
        return self.search_release(barcode, DiscogsConstants.SEARCH_TYPE_BARCODE)

    def search_release_by_catalog_number(
        self, catalog_number: str
    ) -> Optional[discogs_client.Release]:
        """Ищет релиз по каталожному номеру.

        Args:
            catalog_number: Каталожный номер для поиска.

        Returns:
            Optional[discogs_client.Release]: Объект релиза или None, если не найден.
        """
        return self.search_release(catalog_number, DiscogsConstants.SEARCH_TYPE_CATALOG)

    def get_release_videos(self, release_id: int) -> Optional[list]:
        """Получает список видео для релиза.

        Args:
            release_id: ID релиза в Discogs.

        Returns:
            Optional[list]: Список словарей с видео или None при ошибке.
        """
        try:
            release = self._make_request(self.client.release, release_id)
            if hasattr(release, "videos") and release.videos:
                return [
                    {
                        "title": video.title,
                        "url": video.url,
                    }
                    for video in release.videos
                ]
            return None
        except Exception as e:
            logger.error(f"Error getting videos for release {release_id}: {str(e)}")
            return None
