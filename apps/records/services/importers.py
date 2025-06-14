import logging
from typing import Optional

from records.models import Record, RecordConditions, Track

logger = logging.getLogger(__name__)


class DiscogsReleaseImporter:
    """Импортер релизов из Discogs в модели Django."""

    def __init__(self, api_client, model_factory, image_downloader):
        self.api_client = api_client
        self.model_factory = model_factory
        self.image_downloader = image_downloader

    def import_release(
        self, barcode: str, record: Record, save_image: bool = True
    ) -> Optional[Record]:
        """Основной метод импорта релиза по штрих-коду."""
        try:
            logger.debug(f"Starting import for barcode: {barcode}")
            release = self.api_client.search_release_by_barcode(barcode)
            if not release:
                logger.warning(f"No release found for barcode: {barcode}")
                return None

            # Важно: сначала сохраняем запись, если она новая
            if not record.pk:
                record.save()

            self._update_record(release, record, save_image)
            return record
        except Exception as e:
            logger.error(f"Import error for barcode {barcode}: {str(e)}", exc_info=True)
            return None

    def _update_basic_fields(self, release, record: Record):
        """Обновляет основные поля записи."""
        record.title = release.title
        record.release_year = getattr(release, "year", None)
        record.catalog_number = release.labels[0].catno if release.labels else None
        record.country = getattr(release, "country", None)
        record.notes = getattr(release, "notes", None)
        record.condition = RecordConditions.M
        record.discogs_id = release.id

    def _update_relations(self, release, record: Record):
        """Обновляет связи ManyToMany."""
        if release.labels:
            record.label = self.model_factory.create_or_update_label(release.labels[0])

        record.artists.set(
            [self.model_factory.create_or_update_artist(a) for a in release.artists]
            if hasattr(release, "artists")
            else []
        )

        record.genres.set(
            [
                self.model_factory.create_or_update_genre(g)
                for g in getattr(release, "genres", [])
            ]
        )

        record.styles.set(
            [
                self.model_factory.create_or_update_style(s)
                for s in getattr(release, "styles", [])
            ]
        )

        record.formats.set(
            self.model_factory.create_or_update_formats(getattr(release, "formats", []))
        )

    def _update_record(self, release, record: Record, save_image: bool):
        """Обновляет запись данными релиза."""
        # 1. Сначала обновляем и сохраняем основные поля
        self._update_basic_fields(release, record)
        record.save()  # Важно: сохраняем запись перед установкой связей

        # 2. Затем обновляем связи ManyToMany
        self._update_relations(release, record)

        # 3. Обновляем треки
        if hasattr(release, "tracklist") and release.tracklist:
            self._update_tracks(record, release.tracklist)

        # 4. Загружаем обложку
        if save_image:
            self.image_downloader.download_cover(release, record)

    def _update_tracks(self, record: Record, tracklist):
        """Обновляет треки для записи."""
        for track in tracklist:
            Track.objects.update_or_create(
                record=record,
                position=track.position,
                defaults={"title": track.title, "duration": track.duration},
            )
