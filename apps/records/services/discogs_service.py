import time
from typing import Optional

import discogs_client
import requests

from django.conf import settings
from django.core.files.base import ContentFile

from records.models import (
    Artist,
    Format,
    Genre,
    Label,
    Record,
    RecordConditions,
    Style,
    Track,
)


class DiscogsService:
    """Сервис для работы с Discogs API для импорта музыкальных релизов.

    Обрабатывает аутентификацию, ограничение запросов и преобразование данных
    из Discogs API в модели базы данных.

    Атрибуты:
        client (discogs_client.Client): Клиент Discogs API
        request_delay (float): Задержка между запросами в секундах
    """

    def __init__(self):
        self.client = discogs_client.Client(
            user_agent=settings.DISCOGS_USER_AGENT,
            user_token=settings.DISCOGS_TOKEN,
        )
        self.request_delay = 1.2  # Discogs requires 1s between requests

    def _make_request(self, func, *args, **kwargs):
        """Обертка для запросов к Discogs API с ограничением частоты и обработкой ошибок.

        Args:
            func: Метод клиента Discogs для вызова
            *args: Позиционные аргументы
            **kwargs: Именованные аргументы

        Returns:
            Результат вызова API

        Raises:
            Exception: Если аутентификация не удалась (HTTP 401)
            discogs_client.exceptions.HTTPError: Другие HTTP ошибки
        """
        time.sleep(self.request_delay)
        try:
            return func(*args, **kwargs)
        except discogs_client.exceptions.HTTPError as e:
            if e.status_code == 401:
                raise Exception("Authentication error. Check DISCOGS_TOKEN in settings")
            raise

    def import_release_by_barcode(
        self, barcode: str, record: Record, save_image: bool = True
    ) -> Optional[Record]:
        """Основной метод для импорта данных релиза по штрих-коду из Discogs.

        Args:
            barcode: Штрих-код для поиска
            record: Экземпляр Record для заполнения данными
            save_image: Загружать ли обложку. По умолчанию True.

        Returns:
            Record: Обновленный экземпляр Record при успехе, иначе None
        """
        try:
            release = self._get_release_by_barcode(barcode)
            if not release:
                return None

            self._process_release_data(release, record, save_image)
            return record

        except Exception as e:
            print(f"Import error: {str(e)}")
            return None

    def _get_release_by_barcode(self, barcode: str):
        """Ищет релиз в Discogs по штрих-коду.

        Args:
            barcode: Штрих-код для поиска

        Returns:
            discogs_client.Release: Найденный релиз или None
        """
        results = self._make_request(self.client.search, barcode, type="release")
        if results:
            release = results[0]
            self._make_request(release.refresh)  # Get full release data
            return release
        return None

    def _process_release_data(self, release, record: Record, save_image: bool):
        """Обрабатывает данные релиза и обновляет экземпляр Record.

        Args:
            release: Объект релиза Discogs
            record: Экземпляр Record для обновления
            save_image: Загружать ли обложку
        """
        # Основные поля
        record.title = release.title
        record.release_year = getattr(release, "year", None)
        record.catalog_number = release.labels[0].catno if release.labels else None
        record.barcode = record.barcode  # Keep original barcode
        record.country = getattr(release, "country", None)
        record.notes = getattr(release, "notes", None)
        record.condition = RecordConditions.M
        record.discogs_id = release.id
        record.save()

        # Связи
        record.label = (
            self._process_label(release.labels[0]) if release.labels else None
        )
        record.artists.set(self._process_items(release.artists, self._process_artist))
        record.genres.set(self._process_items(release.genres, self._process_genre))
        record.styles.set(self._process_items(release.styles, self._process_style))
        record.formats.set(self._determine_formats(release.formats))

        # Треки и обложка
        record.save()

        if hasattr(release, "tracklist") and release.tracklist:
            self._process_tracks(record, release.tracklist)

        if save_image and hasattr(release, "images") and release.images:
            self._download_cover_image(record, release.images[0]["uri"])

    # Generic processing methods
    def _process_items(self, items, processor):
        """Универсальный обработчик связанных элементов.

        Args:
            items: Список элементов для обработки
            processor: Функция для обработки каждого элемента

        Returns:
            Список обработанных элементов
        """
        return [processor(item) for item in items] if items else []

    def _process_artist(self, artist_data):
        """Создает или получает артиста из данных Discogs.

        Args:
            artist_data: Объект артиста Discogs

        Returns:
            Artist: Локальный экземпляр Artist
        """
        return Artist.objects.get_or_create(
            discogs_id=artist_data.id, defaults={"name": artist_data.name}
        )[0]

    def _process_genre(self, genre_name):
        """Создает или получает жанр.

        Args:
            genre_name: Название жанра из Discogs

        Returns:
            Genre: Локальный экземпляр Genre
        """
        return Genre.objects.get_or_create(name=genre_name)[0]

    def _process_style(self, style_name):
        """Создает или получает стиль.

        Args:
            style_name: Название стиля из Discogs

        Returns:
            Style: Локальный экземпляр Style
        """
        return Style.objects.get_or_create(name=style_name)[0]

    def _process_label(self, label_data):
        """Создает или получает лейбл из данных Discogs.

        Args:
            label_data: Объект лейбла Discogs

        Returns:
            Label: Локальный экземпляр Label
        """
        return Label.objects.get_or_create(
            discogs_id=label_data.id,
            defaults={
                "name": label_data.name,
                "description": f"Discogs ID: {label_data.id}",
            },
        )[0]

    # Track processing
    def _process_tracks(self, record: Record, tracklist_data):
        """Обрабатывает трек-лист и создает/обновляет треки.

        Args:
            record: Родительский экземпляр Record
            tracklist_data: Список треков из Discogs
        """
        for track in tracklist_data:
            Track.objects.update_or_create(
                record=record,
                position=track.position,
                defaults={"title": track.title, "duration": track.duration},
            )

    # Image processing
    def _download_cover_image(self, record: Record, image_url: str):
        """Загружает и сохраняет обложку релиза.

        Args:
            record: Record для связывания с обложкой
            image_url: URL обложки
        """
        try:
            response = requests.get(
                image_url,
                headers={"User-Agent": settings.DISCOGS_USER_AGENT},
                timeout=20,
            )
            response.raise_for_status()

            filename = f"cover_{record.discogs_id}_{int(time.time())}.jpeg"
            record.cover_image.save(filename, ContentFile(response.content))
            print(f"Cover downloaded: {filename}")

        except Exception as e:
            print(f"Error downloading cover: {str(e)}")

    # Format processing
    def _determine_formats(self, formats_data) -> list:
        """Обрабатывает информацию о форматах релиза.

        Args:
            formats_data: Список форматов из Discogs

        Returns:
            Список экземпляров Format для этого релиза
        """
        if not formats_data:
            return []

        format_objects = []

        for format_info in formats_data:
            qty = int(format_info.get("qty", 1))
            descriptions = [d.upper() for d in format_info.get("descriptions", [])]

            # Основные форматы
            if "LP" in descriptions:
                format_name = f"{qty}LP" if qty > 1 else "LP"
                format_obj, _ = Format.objects.get_or_create(name=format_name)
                format_objects.append(format_obj)

            # Дополнительные описания
            for desc in descriptions:
                if desc not in ["LP", "2LP", "3LP"]:  # Исключаем дублирование
                    format_obj, _ = Format.objects.get_or_create(name=desc)
                    format_objects.append(format_obj)

        return format_objects
