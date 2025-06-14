from typing import List, Type, TypeVar

from django.db import models

from records.models import Artist, Format, Genre, Label, Style

T = TypeVar("T", bound=models.Model)


class DiscogsModelFactory:
    """Фабрика для создания/обновления моделей Django.

    Методы:
        create_or_update_model: Базовый метод для всех моделей
        create_or_update_artist: Специализированный для артистов
        create_or_update_genre: Для жанров
        create_or_update_style: Для стилей
        create_or_update_label: Для лейблов
        create_or_update_formats: Для форматов релизов
    """

    @staticmethod
    def create_or_update_model(
        model_class: Type[T], discogs_id: int = None, **defaults
    ) -> T:
        """
        Общий метод для создания/обновления моделей.

        Args:
            model_class: Класс модели Django
            discogs_id: ID в Discogs (опционально)
            defaults: Значения по умолчанию для создания

        Returns:
            Экземпляр указанного класса модели
        """
        if discogs_id is not None:
            return model_class.objects.get_or_create(
                discogs_id=discogs_id, defaults=defaults
            )[0]
        return model_class.objects.get_or_create(
            name=defaults["name"], defaults=defaults
        )[0]

    def create_or_update_artist(self, artist_data) -> Artist:
        """Создает или обновляет артиста."""
        return self.create_or_update_model(
            Artist, discogs_id=artist_data.id, name=artist_data.name
        )

    def create_or_update_genre(self, genre_name: str) -> Genre:
        """Создает или обновляет жанр."""
        return self.create_or_update_model(Genre, name=genre_name)

    def create_or_update_style(self, style_name: str) -> Style:
        """Создает или обновляет стиль."""
        return self.create_or_update_model(Style, name=style_name)

    def create_or_update_label(self, label_data) -> Label:
        """Создает или обновляет лейбл."""
        return self.create_or_update_model(
            Label,
            discogs_id=label_data.id,
            name=label_data.name,
            description=f"Discogs ID: {label_data.id}",
        )

    def create_or_update_formats(self, formats_data) -> List[Format]:
        """Создает или обновляет форматы."""
        if not formats_data:
            return []

        formats = []
        for format_info in formats_data:
            qty = int(format_info.get("qty", 1))
            descriptions = [d.upper() for d in format_info.get("descriptions", [])]

            if "LP" in descriptions:
                format_name = f"{qty}LP" if qty > 1 else "LP"
                fmt = self.create_or_update_model(Format, name=format_name)
                formats.append(fmt)

            for desc in descriptions:
                if desc not in ["LP", "2LP", "3LP"]:
                    fmt = self.create_or_update_model(Format, name=desc)
                    formats.append(fmt)
        return formats
