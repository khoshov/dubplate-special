import logging
from typing import Optional

from django.db import IntegrityError, transaction

from records.models import Record, RecordConditions, Track
from records.services.constants import DiscogsConstants

logger = logging.getLogger(__name__)


class DiscogsReleaseImporter:
    """Импортер релизов из Discogs в модели Django.

    Args:
        api_client: Клиент Discogs API.
        model_factory: Фабрика для создания моделей.
        image_downloader: Загрузчик обложек.

    Methods:
        import_release_by_barcode: Импорт релиза по штрих-коду.
        import_release_by_catalog_number: Импорт релиза по каталожному номеру.
        import_release_by_identifier: Универсальный метод импорта.
    """

    def __init__(self, api_client, model_factory, image_downloader):
        self.api_client = api_client
        self.model_factory = model_factory
        self.image_downloader = image_downloader

    def import_release_by_identifier(
        self,
        identifier: str,
        identifier_type: str,
        record: Record,
        save_image: bool = True,
    ) -> Optional[Record]:
        """Универсальный метод импорта релиза.

        Args:
            identifier: Значение идентификатора для поиска.
            identifier_type: Тип идентификатора (barcode или catalog_number).
            record: Экземпляр модели Record для заполнения данными.
            save_image: Флаг, указывающий нужно ли загружать обложку.

        Returns:
            Optional[Record]: Заполненная модель Record или None при ошибке.
        """
        try:
            logger.debug(f"Starting import for {identifier_type}: {identifier}")

            if identifier_type == DiscogsConstants.IDENTIFIER_BARCODE:
                release = self.api_client.search_release_by_barcode(identifier)
            elif identifier_type == DiscogsConstants.IDENTIFIER_CATALOG:
                release = self.api_client.search_release_by_catalog_number(identifier)
            else:
                raise ValueError(f"Unknown identifier type: {identifier_type}")

            if not release:
                logger.warning(f"No release found for {identifier_type}: {identifier}")
                return None

            return self._import_release_data(release, record, save_image)
        except Exception as e:
            logger.error(
                f"Import error for {identifier_type} {identifier}: {str(e)}",
                exc_info=True,
            )
            return None

    def import_release_by_barcode(
        self, barcode: str, record: Record, save_image: bool = True
    ) -> Optional[Record]:
        """Импортирует релиз по штрих-коду в модель Record.

        Args:
            barcode: Штрих-код релиза для поиска в Discogs.
            record: Экземпляр модели Record для заполнения данными.
            save_image: Флаг, указывающий нужно ли загружать обложку.

        Returns:
            Optional[Record]: Заполненная модель Record или None при ошибке.
        """
        return self.import_release_by_identifier(
            barcode, DiscogsConstants.IDENTIFIER_BARCODE, record, save_image
        )

    def import_release_by_catalog_number(
        self, catalog_number: str, record: Record, save_image: bool = True
    ) -> Optional[Record]:
        """Импортирует релиз по каталожному номеру в модель Record.

        Args:
            catalog_number: Каталожный номер релиза для поиска в Discogs.
            record: Экземпляр модели Record для заполнения данными.
            save_image: Флаг, указывающий нужно ли загружать обложку.

        Returns:
            Optional[Record]: Заполненная модель Record или None при ошибке.
        """
        return self.import_release_by_identifier(
            catalog_number, DiscogsConstants.IDENTIFIER_CATALOG, record, save_image
        )

    def _import_release_data(
        self, release, record: Record, save_image: bool = True
    ) -> Optional[Record]:
        """Общий метод для импорта данных релиза.

        Args:
            release: Объект релиза из Discogs API.
            record: Экземпляр модели Record для заполнения данными.
            save_image: Флаг, указывающий нужно ли загружать обложку.

        Returns:
            Optional[Record]: Заполненная модель Record или существующая запись.
        """
        # Извлекаем данные из релиза заранее
        release_data = self._extract_release_data(release)

        # ВАЖНО: Проверяем все возможные дубликаты перед сохранением
        existing_record = self._find_existing_record(
            discogs_id=release_data["discogs_id"],
            barcode=release_data.get("barcode"),
            catalog_number=release_data.get("catalog_number"),
            exclude_pk=record.pk,
        )

        if existing_record:
            logger.info(
                f"Found existing record (ID: {existing_record.pk}) with matching identifiers. "
                f"Updating missing fields and returning existing record."
            )

            # Обновляем недостающие поля в существующей записи
            self._update_existing_record_fields(existing_record, release_data)

            # Удаляем временную запись, если она была создана
            if record.pk:
                logger.info(f"Deleting temporary record {record.pk}")
                record.delete()

            return existing_record

        # Если записи нет, продолжаем обычный импорт
        # Важно: сначала сохраняем запись, если она новая
        if not record.pk:
            record.save()

        # Используем транзакцию для безопасного обновления
        try:
            with transaction.atomic():
                self._update_record(release, record, save_image)
        except IntegrityError as e:
            logger.error(f"IntegrityError during import: {str(e)}")
            # Пытаемся найти конфликтующую запись
            if "discogs_id" in str(e):
                existing = Record.objects.filter(
                    discogs_id=release_data["discogs_id"]
                ).first()
            elif "catalog_number" in str(e):
                existing = Record.objects.filter(
                    catalog_number=release_data["catalog_number"]
                ).first()
            elif "barcode" in str(e):
                existing = Record.objects.filter(
                    barcode=release_data["barcode"]
                ).first()
            else:
                existing = None

            if existing:
                # Удаляем временную запись
                if record.pk:
                    record.delete()
                # Обновляем и возвращаем существующую
                self._update_existing_record_fields(existing, release_data)
                return existing

            # Если не можем найти существующую запись, перебрасываем исключение
            raise

        return record

    def _extract_release_data(self, release) -> dict:
        """Извлекает все данные из релиза Discogs.

        Args:
            release: Объект релиза из Discogs API.

        Returns:
            dict: Словарь с данными релиза.
        """
        data = {
            "discogs_id": release.id,
            "title": release.title,
            "year": getattr(release, "year", None),
            "country": getattr(release, "country", None),
            "notes": getattr(release, "notes", None),
            "catalog_number": release.labels[0].catno if release.labels else None,
            "barcode": None,
        }

        # Извлекаем barcode из identifiers
        if hasattr(release, "identifiers"):
            for identifier in release.identifiers:
                if identifier.type == "Barcode" and identifier.value:
                    data["barcode"] = identifier.value
                    break

        return data

    def _find_existing_record(
        self,
        discogs_id: int = None,
        barcode: str = None,
        catalog_number: str = None,
        exclude_pk: int = None,
    ) -> Optional[Record]:
        """Ищет существующую запись по любому из идентификаторов.

        Args:
            discogs_id: ID в Discogs.
            barcode: Штрих-код.
            catalog_number: Каталожный номер.
            exclude_pk: ID записи для исключения из поиска.

        Returns:
            Optional[Record]: Найденная запись или None.
        """
        query = Record.objects.all()

        if exclude_pk:
            query = query.exclude(pk=exclude_pk)

        # Проверяем по discogs_id
        if discogs_id:
            existing = query.filter(discogs_id=discogs_id).first()
            if existing:
                return existing

        # Проверяем по barcode
        if barcode:
            existing = query.filter(barcode=barcode).first()
            if existing:
                return existing

        # Проверяем по catalog_number
        if catalog_number:
            existing = query.filter(catalog_number=catalog_number).first()
            if existing:
                return existing

        return None

    def _update_existing_record_fields(self, record: Record, release_data: dict):
        """Обновляет недостающие поля в существующей записи.

        Args:
            record: Существующая запись для обновления.
            release_data: Словарь с данными из Discogs.
        """
        updated = False

        # Обновляем barcode если его нет
        if not record.barcode and release_data.get("barcode"):
            record.barcode = release_data["barcode"]
            updated = True
            logger.info(
                f"Updated barcode for existing record: {release_data['barcode']}"
            )

        # Обновляем catalog_number если его нет
        if not record.catalog_number and release_data.get("catalog_number"):
            record.catalog_number = release_data["catalog_number"]
            updated = True
            logger.info(
                f"Updated catalog_number for existing record: {release_data['catalog_number']}"
            )

        # Обновляем discogs_id если его нет
        if not record.discogs_id and release_data.get("discogs_id"):
            record.discogs_id = release_data["discogs_id"]
            updated = True
            logger.info(
                f"Updated discogs_id for existing record: {release_data['discogs_id']}"
            )

        if updated:
            try:
                record.save()
            except IntegrityError as e:
                logger.error(f"Failed to update existing record: {str(e)}")

    def _update_basic_fields(self, release, record: Record):
        """Обновляет основные поля записи.

        Args:
            release: Объект релиза из Discogs API.
            record: Экземпляр модели Record для обновления.
        """
        record.title = release.title
        record.release_year = getattr(release, "year", None)
        record.catalog_number = release.labels[0].catno if release.labels else None
        record.country = getattr(release, "country", None)
        record.notes = getattr(release, "notes", None)
        record.condition = RecordConditions.M
        record.discogs_id = release.id

        # Также пытаемся получить barcode из identifiers
        if hasattr(release, "identifiers"):
            for identifier in release.identifiers:
                if (
                    identifier.type == "Barcode"
                    and identifier.value
                    and not record.barcode
                ):
                    record.barcode = identifier.value
                    break

    def _update_relations(self, release, record: Record):
        """Обновляет связи ManyToMany записи.

        Args:
            release: Объект релиза из Discogs API.
            record: Экземпляр модели Record для обновления.
        """
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
        """Обновляет запись данными релиза.

        Args:
            release: Объект релиза из Discogs API.
            record: Экземпляр модели Record для обновления.
            save_image: Флаг, указывающий нужно ли загружать обложку.

        Note:
            Выполняет обновление в следующем порядке:
            1. Основные поля
            2. Связи ManyToMany
            3. Треки
            4. Обложка (если требуется)
        """
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
        """Обновляет треки для записи, включая ссылки на видео.

        Args:
            record: Экземпляр модели Record.
            tracklist: Список треков из Discogs API.
        """
        # Получаем все видео релиза
        release_videos = self.api_client.get_release_videos(record.discogs_id) or []

        for track in tracklist:
            track_url = None

            # Пытаемся найти видео для этого трека
            for video in release_videos:
                # Простая проверка по названию трека
                if track.title.lower() in video["title"].lower():
                    track_url = video["url"]
                    break

            Track.objects.update_or_create(
                record=record,
                position=track.position,
                defaults={
                    "title": track.title,
                    "duration": track.duration,
                    "youtube_url": track_url,
                },
            )
