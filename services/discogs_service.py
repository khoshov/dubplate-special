import discogs_client
import time
import requests
from datetime import datetime
from typing import Optional
from django.conf import settings
from django.core.files.base import ContentFile

from apps.records.models import (
    Track, RecordConditions, RecordFormats, Record,
    Style, Genre, Label, Artist
)


class DiscogsService:
    """
    Сервис для работы с Discogs API через Personal Access Token
    """

    def __init__(self):
        self.client = discogs_client.Client(
            user_agent="VinylCatalog/1.0 +http://localhost",  # Укажите свой User-Agent
            user_token=settings.DISCOGS_TOKEN  # Только user_token
        )
        self.request_delay = 1.2  # Задержка между запросами (Discogs требует минимум 1 сек)

    def _make_request(self, func, *args, **kwargs):
        """Обертка для запросов с задержкой и обработкой ошибок"""
        time.sleep(self.request_delay)
        try:
            return func(*args, **kwargs)
        except discogs_client.exceptions.HTTPError as e:
            if e.status_code == 401:
                raise Exception("Ошибка аутентификации. Проверьте DISCOGS_USER_TOKEN в settings.py")
            raise

    def import_release_by_barcode(self, barcode: str, save_image: bool = True) -> Optional[Record]:
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
            results = self._make_request(
                self.client.search,
                barcode,
                type='release'
            )
            if not results:
                return None

            release = results[0]
            self._make_request(release.refresh)  # Получаем полные данные

            # Обработка данных релиза
            artists = self._process_artists(release.artists)
            label = self._process_label(release.labels[0]) if release.labels else None
            genres = self._process_genres(release.genres)
            styles = self._process_styles(release.styles)

            # Создание/обновление записи
            record_data = {
                'title': release.title,
                'label': label,
                'release_date': self._parse_release_date(getattr(release, 'released', None)),
                'catalog_number': release.labels[0].catno if release.labels else None,
                'barcode': barcode,
                'format': self._determine_format(release.formats),
                'country': release.country,
                'notes': getattr(release, 'notes', None),
                'condition': RecordConditions.NM,
                'discogs_id': release.id,
            }

            record, _ = Record.objects.update_or_create(
                discogs_id=release.id,
                defaults=record_data
            )

            # Установка связей
            record.artists.set(artists)
            record.genres.set(genres)
            record.styles.set(styles)

            # Треки
            self._process_tracks(record, release.tracklist)

            # Обложка
            if save_image and release.images:
                self._download_cover_image(record, release.images[0]['uri'])

            return record

        except Exception as e:
            print(f"Ошибка импорта: {str(e)}")
            return None

    # Вспомогательные методы
    def _process_artists(self, artists_data) -> list[Artist]:
        artists = []
        for artist_data in artists_data:
            artist, _ = Artist.objects.get_or_create(
                discogs_id=artist_data.id,
                defaults={'name': artist_data.name}
            )
            artists.append(artist)
        return artists

    def _process_label(self, label_data) -> Label:
        label, _ = Label.objects.get_or_create(
            discogs_id=label_data.id,
            defaults={
                'name': label_data.name,
                'description': f"Discogs ID: {label_data.id}"
            }
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
                defaults={
                    'title': track_data.title,
                    'duration': track_data.duration
                }
            )

    def _download_cover_image(self, record: Record, image_url: str):
        """Загружает обложку"""
        try:
            headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:60.0) Gecko/20100101 Firefox/60.0",
                       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                       "Accept-Language": "en-US,en;q=0.9"
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
        try:
            for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
                try:
                    return datetime.strptime(date_str, fmt).date()
                except ValueError:
                    continue
            return None
        except (ValueError, AttributeError):
            return None

    def _determine_format(self, formats_data) -> str:
        if not formats_data:
            return RecordFormats.OTHER

        format_name = formats_data[0]['name'].upper()
        qty = int(formats_data[0].get('qty', 1))

        format_mapping = {
            'LP': RecordFormats.LP,
            '2LP': RecordFormats.LP2,
            '3LP': RecordFormats.LP3,
            'EP': RecordFormats.EP,
            '7"': RecordFormats.SEVEN,
            '10"': RecordFormats.TEN,
            '12"': RecordFormats.TWELVE,
            'BOX': RecordFormats.BOX,
            'PICTURE DISC': RecordFormats.PIC,
            'SHAPED': RecordFormats.SHAPED,
            'FLEXI': RecordFormats.FLEXI,
            'ACETATE': RecordFormats.ACETATE,
            'TEST PRESSING': RecordFormats.TEST,
            'CD': RecordFormats.OTHER,
            'CASSETTE': RecordFormats.OTHER,
            'DIGITAL': RecordFormats.OTHER,
        }

        if qty > 1 and format_name == 'LP':
            if qty == 2:
                return RecordFormats.LP2
            elif qty == 3:
                return RecordFormats.LP3

        return format_mapping.get(format_name, RecordFormats.OTHER)
