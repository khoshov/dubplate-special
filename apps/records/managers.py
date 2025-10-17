# apps/records/managers.py
"""
Кастомные QuerySet/Manager для моделей записей.
Здесь же централизуем, что треки всегда подгружаются в порядке числового индекса.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from apps.core.managers import BaseManager, BaseQuerySet
from django.db.models import Count, F, Prefetch, Q

if TYPE_CHECKING:
    from .models import Artist, Format, Genre, Label, Record, Style


class RecordQuerySet(BaseQuerySet):
    """QuerySet для Record с методами фильтрации и оптимизации."""

    # === Фильтрация ===
    def available(self):
        """Записи в наличии."""
        return self.filter(stock__gt=0)

    def out_of_stock(self):
        """Записи не в наличии."""
        return self.filter(stock=0)

    def by_barcode(self, barcode: str):
        """Поиск по штрих-коду."""
        return self.filter(barcode=barcode)

    def by_catalog_number(self, catalog_number: str):
        """Поиск по каталожному номеру."""
        return self.filter(catalog_number=catalog_number)

    def by_discogs_id(self, discogs_id: int):
        """Поиск по Discogs ID."""
        return self.filter(discogs_id=discogs_id)

    def by_identifier(self, identifier: str):
        """Поиск по любому идентификатору."""
        return self.filter(
            Q(barcode=identifier)
            | Q(catalog_number=identifier)
            | Q(discogs_id=identifier)
        )

    def by_artist(self, artist_name: str):
        """Фильтр по артисту."""
        return self.filter(artists__name__icontains=artist_name)

    def by_genre(self, genre_name: str):
        """Фильтр по жанру."""
        return self.filter(genres__name__icontains=genre_name)

    def by_year_range(self, start_year: int, end_year: int):
        """Фильтр по диапазону годов."""
        return self.filter(release_year__range=(start_year, end_year))

    # === Оптимизация запросов ===
    def with_related(self):
        """
        Загрузка связанных объектов + треки в корректном порядке:
        сначала по числовому индексу (position_index), затем по текстовой позиции (для стабильности),
        затем по id на случай одинаковых значений.
        """
        from .models import Track  # локальный импорт, чтобы избежать циклов

        tracks_qs = Track.objects.order_by("position_index", "position", "id")
        return self.select_related("label").prefetch_related(
            "artists",
            "genres",
            "styles",
            "formats",
            Prefetch("tracks", queryset=tracks_qs),
        )

    def with_tracks(self):
        """Только треки в правильном порядке (см. выше)."""
        from .models import Track

        tracks_qs = Track.objects.order_by("position_index", "position", "id")
        return self.prefetch_related(Prefetch("tracks", queryset=tracks_qs))

    def with_stats(self):
        """С подсчётом статистики по связанным таблицам."""
        return self.annotate(
            tracks_count=Count("tracks"),
            artists_count=Count("artists", distinct=True),
            total_value=F("stock") * F("price"),
        )


class RecordManager(BaseManager):
    """Manager для Record."""

    def get_queryset(self):
        return RecordQuerySet(self.model, using=self._db)

    # === Простые методы поиска ===
    def find_by_barcode(self, barcode: str) -> Optional["Record"]:
        return self.filter(barcode=barcode).first()

    def find_by_catalog_number(self, catalog_number: str) -> Optional["Record"]:
        return self.filter(catalog_number=catalog_number).first()

    def find_by_discogs_id(self, discogs_id: int) -> Optional["Record"]:
        return self.filter(discogs_id=discogs_id).first()

    # === Chainable методы ===
    def available(self):
        return self.get_queryset().available()

    def with_related(self):
        return self.get_queryset().with_related()


class ArtistManager(BaseManager):
    """Manager для Artist."""

    def find_by_discogs_id(self, discogs_id: int) -> Optional["Artist"]:
        return self.filter(discogs_id=discogs_id).first()

    def find_by_name(self, name: str) -> Optional["Artist"]:
        return self.filter(name=name).first()

    def with_records_count(self):
        return self.annotate(records_count=Count("records"))


class LabelManager(BaseManager):
    """Manager для Label."""

    def find_by_discogs_id(self, discogs_id: int) -> Optional["Label"]:
        return self.filter(discogs_id=discogs_id).first()

    def find_by_name(self, name: str) -> Optional["Label"]:
        return self.filter(name=name).first()


class GenreManager(BaseManager):
    """Manager для Genre."""

    def find_by_name(self, name: str) -> Optional["Genre"]:
        return self.filter(name=name).first()


class StyleManager(BaseManager):
    """Manager для Style."""

    def find_by_name(self, name: str) -> Optional["Style"]:
        return self.filter(name=name).first()


class FormatManager(BaseManager):
    """Manager для Format."""

    def find_by_name(self, name: str) -> Optional["Format"]:
        return self.filter(name=name).first()
