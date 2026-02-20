from __future__ import annotations

import pytest

from records.models import (
    Artist,
    AvailableChoices,
    Genre,
    Record,
    RecordConditions,
    Style,
)
from records.services.record_assembly import attach_relations
from records.services.social.vk_service import compose_record_text


@pytest.mark.django_db
def test_compose_record_text_hides_condition_for_preorder() -> None:
    artist = Artist.objects.create(name="Test Artist")
    genre, _ = Genre.objects.get_or_create(name="Jungle")
    style, _ = Style.objects.get_or_create(name="Not specified")
    record = Record.objects.create(
        title="Test Release",
        price=1000,
        condition="SS",
        availability_status=AvailableChoices.PREORDER,
    )
    record.artists.add(artist)
    record.genres.add(genre)
    record.styles.add(style)

    text = compose_record_text(record)
    first_line = text.splitlines()[0]

    assert "НОВАЯ" not in first_line
    assert "ПРЕДЗАКАЗ" in first_line
    assert "#ds_jungle" in text
    assert "notspecified" not in text.lower()


@pytest.mark.django_db
def test_compose_record_text_shows_condition_for_in_stock() -> None:
    record = Record.objects.create(
        title="Stock Release",
        price=1200,
        condition="SS",
        availability_status=AvailableChoices.IN_STOCK,
    )

    text = compose_record_text(record)
    first_line = text.splitlines()[0]

    assert "НОВАЯ" in first_line
    assert "В НАЛИЧИИ" in first_line


@pytest.mark.django_db
def test_attach_relations_canonizes_style_and_preserves_regular_genre() -> None:
    record = Record.objects.create(title="Canon Record")

    attach_relations(
        record,
        {
            "styles": ["Не указан"],
            "genres": ["Breakbeat"],
        },
    )

    style_names = set(record.styles.values_list("name", flat=True))
    genre_names = set(record.genres.values_list("name", flat=True))

    assert style_names == {"Not specified"}
    assert genre_names == {"Breakbeat"}


@pytest.mark.django_db
def test_not_specified_str_is_localized_for_admin_display() -> None:
    genre, _ = Genre.objects.get_or_create(name="Not specified")
    style, _ = Style.objects.get_or_create(name="Not specified")

    assert str(genre) == "Не указан"
    assert str(style) == "Не указан"


def test_condition_not_specified_choice_is_last() -> None:
    assert RecordConditions.CONDITION_CHOICES[-1] == (
        RecordConditions.NOT_SPECIFIED,
        "Не указано",
    )
