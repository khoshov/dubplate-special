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
    """Service for interacting with Discogs API"""

    def __init__(self):
        self.client = discogs_client.Client(
            user_agent=settings.DISCOGS_USER_AGENT,
            user_token=settings.DISCOGS_TOKEN,
        )
        self.request_delay = 1.2  # Discogs requires 1s between requests

    def _make_request(self, func, *args, **kwargs):
        """Wrapper for requests with rate limiting"""
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
        """Import release by barcode from Discogs"""
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
        """Find release by barcode"""
        results = self._make_request(self.client.search, barcode, type="release")
        if results:
            release = results[0]
            self._make_request(release.refresh)  # Get full release data
            return release
        return None

    def _process_release_data(self, release, record: Record, save_image: bool):
        """Process all release data and update record"""
        # Save the main record with basic fields
        record.title = release.title
        record.release_year = getattr(release, "year", None)
        record.catalog_number = release.labels[0].catno if release.labels else None
        record.barcode = record.barcode  # Keep original barcode
        record.country = getattr(release, "country", None)
        record.notes = getattr(release, "notes", None)
        record.condition = RecordConditions.M
        record.discogs_id = release.id

        # Save the record before establishing ManyToMany relationships
        record.save()

        # Establish relationships
        record.label = (
            self._process_label(release.labels[0]) if release.labels else None
        )
        record.artists.set(self._process_items(release.artists, self._process_artist))
        record.genres.set(self._process_items(release.genres, self._process_genre))
        record.styles.set(self._process_items(release.styles, self._process_style))
        record.formats.set(self._determine_formats(release.formats))

        # Save again after establishing relationships
        record.save()

        if hasattr(release, "tracklist") and release.tracklist:
            self._process_tracks(record, release.tracklist)

        if save_image and hasattr(release, "images") and release.images:
            self._download_cover_image(record, release.images[0]["uri"])

    # Generic processing methods
    def _process_items(self, items, processor):
        """Generic method to process related items"""
        return [processor(item) for item in items] if items else []

    def _process_artist(self, artist_data):
        """Create or get Artist instance"""
        return Artist.objects.get_or_create(
            discogs_id=artist_data.id, defaults={"name": artist_data.name}
        )[0]

    def _process_genre(self, genre_name):
        """Create or get Genre instance"""
        return Genre.objects.get_or_create(name=genre_name)[0]

    def _process_style(self, style_name):
        """Create or get Style instance"""
        return Style.objects.get_or_create(name=style_name)[0]

    def _process_label(self, label_data):
        """Create or get Label instance"""
        return Label.objects.get_or_create(
            discogs_id=label_data.id,
            defaults={
                "name": label_data.name,
                "description": f"Discogs ID: {label_data.id}",
            },
        )[0]

    # Track processing
    def _process_tracks(self, record: Record, tracklist_data):
        """Process all tracks for the release"""
        for track in tracklist_data:
            Track.objects.update_or_create(
                record=record,
                position=track.position,
                defaults={"title": track.title, "duration": track.duration},
            )

    # Image processing
    def _download_cover_image(self, record: Record, image_url: str):
        """Download and save cover image"""
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
        """Определяет все форматы релиза, используя только name"""
        if not formats_data:
            return []

        format_objects = []

        for format_info in formats_data:
            qty = int(format_info.get('qty', 1))
            descriptions = [d.upper() for d in format_info.get('descriptions', [])]

            # Основные форматы
            if 'LP' in descriptions:
                format_name = f"{qty}LP" if qty > 1 else 'LP'
                format_obj, _ = Format.objects.get_or_create(name=format_name)
                format_objects.append(format_obj)

            # Дополнительные описания
            for desc in descriptions:
                if desc not in ['LP', '2LP', '3LP']:  # Исключаем дублирование
                    format_obj, _ = Format.objects.get_or_create(name=desc)
                    format_objects.append(format_obj)

        return format_objects
