import time
from datetime import datetime
from typing import Optional

import discogs_client
import requests

from django.conf import settings
from django.core.files.base import ContentFile

from apps.records.models import (
    Artist,
    Genre,
    Label,
    Record,
    RecordConditions,
    RecordFormats,
    Style,
    Track,
)


class DiscogsService:
    """
    Сервис для работы с Discogs API через Personal Access Token
    """

    def __init__(self):
        self.client = discogs_client.Client(
            user_agent="VinylCatalog/1.0 +http://localhost",  # Укажите свой User-Agent
            user_token=settings.DISCOGS_TOKEN,  # Только user_token
        )
        self.request_delay = (
            1.2  # Задержка между запросами (Discogs требует минимум 1 сек)
        )

    def _make_request(self, func, *args, **kwargs):
        """Обертка для запросов с задержкой и обработкой ошибок"""
        time.sleep(self.request_delay)
        try:
            return func(*args, **kwargs)
        except discogs_client.exceptions.HTTPError as e:
            if e.status_code == 401:
                raise Exception(
                    "Ошибка аутентификации. Проверьте DISCOGS_USER_TOKEN в settings.py"
                )
            raise

    def import_release_by_barcode(
        self, barcode: str, record: Record, save_image: bool = True
    ) -> Optional[Record]:
        """
        Импортирует релиз по штрих-коду
        Args:
            barcode: Штрих-код релиза
            save_image: Сохранять ли обложку
        Returns:
            Record или None если релиз не найден
        """
        try:
            # Поиск релиза
            results = self._make_request(self.client.search, barcode, type="release")
            if not results:
                return None

            release = results[0]
            self._make_request(release.refresh)  # Получаем полные данные

            # Обработка данных релиза
            artists = (
                self._process_artists(release.artists)
                if hasattr(release, "artists") and release.artists
                else []
            )
            label = (
                self._process_label(release.labels[0])
                if hasattr(release, "labels") and release.labels
                else None
            )
            genres = (
                self._process_genres(release.genres)
                if hasattr(release, "genres") and release.genres
                else []
            )
            styles = (
                self._process_styles(release.styles)
                if hasattr(release, "styles") and release.styles
                else []
            )

            # Получаем дату релиза (пробуем released, затем year)
            release_date = None
            if hasattr(release, "released") and release.released:
                release_date = self._parse_release_date(release.released)
            elif hasattr(release, "year") and release.year:
                release_date = datetime(int(release.year), 1, 1).date()

            # Обновляем поля записи
            record.title = release.title
            record.label = label
            record.release_date = release_date
            record.catalog_number = (
                release.labels[0].catno
                if hasattr(release, "labels") and release.labels
                else None
            )
            record.barcode = barcode
            record.format = (
                self._determine_format(release.formats)
                if hasattr(release, "formats")
                else RecordFormats.OTHER
            )
            record.country = getattr(release, "country", None)
            record.notes = getattr(release, "notes", None)
            record.condition = RecordConditions.NM
            record.discogs_id = release.id

            # Сохраняем изменения в записи перед установкой связей
            record.save()

            # Установка связей ManyToMany (теперь у записи есть ID)
            record.artists.set(artists)
            record.genres.set(genres)
            record.styles.set(styles)

            # Треки
            if hasattr(release, "tracklist") and release.tracklist:
                self._process_tracks(record, release.tracklist)

            # Обложка
            if save_image and hasattr(release, "images") and release.images:
                self._download_cover_image(record, release.images[0]["uri"])

            return record

        except Exception as e:
            print(f"Ошибка импорта: {str(e)}")
            return None

    # Вспомогательные методы
    def _process_artists(self, artists_data) -> list[Artist]:
        artists = []
        for artist_data in artists_data:
            artist, _ = Artist.objects.get_or_create(
                discogs_id=artist_data.id, defaults={"name": artist_data.name}
            )
            artists.append(artist)
        return artists

    def _process_label(self, label_data) -> Label:
        label, _ = Label.objects.get_or_create(
            discogs_id=label_data.id,
            defaults={
                "name": label_data.name,
                "description": f"Discogs ID: {label_data.id}",
            },
        )
        return label

    def _process_genres(self, genres_data) -> list[Genre]:
        genres = []
        for genre_name in genres_data:
            genre, _ = Genre.objects.get_or_create(name=genre_name)
            genres.append(genre)
        return genres

    def _process_styles(self, styles_data) -> list[Style]:
        styles = []
        for style_name in styles_data:
            style, _ = Style.objects.get_or_create(name=style_name)
            styles.append(style)
        return styles

    def _process_tracks(self, record: Record, tracklist_data):
        for track_data in tracklist_data:
            Track.objects.update_or_create(
                record=record,
                position=track_data.position,
                defaults={"title": track_data.title, "duration": track_data.duration},
            )

    def _download_cover_image(self, record: Record, image_url: str):
        """Загружает обложку"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:60.0) Gecko/20100101 Firefox/60.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }

            response = requests.get(image_url, headers=headers, timeout=20)
            response.raise_for_status()

            filename = f"cover_{record.discogs_id}_{int(time.time())}.jpeg"

            record.cover_image.save(filename, ContentFile(response.content))

            print(f"Обложка успешно загружена: {filename}")

        except Exception as e:
            print(f"Ошибка загрузки обложки {image_url}: {str(e)}")

    def _parse_release_date(self, date_str: Optional[str]) -> Optional[datetime.date]:
        if not date_str:
            return None

        date_str = str(date_str).strip()

        try:
            # Сначала пробуем стандартные форматы
            for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%b %d, %Y", "%Y-%m", "%Y"):
                try:
                    return datetime.strptime(date_str, fmt).date()
                except ValueError:
                    continue

            # Если дата содержит только год
            if date_str.isdigit() and len(date_str) == 4:
                return datetime(int(date_str), 1, 1).date()

            return None
        except Exception as e:
            print(f"Ошибка парсинга даты '{date_str}': {str(e)}")
            return None

    def _determine_format(self, formats_data) -> str:
        if not formats_data:
            return RecordFormats.OTHER

        # Получаем первый элемент формата (основной формат)
        format_info = formats_data[0]

        # Проверяем описания формата (в descriptions часто содержится точный тип)
        descriptions = format_info.get('descriptions', [])
        format_name = format_info['name'].upper()

        # Сначала проверяем описания (там может быть "LP", "Album" и т.д.)
        for desc in descriptions:
            desc_upper = desc.upper()
            if desc_upper in {'LP', '2LP', '3LP', 'EP', '12"', '10"', '7"'}:
                format_name = desc_upper
                break

        # Обработка количества дисков
        qty = int(format_info.get('qty', 1))

        # Определение формата
        if format_name == 'LP':
            if qty == 2:
                return RecordFormats.LP2
            elif qty == 3:
                return RecordFormats.LP3
            return RecordFormats.LP

        # Простые соответствия
        simple_mapping = {
            'EP': RecordFormats.EP,
            '7"': RecordFormats.SEVEN,
            '10"': RecordFormats.TEN,
            '12"': RecordFormats.TWELVE,
            'PICTURE DISC': RecordFormats.PIC,
            'SHAPED': RecordFormats.SHAPED,
            'FLEXI': RecordFormats.FLEXI,
            'ACETATE': RecordFormats.ACETATE,
            'TEST PRESSING': RecordFormats.TEST
        }

        return simple_mapping.get(format_name, RecordFormats.OTHER)
