"""
Кастомные QuerySet и Manager классы для моделей приложения `records`.

Здесь централизуется логика выборок и оптимизации запросов:
- фильтрация по полям (barcode, catalog_number, artist и т.п.);
- аннотации со статистикой;
- предзагрузка связанных данных (artists, genres, tracks и т.д.);
- гарантированный порядок треков (по числовому индексу).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from django.db.models import Count, F, Prefetch, Q
from apps.core.managers import BaseManager, BaseQuerySet

if TYPE_CHECKING:
    from .models import Artist, Format, Genre, Label, Record, Style


class RecordQuerySet(BaseQuerySet):
    """Расширенный QuerySet для модели Record."""

    def available(self):
        """Возвращает записи, находящиеся в наличии."""
        return self.filter(stock__gt=0)

    def out_of_stock(self):
        """Возвращает записи, отсутствующие в наличии."""
        return self.filter(stock=0)

    def by_barcode(self, barcode: str):
        """Фильтрует записи по штрих-коду."""
        return self.filter(barcode=barcode)

    def by_catalog_number(self, catalog_number: str):
        """Фильтрует записи по каталожному номеру."""
        return self.filter(catalog_number=catalog_number)

    def by_discogs_id(self, discogs_id: int):
        """Фильтрует записи по Discogs-ID."""
        return self.filter(discogs_id=discogs_id)

    def by_identifier(self, identifier: str):
        """
        Выполняет универсальный поиск по любому идентификатору:
        barcode / catalog_number / discogs_id.
        """
        return self.filter(
            Q(barcode=identifier)
            | Q(catalog_number=identifier)
            | Q(discogs_id=identifier)
        )

    def by_artist(self, artist_name: str):
        """Фильтрует записи по имени артиста."""
        return self.filter(artists__name__icontains=artist_name)

    def by_genre(self, genre_name: str):
        """Фильтрует записи по названию жанра."""
        return self.filter(genres__name__icontains=genre_name)

    def by_year_range(self, start_year: int, end_year: int):
        """Фильтрует записи по диапазону годов релиза."""
        return self.filter(release_year__range=(start_year, end_year))

    def with_related(self):
        """
        Выполняет предзагрузку связанных объектов:
        label, artists, genres, styles, formats и треков в корректном порядке.
        """
        from .models import Track  # локальный импорт во избежание циклов

        tracks_qs = Track.objects.order_by("position_index", "position", "id")
        return self.select_related("label").prefetch_related(
            "artists",
            "genres",
            "styles",
            "formats",
            Prefetch("tracks", queryset=tracks_qs),
        )

    def with_tracks(self):
        """Добавляет предзагрузку треков в порядке (position_index, position, id)."""
        from .models import Track

        tracks_qs = Track.objects.order_by("position_index", "position", "id")
        return self.prefetch_related(Prefetch("tracks", queryset=tracks_qs))

    def with_stats(self):
        """Добавляет статистические поля (tracks_count, artists_count, total_value)."""
        return self.annotate(
            tracks_count=Count("tracks"),
            artists_count=Count("artists", distinct=True),
            total_value=F("stock") * F("price"),
        )


class RecordManager(BaseManager):
    """Менеджер для модели Record."""

    def get_queryset(self) -> RecordQuerySet:
        return RecordQuerySet(self.model, using=self._db)

    def find_by_barcode(self, barcode: str) -> Optional["Record"]:
        """Возвращает запись по штрих-коду (или None)."""
        return self.filter(barcode=barcode).first()

    def find_by_catalog_number(self, catalog_number: str) -> Optional["Record"]:
        """Возвращает запись по каталожному номеру (или None)."""
        return self.filter(catalog_number=catalog_number).first()

    def find_by_discogs_id(self, discogs_id: int) -> Optional["Record"]:
        """Возвращает запись по Discogs-ID (или None)."""
        return self.filter(discogs_id=discogs_id).first()

    def available(self):
        """Возвращает записи, находящиеся в наличии."""
        return self.get_queryset().available()

    def with_related(self):
        """Возвращает queryset с предзагруженными зависимостями."""
        return self.get_queryset().with_related()


class ArtistManager(BaseManager):
    """Менеджер для модели Artist."""

    def find_by_discogs_id(self, discogs_id: int) -> Optional["Artist"]:
        return self.filter(discogs_id=discogs_id).first()

    def find_by_name(self, name: str) -> Optional["Artist"]:
        return self.filter(name=name).first()

    def with_records_count(self):
        """Добавляет поле records_count — количество записей у артиста."""
        return self.annotate(records_count=Count("records"))


class LabelManager(BaseManager):
    """Менеджер для модели Label."""

    def find_by_discogs_id(self, discogs_id: int) -> Optional["Label"]:
        return self.filter(discogs_id=discogs_id).first()

    def find_by_name(self, name: str) -> Optional["Label"]:
        return self.filter(name=name).first()


class GenreManager(BaseManager):
    """Менеджер для модели Genre."""

    def find_by_name(self, name: str) -> Optional["Genre"]:
        return self.filter(name=name).first()


class StyleManager(BaseManager):
    """Менеджер для модели Style."""

    def find_by_name(self, name: str) -> Optional["Style"]:
        return self.filter(name=name).first()


class FormatManager(BaseManager):
    """Менеджер для модели Format."""

    def find_by_name(self, name: str) -> Optional["Format"]:
        return self.filter(name=name).first()
