import logging
import time
from typing import Any, Dict, List, Optional

import discogs_client
from rest_framework import status

from django.conf import settings

logger = logging.getLogger(__name__)


class DiscogsService:
    """Сервис для работы с Discogs API.

    Инкапсулирует всю логику работы с Discogs API, включая
    поиск релизов, обработку ошибок и rate limiting.

    Attributes:
        RATE_LIMIT_WAIT_TIME: Время ожидания при превышении лимита запросов (сек).
        client: Клиент для работы с Discogs API.
    """

    RATE_LIMIT_WAIT_TIME = 30

    def __init__(self):
        """Инициализация сервиса с настройками из Django settings."""
        self.client = discogs_client.Client(
            settings.DISCOGS_USER_AGENT, user_token=settings.DISCOGS_TOKEN
        )

    def search_by_barcode(self, barcode: str) -> Optional[discogs_client.Release]:
        """Поиск релиза по штрих-коду.

        Args:
            barcode: Штрих-код для поиска.

        Returns:
            Объект релиза или None, если не найден.
        """
        try:
            results = self._make_request(
                self.client.search, barcode=barcode, type="release"
            )

            if results:
                search_result = results[0]
                # Пробуем refresh для получения полных данных
                try:
                    self._make_request(search_result.refresh)
                except Exception as e:
                    logger.warning(f"Failed to refresh search result: {e}")

                return search_result

        except Exception as e:
            logger.error(f"Barcode search failed for {barcode}: {e}")

        return None

    def search_by_catalog_number(
        self, catalog_number: str
    ) -> Optional[discogs_client.Release]:
        """Поиск релиза по каталожному номеру.

        Args:
            catalog_number: Каталожный номер для поиска.

        Returns:
            Объект релиза или None, если не найден.
        """
        try:
            results = self._make_request(
                self.client.search, catno=catalog_number, type="release"
            )

            if results:
                search_result = results[0]
                # Пробуем refresh для получения полных данных
                try:
                    self._make_request(search_result.refresh)
                except Exception as e:
                    logger.warning(f"Failed to refresh search result: {e}")

                return search_result

        except Exception as e:
            logger.error(f"Catalog number search failed for {catalog_number}: {e}")

        return None

    def get_release(self, discogs_id: int) -> Optional[discogs_client.Release]:
        """Получение релиза по Discogs ID.

        Args:
            discogs_id: Идентификатор релиза в Discogs.

        Returns:
            Объект релиза или None, если не найден.
        """
        try:
            return self._make_request(self.client.release, discogs_id)
        except Exception as e:
            logger.error(f"Failed to get release {discogs_id}: {e}")
            return None

    def get_release_videos(self, discogs_id: int) -> List[Dict[str, str]]:
        """Получение списка видео для релиза.

        Args:
            discogs_id: Идентификатор релиза в Discogs.

        Returns:
            Список словарей с информацией о видео.
            Каждый словарь содержит ключи 'title' и 'url'.
        """
        try:
            release = self._make_request(self.client.release, discogs_id)
            if hasattr(release, "videos") and release.videos:
                return [
                    {"title": video.title, "url": video.url} for video in release.videos
                ]
        except Exception as e:
            logger.error(f"Failed to get videos for release {discogs_id}: {e}")
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
        """Парсинг данных штрих-кода с валидацией.

        Принимает только значения, состоящие из цифр.

        Args:
            barcode_data: Данные штрих-кода (строка или список).

        Returns:
            Очищенный и валидированный штрих-код или None.
        """

        def is_valid_barcode(value: str) -> bool:
            """Проверка, является ли значение валидным штрих-кодом.

            Валидный штрих-код должен состоять только из цифр.
            """
            if not value:
                return False

            # Очищаем от пробелов и дефисов
            clean_value = value.replace(" ", "").replace("-", "")

            # Проверяем, что это только цифры и длина разумная
            return clean_value.isdigit() and 6 <= len(clean_value) <= 20

        if isinstance(barcode_data, list) and barcode_data:
            # Ищем первый валидный штрих-код в списке
            for bc in barcode_data:
                bc_str = str(bc).strip()
                if is_valid_barcode(bc_str):
                    # Возвращаем очищенный от пробелов и дефисов
                    return bc_str.replace(" ", "").replace("-", "")

            # Не нашли валидный штрих-код
            return None

        elif isinstance(barcode_data, str) and barcode_data:
            # Проверяем одиночное значение
            if is_valid_barcode(barcode_data):
                return barcode_data.strip().replace(" ", "").replace("-", "")
            else:
                return None

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
            Exception: При ошибке аутентификации.
            discogs_client.exceptions.HTTPError: При других HTTP ошибках.
        """
        try:
            return func(*args, **kwargs)
        except discogs_client.exceptions.HTTPError as e:
            if e.status_code == status.HTTP_401_UNAUTHORIZED:
                raise Exception("Discogs authentication error. Check your token.")
            elif e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                logger.warning("Rate limit exceeded, waiting...")
                time.sleep(self.RATE_LIMIT_WAIT_TIME)
                return self._make_request(func, *args, **kwargs)
            raise
