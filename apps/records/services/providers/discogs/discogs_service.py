import logging
import time
from typing import Any, Dict, List, Optional

import discogs_client
from rest_framework import status

from django.conf import settings

logger = logging.getLogger(__name__)


class DiscogsServiceError(Exception):
    """Базовая ошибка интеграции с Discogs."""


class DiscogsConfigError(DiscogsServiceError):
    """Ошибка конфигурации Discogs (токен/user-agent)."""


class DiscogsAuthError(DiscogsServiceError):
    """Ошибка авторизации в Discogs API."""


class DiscogsNotFoundError(DiscogsServiceError):
    """Релиз не найден в Discogs."""


class DiscogsApiError(DiscogsServiceError):
    """Прочая ошибка обращения к Discogs API."""


class DiscogsService:
    """Сервис для работы с Discogs API.

    Инкапсулирует всю логику работы с Discogs API, включая
    поиск релизов, обработку ошибок и rate limiting.

    Attributes:
        RATE_LIMIT_WAIT_TIME: Время ожидания при превышении лимита запросов (сек).
        client: Клиент для работы с Discogs API.
    """

    RATE_LIMIT_WAIT_TIME = 60

    def __init__(self):
        """Инициализация сервиса с настройками из Django settings."""
        self.client: discogs_client.Client | None = None
        self._init_error: DiscogsConfigError | None = None

        token = str(getattr(settings, "DISCOGS_TOKEN", "") or "").strip()
        user_agent = str(getattr(settings, "DISCOGS_USER_AGENT", "") or "").strip()

        if not token:
            self._init_error = DiscogsConfigError(
                "Импорт из Discogs недоступен: не задан API-ключ (DISCOGS_API_KEY)."
            )
            return

        if not user_agent:
            self._init_error = DiscogsConfigError(
                "Импорт из Discogs недоступен: не задан DISCOGS_USER_AGENT."
            )
            return

        self.client = discogs_client.Client(user_agent, user_token=token)

    def _ensure_client_ready(self) -> discogs_client.Client:
        """Возвращает инициализированный клиент Discogs или поднимает ошибку конфигурации."""
        if self._init_error is not None:
            raise self._init_error
        if self.client is None:
            raise DiscogsConfigError(
                "Импорт из Discogs недоступен: клиент Discogs не инициализирован."
            )
        return self.client

    def search_by_barcode(self, barcode: str) -> Optional[discogs_client.Release]:
        """Поиск релиза по штрих-коду.

        Args:
            barcode: Штрих-код для поиска.

        Returns:
            Объект релиза или None, если не найден.
        """
        client = self._ensure_client_ready()
        results = self._make_request(client.search, barcode=barcode, type="release")
        if not results:
            raise DiscogsNotFoundError(
                f"Релиз не найден в Discogs по штрих-коду {barcode}."
            )

        search_result = results[0]
        # refresh делаем best-effort, чтобы не ломать поток на неполных данных search-result
        try:
            self._make_request(search_result.refresh)
        except DiscogsServiceError as e:
            logger.warning("Discogs: не удалось refresh search result: %s", e)

        return search_result

    def search_by_catalog_number(
        self, catalog_number: str
    ) -> Optional[discogs_client.Release]:
        """Поиск релиза по каталожному номеру.

        Args:
            catalog_number: Каталожный номер для поиска.

        Returns:
            Объект релиза или None, если не найден.
        """
        client = self._ensure_client_ready()
        results = self._make_request(
            client.search, catno=catalog_number, type="release"
        )
        if not results:
            raise DiscogsNotFoundError(
                f"Релиз не найден в Discogs по каталожному номеру {catalog_number}."
            )

        search_result = results[0]
        # refresh делаем best-effort, чтобы не ломать поток на неполных данных search-result
        try:
            self._make_request(search_result.refresh)
        except DiscogsServiceError as e:
            logger.warning("Discogs: не удалось refresh search result: %s", e)

        return search_result

    def get_release(self, discogs_id: int) -> Optional[discogs_client.Release]:
        """Получение релиза по Discogs ID.

        Args:
            discogs_id: Идентификатор релиза в Discogs.

        Returns:
            Объект релиза или None, если не найден.
        """
        client = self._ensure_client_ready()
        return self._make_request(client.release, discogs_id)

    def get_release_videos(self, discogs_id: int) -> List[Dict[str, str]]:
        """Получение списка видео для релиза.

        Args:
            discogs_id: Идентификатор релиза в Discogs.

        Returns:
            Список словарей с информацией о видео.
            Каждый словарь содержит ключи 'title' и 'url'.
        """
        try:
            client = self._ensure_client_ready()
            release = self._make_request(client.release, discogs_id)
            if hasattr(release, "videos") and release.videos:
                return [
                    {"title": video.title, "url": video.url} for video in release.videos
                ]
        except DiscogsServiceError as e:
            logger.error(
                "Discogs: не удалось получить видео для релиза %s: %s", discogs_id, e
            )
        return []

    def extract_release_data(self, release: discogs_client.Release) -> Dict[str, Any]:
        """Извлечение данных из объекта релиза Discogs.

        Работает как с SearchResult (из search), так и с полным Release (из release).

        Args:
            release: Объект релиза из Discogs API.

        Returns:
            Словарь с извлечёнными данными релиза.
        """
        # Базовые данные
        data = {
            "discogs_id": release.id,
            "title": release.title,
            "year": getattr(release, "year", None),
            "country": getattr(release, "country", None),
            "notes": getattr(release, "notes", None),
            "catalog_number": None,
            "barcode": None,
        }

        # Извлечение catalog_number
        data["catalog_number"] = self._extract_catalog_number(release)

        # Извлечение barcode
        data["barcode"] = self._extract_barcode(release)

        logger.debug(
            f"Extracted from Discogs - "
            f"ID: {data['discogs_id']}, "
            f"Title: {data['title']}, "
            f"Barcode: {data.get('barcode', 'None')}, "
            f"Catalog: {data.get('catalog_number', 'None')}"
        )

        return data

    def _extract_catalog_number(self, release) -> Optional[str]:
        """Извлечение каталожного номера из релиза.

        Args:
            release: Объект релиза.

        Returns:
            Каталожный номер или None.
        """
        # Из labels (полный Release)
        try:
            if hasattr(release, "labels") and release.labels:
                return release.labels[0].catno
        except (IndexError, AttributeError):
            pass

        # Из data (SearchResult)
        if hasattr(release, "data") and isinstance(release.data, dict):
            if "catno" in release.data:
                return release.data["catno"]

        return None

    def _extract_barcode(self, release) -> Optional[str]:
        """Извлечение штрих-кода из релиза.

        Args:
            release: Объект релиза.

        Returns:
            Штрих-код или None.
        """
        # Из release.data['barcode'] (SearchResult)
        if hasattr(release, "data") and isinstance(release.data, dict):
            if "barcode" in release.data:
                return self._parse_barcode_data(release.data["barcode"])

            # Из release.data['identifiers'] (полный Release в data)
            if "identifiers" in release.data:
                barcode = self._extract_barcode_from_identifiers_list(
                    release.data["identifiers"]
                )
                if barcode:
                    return barcode

        # Из release.identifiers (полный Release объект)
        if hasattr(release, "identifiers"):
            try:
                for identifier in release.identifiers:
                    if hasattr(identifier, "type") and hasattr(identifier, "value"):
                        if (
                            identifier.type in ["Barcode", "barcode"]
                            and identifier.value
                        ):
                            return str(identifier.value).strip()
            except Exception as e:
                logger.debug(f"Failed to extract from identifiers: {e}")

        return None

    def _parse_barcode_data(self, barcode_data) -> Optional[str]:
        """Парсинг данных штрих-кода.

        Args:
            barcode_data: Данные штрих-кода (строка или список).

        Returns:
            Очищенный штрих-код или None.
        """
        if isinstance(barcode_data, list) and barcode_data:
            # Ищем первый элемент, содержащий только цифры
            for bc in barcode_data:
                bc_clean = str(bc).strip()
                if bc_clean and bc_clean.isdigit():
                    return bc_clean

            # Если не нашли чисто цифровой, берём первый
            if barcode_data[0]:
                return str(barcode_data[0]).strip()

        elif isinstance(barcode_data, str) and barcode_data:
            return barcode_data.strip()

        return None

    def _extract_barcode_from_identifiers_list(self, identifiers) -> Optional[str]:
        """Извлечение штрих-кода из списка идентификаторов.

        Args:
            identifiers: Список идентификаторов.

        Returns:
            Штрих-код или None.
        """
        if isinstance(identifiers, list):
            for identifier in identifiers:
                if isinstance(identifier, dict):
                    if identifier.get("type") in [
                        "Barcode",
                        "barcode",
                    ] and identifier.get("value"):
                        return str(identifier["value"]).strip()
        return None

    def _make_request(self, func, *args, **kwargs):
        """Выполнение запроса к API с обработкой ошибок.

        Обрабатывает ошибки аутентификации и превышения лимита запросов.
        При превышении лимита автоматически повторяет запрос после ожидания.

        Args:
            func: Функция для выполнения.
            *args: Позиционные аргументы для функции.
            **kwargs: Именованные аргументы для функции.

        Returns:
            Результат выполнения функции.

        Raises:
            DiscogsAuthError: При ошибке аутентификации.
            DiscogsNotFoundError: При отсутствии данных.
            DiscogsApiError: При прочих ошибках API.
        """
        try:
            return func(*args, **kwargs)
        except discogs_client.exceptions.HTTPError as e:
            if e.status_code == status.HTTP_401_UNAUTHORIZED:
                raise DiscogsAuthError(
                    "Ошибка авторизации Discogs. Проверьте API-ключ."
                ) from e
            elif e.status_code == status.HTTP_404_NOT_FOUND:
                raise DiscogsNotFoundError("Релиз не найден в Discogs.") from e
            elif e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                logger.warning("Discogs: превышен rate limit, повтор через ожидание...")
                time.sleep(self.RATE_LIMIT_WAIT_TIME)
                return self._make_request(func, *args, **kwargs)
            raise DiscogsApiError(
                f"Discogs API вернул ошибку HTTP {e.status_code}."
            ) from e
        except DiscogsServiceError:
            raise
        except Exception as e:
            raise DiscogsApiError("Ошибка обращения к Discogs API.") from e
