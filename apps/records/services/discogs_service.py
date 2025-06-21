import logging
import time
from typing import Optional

import discogs_client

from django.conf import settings

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
            После превышения лимита запросов делает паузу 60 секунд.
        """
        try:
            return func(*args, **kwargs)
        except discogs_client.exceptions.HTTPError as e:
            if e.status_code == 401:
                logger.error("Discogs authentication error")
                raise Exception("Discogs authentication error. Check your token.")
            elif e.status_code == 429:
                logger.warning("Rate limit exceeded, waiting before retry...")
                time.sleep(60)
                return self._make_request(func, *args, **kwargs)
            logger.error(f"Discogs API error: {str(e)}")
            raise

    def search_release_by_barcode(
        self, barcode: str
    ) -> Optional[discogs_client.Release]:
        """Ищет релиз по штрих-коду.

        Args:
            barcode: Штрих-код для поиска.

        Returns:
            Optional[discogs_client.Release]: Объект релиза или None, если не найден.

        Note:
            Возвращает первый найденный релиз и обновляет его данные через refresh().
        """
        results = self._make_request(self.client.search, barcode, type="release")
        if results:
            release = results[0]
            self._make_request(release.refresh)  # Получение полных данных
            return release
        return None
