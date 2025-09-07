import logging
from functools import partial
from typing import List, Optional, Tuple

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
from records.services.discogs_service import DiscogsService
from records.services.image_service import ImageService

from django.db import transaction

from apps.records.services.tasks import dl_track

logger = logging.getLogger(__name__)


class RecordService:
    """Сервис для работы с записями.

    Инкапсулирует всю бизнес-логику работы с записями,
    включая импорт из Discogs, управление остатками и проверку дубликатов.

    Attributes:
        discogs_service: Сервис для работы с Discogs API.
        image_service: Сервис для работы с изображениями.
    """

    def __init__(self, discogs_service: DiscogsService, image_service: ImageService):
        """Инициализация сервиса.

        Args:
            discogs_service: Экземпляр сервиса Discogs.
            image_service: Экземпляр сервиса изображений.
        """
        self.discogs_service = discogs_service
        self.image_service = image_service

    def import_from_discogs(
        self,
        barcode: Optional[str] = None,
        catalog_number: Optional[str] = None,
        save_image: bool = True,
    ) -> Tuple[Record, bool]:
        """Импорт записи из Discogs.

        Ищет релиз в Discogs по штрих-коду или каталожному номеру
        и создаёт запись в базе данных.

        Args:
            barcode: Штрих-код для поиска.
            catalog_number: Каталожный номер для поиска.
            save_image: Флаг загрузки обложки.

        Returns:
            Кортеж из записи и флага был ли выполнен импорт.
            (record, True) - если запись импортирована из Discogs.
            (record, False) - если вернули существующую запись.

        Raises:
            ValueError: Если не указаны идентификаторы или запись не найдена.
        """
        # Проверяем существующие записи
        existing = self._find_existing_record(barcode, catalog_number)
        if existing:
            logger.info(f"Found existing record: {existing.id}")
            self._update_missing_identifiers(existing, barcode, catalog_number)
            return existing, False

        # Ищем в Discogs
        if barcode:
            discogs_release = self.discogs_service.search_by_barcode(barcode)
        elif catalog_number:
            discogs_release = self.discogs_service.search_by_catalog_number(
                catalog_number
            )
        else:
            raise ValueError("Either barcode or catalog_number is required")

        if not discogs_release:
            raise ValueError("Release not found in Discogs")

        # Создаём запись в транзакции
        with transaction.atomic():
            record = self._create_record_from_discogs(
                discogs_release,
                search_barcode=barcode,
                search_catalog_number=catalog_number,
            )

            # Загружаем обложку
            if save_image and discogs_release.images:
                success = self.image_service.download_cover(
                    record, discogs_release.images[0]["uri"]
                )
                if success:
                    logger.info(f"Cover downloaded for record {record.id}")

        logger.info(f"Record imported successfully: {record.id}")
        return record, True

    def update_from_discogs(self, record: Record, update_image: bool = True) -> Record:
        """Обновление существующей записи из Discogs.

        Обновляет следующие данные:
        1. Основные поля: title, year, country, notes
        2. Идентификаторы: barcode и catalog_number (если отсутствуют)
        3. Связи: артисты, лейбл, жанры, стили, форматы (полная замена)
        4. Треки: полная замена (удаляются старые, создаются новые)
        5. Обложка: загружается если отсутствует и update_image=True

        НЕ обновляются:
        - condition (состояние)
        - stock (остатки)
        - price (цена)
        - discogs_id (не меняется)

        Args:
            record: Запись для обновления.
            update_image: Флаг обновления обложки.

        Returns:
            Обновлённая запись.

        Raises:
            ValueError: Если у записи нет discogs_id или она не найдена в Discogs.
        """
        if not record.discogs_id:
            raise ValueError("Record must have discogs_id for update")

        logger.info(
            f"Starting update from Discogs for record {record.id} "
            f"(Discogs ID: {record.discogs_id}, "
            f"Barcode: '{record.barcode}', "
            f"Catalog: '{record.catalog_number}')"
        )

        discogs_release = self.discogs_service.get_release(record.discogs_id)
        if not discogs_release:
            raise ValueError(f"Release {record.discogs_id} not found in Discogs")

        with transaction.atomic():
            # 1. Обновляем основные поля
            self._update_record_fields(record, discogs_release)

            # 2. Обновляем связи (полная замена)
            self._update_record_relations(record, discogs_release)

            # 3. Обновляем треки (полная замена)
            self._update_tracks(record, discogs_release)

            # 4. Обновляем обложку если нужно
            if update_image and not record.cover_image and discogs_release.images:
                if self.image_service.download_cover(
                    record, discogs_release.images[0]["uri"]
                ):
                    logger.info(f"Cover image updated for record {record.id}")

        # Финальное логирование
        logger.info(
            f"Record {record.id} successfully updated from Discogs. "
            f"Final state - Barcode: '{record.barcode}', "
            f"Catalog: '{record.catalog_number}'"
        )

        return record

    def check_duplicate(
        self,
        barcode: Optional[str] = None,
        catalog_number: Optional[str] = None,
        discogs_id: Optional[int] = None,
        exclude_pk: Optional[int] = None,
    ) -> Optional[Record]:
        """Проверка на дубликаты по идентификаторам.

        Проверяет существование записи с указанными идентификаторами
        и обновляет недостающие идентификаторы.

        Args:
            barcode: Штрих-код для проверки.
            catalog_number: Каталожный номер для проверки.
            discogs_id: ID в Discogs для проверки.
            exclude_pk: ID записи для исключения из поиска.

        Returns:
            Найденная запись-дубликат или None.
        """
        # Проверяем по прямым совпадениям
        if discogs_id:
            record = Record.objects.find_by_discogs_id(discogs_id)
            if record and record.pk != exclude_pk:
                self._update_missing_identifiers(record, barcode, catalog_number)
                return record

        if barcode:
            record = Record.objects.find_by_barcode(barcode)
            if record and record.pk != exclude_pk:
                self._update_missing_identifiers(record, barcode, catalog_number)
                return record

        if catalog_number:
            record = Record.objects.find_by_catalog_number(catalog_number)
            if record and record.pk != exclude_pk:
                self._update_missing_identifiers(record, barcode, catalog_number)
                return record

        # Дополнительная проверка через Discogs API
        if barcode or catalog_number:
            try:
                if barcode:
                    release = self.discogs_service.search_by_barcode(barcode)
                elif catalog_number:
                    release = self.discogs_service.search_by_catalog_number(
                        catalog_number
                    )

                if release:
                    record = Record.objects.find_by_discogs_id(release.id)
                    if record and record.pk != exclude_pk:
                        self._update_missing_identifiers(
                            record, barcode, catalog_number
                        )
                        return record
            except Exception as e:
                logger.debug(f"Failed to check discogs duplicate: {e}")

        return None

    def update_stock(
        self, record: Record, quantity: int, operation: str = "set"
    ) -> Record:
        """Обновление остатков записи.

        Поддерживает операции установки, добавления и вычитания остатков.

        Args:
            record: Запись для обновления остатков.
            quantity: Количество для операции.
            operation: Тип операции ('set', 'add', 'subtract').

        Returns:
            Обновлённая запись.

        Raises:
            ValueError: При недостаточном количестве для вычитания
                       или неизвестной операции.
        """
        if operation == "set":
            record.stock = quantity
        elif operation == "add":
            record.stock += quantity
        elif operation == "subtract":
            if record.stock < quantity:
                raise ValueError(f"Insufficient stock. Available: {record.stock}")
            record.stock -= quantity
        else:
            raise ValueError(f"Unknown operation: {operation}")

        record.save()
        logger.info(f"Stock updated for record {record.id}: {operation} {quantity}")
        return record

    # === Приватные методы ===

    def _find_existing_record(
        self, barcode: Optional[str], catalog_number: Optional[str]
    ) -> Optional[Record]:
        """Поиск существующей записи по идентификаторам.

        Args:
            barcode: Штрих-код для поиска.
            catalog_number: Каталожный номер для поиска.

        Returns:
            Найденная запись или None.
        """
        if barcode:
            if record := Record.objects.find_by_barcode(barcode):
                return record

        if catalog_number:
            if record := Record.objects.find_by_catalog_number(catalog_number):
                return record

        return None

    def _create_record_from_discogs(
        self,
        discogs_release,
        search_barcode: Optional[str] = None,
        search_catalog_number: Optional[str] = None,
    ) -> Record:
        """Создание записи из данных Discogs.

        Args:
            discogs_release: Объект релиза из Discogs API.
            search_barcode: Штрих-код, использованный для поиска.
            search_catalog_number: Каталожный номер, использованный для поиска.

        Returns:
            Созданная запись.
        """
        # Извлекаем данные
        record_data = self.discogs_service.extract_release_data(discogs_release)

        # Приоритет у идентификаторов из поиска
        if search_barcode:
            record_data["barcode"] = search_barcode
        if search_catalog_number:
            record_data["catalog_number"] = search_catalog_number

        logger.info(
            f"Creating record - "
            f"Discogs ID: {record_data['discogs_id']}, "
            f"Barcode: {record_data.get('barcode', 'None')}, "
            f"Catalog: {record_data.get('catalog_number', 'None')}"
        )

        # Создаём основную запись
        record = Record.objects.create(
            title=record_data["title"],
            discogs_id=record_data["discogs_id"],
            release_year=record_data.get("year"),
            catalog_number=record_data.get("catalog_number"),
            barcode=record_data.get("barcode"),
            country=record_data.get("country"),
            notes=record_data.get("notes"),
            condition=RecordConditions.M,
            stock=1,
        )

        # Создаём связи
        self._create_record_relations(record, discogs_release)

        # Создаём треки
        self._create_tracks(record, discogs_release)

        return record

    @staticmethod
    def _is_empty_identifier(value: Optional[str]) -> bool:
        """Проверка, является ли идентификатор пустым.

        В Django пустые CharField сохраняются как '', а не None.

        Args:
            value: Значение для проверки.

        Returns:
            True если значение пустое (None, пустая строка или только пробелы).
        """
        return value is None or (isinstance(value, str) and value.strip() == "")

    def _update_missing_identifiers(
        self,
        record: Record,
        barcode: Optional[str] = None,
        catalog_number: Optional[str] = None,
    ):
        """Обновляет недостающие идентификаторы в существующей записи.

        Args:
            record: Запись для обновления.
            barcode: Штрих-код для добавления.
            catalog_number: Каталожный номер для добавления.
        """
        updated = False

        if barcode and self._is_empty_identifier(record.barcode):
            record.barcode = barcode
            updated = True
            logger.info(f"Updated missing barcode for record {record.id}: {barcode}")

        if catalog_number and self._is_empty_identifier(record.catalog_number):
            record.catalog_number = catalog_number
            updated = True
            logger.info(
                f"Updated missing catalog_number for record {record.id}: {catalog_number}"
            )

        if updated:
            record.save()

    def _update_record_fields(self, record: Record, discogs_release):
        """Обновление основных полей записи.

        Обновляет:
        - title (всегда)
        - release_year (всегда)
        - country (всегда)
        - notes (всегда)
        - catalog_number (только если пустой)
        - barcode (только если пустой)

        Args:
            record: Запись для обновления.
            discogs_release: Объект релиза из Discogs API.
        """
        record_data = self.discogs_service.extract_release_data(discogs_release)

        # Сохраняем старые значения для логирования
        old_values = {
            "title": record.title,
            "year": record.release_year,
            "country": record.country,
            "catalog_number": record.catalog_number,
            "barcode": record.barcode,
        }

        # Обновляем поля
        record.title = record_data["title"]
        record.release_year = record_data.get("year")
        record.country = record_data.get("country")
        record.notes = record_data.get("notes")

        # Обновляем идентификаторы если они пустые
        if self._is_empty_identifier(record.catalog_number) and record_data.get(
            "catalog_number"
        ):
            record.catalog_number = record_data["catalog_number"]
            logger.info(f"Added missing catalog_number: {record.catalog_number}")

        if self._is_empty_identifier(record.barcode) and record_data.get("barcode"):
            record.barcode = record_data["barcode"]
            logger.info(f"Added missing barcode: {record.barcode}")

        # Логируем изменения
        changes = []
        for field, old_value in old_values.items():
            new_value = getattr(record, field if field != "year" else "release_year")
            if old_value != new_value:
                changes.append(f"{field}: '{old_value}' → '{new_value}'")

        if changes:
            logger.info(f"Updated fields for record {record.id}: {', '.join(changes)}")

        record.save()

    def _create_record_relations(self, record: Record, discogs_release):
        """Создание связей записи (артисты, лейбл, жанры и т.д.).

        Args:
            record: Запись для установки связей.
            discogs_release: Объект релиза из Discogs API.
        """
        # Артисты
        artists = []
        for artist_data in discogs_release.artists:
            artist = self._get_or_create_artist(artist_data)
            artists.append(artist)
        record.artists.set(artists)

        # Лейбл
        if discogs_release.labels:
            label = self._get_or_create_label(discogs_release.labels[0])
            record.label = label
            record.save()

        # Жанры
        genres = []
        for genre_name in getattr(discogs_release, "genres", []):
            genre = self._get_or_create_genre(genre_name)
            genres.append(genre)
        record.genres.set(genres)

        # Стили
        styles = []
        for style_name in getattr(discogs_release, "styles", []):
            style = self._get_or_create_style(style_name)
            styles.append(style)
        record.styles.set(styles)

        # Форматы
        formats = self._create_formats(getattr(discogs_release, "formats", []))
        record.formats.set(formats)

    def _update_record_relations(self, record: Record, discogs_release):
        """Обновление связей записи.

        ПОЛНОСТЬЮ ЗАМЕНЯЕТ:
        - Артистов
        - Жанры
        - Стили
        - Форматы
        - Лейбл

        Args:
            record: Запись для обновления связей.
            discogs_release: Объект релиза из Discogs API.
        """
        logger.info(f"Updating relations for record {record.id}")

        # Сохраняем старые значения для логирования
        old_artists = list(record.artists.values_list("name", flat=True))
        old_genres = list(record.genres.values_list("name", flat=True))
        old_styles = list(record.styles.values_list("name", flat=True))
        old_formats = list(record.formats.values_list("name", flat=True))
        old_label = record.label.name if record.label else None

        # Обновляем все связи
        self._create_record_relations(record, discogs_release)

        # Логируем изменения
        new_artists = list(record.artists.values_list("name", flat=True))
        new_genres = list(record.genres.values_list("name", flat=True))
        new_styles = list(record.styles.values_list("name", flat=True))
        new_formats = list(record.formats.values_list("name", flat=True))
        new_label = record.label.name if record.label else None

        if old_artists != new_artists:
            logger.info(f"Artists updated: {old_artists} → {new_artists}")
        if old_genres != new_genres:
            logger.info(f"Genres updated: {old_genres} → {new_genres}")
        if old_styles != new_styles:
            logger.info(f"Styles updated: {old_styles} → {new_styles}")
        if old_formats != new_formats:
            logger.info(f"Formats updated: {old_formats} → {new_formats}")
        if old_label != new_label:
            logger.info(f"Label updated: '{old_label}' → '{new_label}'")

    def _get_or_create_artist(self, artist_data) -> Artist:
        """Получение или создание артиста.

        Args:
            artist_data: Данные артиста из Discogs API.

        Returns:
            Экземпляр модели Artist.
        """
        artist = Artist.objects.find_by_discogs_id(artist_data.id)
        if not artist:
            artist = Artist.objects.create(
                discogs_id=artist_data.id, name=artist_data.name
            )
        return artist

    def _get_or_create_label(self, label_data) -> Label:
        """Получение или создание лейбла.

        Args:
            label_data: Данные лейбла из Discogs API.

        Returns:
            Экземпляр модели Label.
        """
        label = Label.objects.find_by_discogs_id(label_data.id)
        if not label:
            label = Label.objects.create(
                discogs_id=label_data.id,
                name=label_data.name,
                description=f"Discogs ID: {label_data.id}",
            )
        return label

    def _get_or_create_genre(self, genre_name: str) -> Genre:
        """Получение или создание жанра.

        Args:
            genre_name: Название жанра.

        Returns:
            Экземпляр модели Genre.
        """
        genre = Genre.objects.find_by_name(genre_name)
        if not genre:
            genre = Genre.objects.create(name=genre_name)
        return genre

    def _get_or_create_style(self, style_name: str) -> Style:
        """Получение или создание стиля.

        Args:
            style_name: Название стиля.

        Returns:
            Экземпляр модели Style.
        """
        style = Style.objects.find_by_name(style_name)
        if not style:
            style = Style.objects.create(name=style_name)
        return style

    def _create_formats(self, formats_data) -> List[Format]:
        """Создание форматов записи.

        Обрабатывает специальные случаи для LP и других форматов.

        Args:
            formats_data: Данные о форматах из Discogs API.

        Returns:
            Список экземпляров модели Format.
        """
        if not formats_data:
            return []

        formats = []
        for format_info in formats_data:
            qty = int(format_info.get("qty", 1))
            descriptions = [d.upper() for d in format_info.get("descriptions", [])]

            # Специальная обработка для LP
            if "LP" in descriptions:
                format_name = f"{qty}LP" if qty > 1 else "LP"
                fmt = Format.objects.find_by_name(format_name)
                if not fmt:
                    fmt = Format.objects.create(name=format_name)
                formats.append(fmt)

            # Остальные форматы
            for desc in descriptions:
                if desc not in ["LP", "2LP", "3LP", "4LP", "5LP", "6LP"]:
                    fmt = Format.objects.find_by_name(desc)
                    if not fmt:
                        fmt = Format.objects.create(name=desc)
                    formats.append(fmt)

        return formats

    def _create_tracks(self, record: Record, discogs_release):
        """Создание треков для записи.

        Создаёт треки и пытается найти соответствующие видео на YouTube.

        Args:
            record: Запись для добавления треков.
            discogs_release: Объект релиза из Discogs API.
        """
        # Получаем видео если есть
        videos = self.discogs_service.get_release_videos(record.discogs_id) or []

        # Получаем список всех Исполнителей
        artists = [artist.name for artist in record.artists.all()]

        with transaction.atomic():
            for track in getattr(discogs_release, "tracklist", []):
                # Ищем видео для трека
                track_url = None

                # Пытаемся найти видео для этого трека
                for video in videos:
                    # Простая проверка по названию трека
                    if track.title.lower() in video["title"].lower():
                        track_url = video["url"]
                        break

                track_instance = Track.objects.create(
                    record=record,
                    position=track.position,
                    title=track.title,
                    duration=track.duration,
                    youtube_url=track_url,
                )

                # Создаем частично примененную функцию с текущими значениями
                task = partial(
                    dl_track.delay,
                    record_id=record.id,
                    track_id=track_instance.id,
                    url=track_url,
                    expected_artists=artists,
                )
                transaction.on_commit(task)

    def _update_tracks(self, record: Record, discogs_release):
        """Обновление треков записи.

        ПОЛНОСТЬЮ УДАЛЯЕТ старые треки и создаёт новые.
        Также пытается найти YouTube видео для каждого трека.

        Args:
            record: Запись для обновления треков.
            discogs_release: Объект релиза из Discogs API.
        """
        old_tracks_count = record.tracks.count()

        # Удаляем старые треки
        record.tracks.all().delete()
        # TODO: Написать функцию удаления mp3 с хранилища
        logger.info(f"Deleted {old_tracks_count} old tracks for record {record.id}")

        # Создаём новые
        self._create_tracks(record, discogs_release)

        new_tracks_count = record.tracks.count()
        logger.info(f"Created {new_tracks_count} new tracks for record {record.id}")
