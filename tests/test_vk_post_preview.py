from __future__ import annotations

import pytest

from records.models import (
    Artist,
    AvailableChoices,
    Format,
    Genre,
    Label,
    Record,
    StructuredFormat,
    Style,
)
from records.services.social.vk_service import compose_record_text


@pytest.mark.django_db
def test_vk_post_preview_structured_formats() -> None:
    artist = Artist.objects.create(name="Nirvana")
    label = Label.objects.create(name="Sub Pop")
    genre, _ = Genre.objects.get_or_create(name="Rock")
    style, _ = Style.objects.get_or_create(name="Grunge")

    record = Record.objects.create(
        title="Bleach",
        label=label,
        catalog_number="SP34",
        release_year=1989,
        release_month=6,
        release_day=15,
        price=2490,
        condition="SS",
        availability_status=AvailableChoices.IN_STOCK,
        active_structured_format_variant=2,
    )
    record.artists.add(artist)
    record.genres.add(genre)
    record.styles.add(style)

    StructuredFormat.objects.create(
        record=record,
        variant_of_format=1,
        carrier="Vinyl",
        quantity=2,
        format_name='12"',
        details="Album, Reissue, Remastered",
    )
    StructuredFormat.objects.create(
        record=record,
        variant_of_format=2,
        carrier="CD",
        quantity=1,
        format_name="Album",
        details="Promo",
    )

    text = compose_record_text(record)

    print("\n=== VK POST PREVIEW: STRUCTURED FORMATS ===")
    print(text)
    print("=== END PREVIEW ===\n")

    assert "Nirvana — Bleach" in text
    assert "Label: Sub Pop – SP34" in text
    assert "Format: CD Album (Promo)" in text
    assert '2x12" Vinyl' not in text
    assert "Release Date: Jun 15, 1989" in text


@pytest.mark.django_db
def test_vk_post_preview_legacy_format_fallback() -> None:
    artist = Artist.objects.create(name="Unknown Artist")
    label = Label.objects.create(name="Test Label")
    genre, _ = Genre.objects.get_or_create(name="Breakbeat")
    style, _ = Style.objects.get_or_create(name="Hardcore Breakbeat")
    format_obj, _ = Format.objects.get_or_create(name='12" Vinyl')

    record = Record.objects.create(
        title="Legacy Release",
        label=label,
        catalog_number="TEST-12",
        release_year=1993,
        price=1990,
        condition="SS",
        availability_status=AvailableChoices.PREORDER,
    )
    record.artists.add(artist)
    record.genres.add(genre)
    record.styles.add(style)
    record.formats.add(format_obj)

    text = compose_record_text(record)

    print("\n=== VK POST PREVIEW: LEGACY FALLBACK ===")
    print(text)
    print("=== END PREVIEW ===\n")

    assert "Unknown Artist — Legacy Release" in text
    assert "Label: Test Label – TEST-12" in text
    assert 'Format: 12" Vinyl' in text
    assert "Release Date: Dec 31, 1993" in text


@pytest.mark.django_db
def test_vk_post_preview_gorillaz_the_mountain_real_case() -> None:
    """
    Preview-кейс по релизу Discogs 36594097.

    Допущение:
    - прямой Discogs payload в тест не подгружается;
    - title / label / catno / genre-style / format собраны по публичным страницам,
      использующим Discogs metadata;
    - дата 2026-02-27 взята из публичных релизных анонсов The Mountain.
    """
    artist = Artist.objects.create(name="Gorillaz")
    label = Label.objects.create(name="Kong")
    genres = [
        Genre.objects.get_or_create(name="Electronic")[0],
        Genre.objects.get_or_create(name="Hip Hop")[0],
        Genre.objects.get_or_create(name="Pop")[0],
        Genre.objects.get_or_create(name="Folk, World, & Country")[0],
    ]
    styles = [
        Style.objects.get_or_create(name="Latin Pop")[0],
        Style.objects.get_or_create(name="Indie Pop")[0],
        Style.objects.get_or_create(name="Indo-Pop")[0],
        Style.objects.get_or_create(name="Alt-Pop")[0],
    ]

    record = Record.objects.create(
        title="पर्वत (The Mountain)",
        label=label,
        catalog_number="KONG001LPI",
        release_year=2026,
        release_month=2,
        release_day=27,
        price=4400,
        condition="SS",
        availability_status=AvailableChoices.IN_STOCK,
    )
    record.artists.add(artist)
    record.genres.add(*genres)
    record.styles.add(*styles)

    StructuredFormat.objects.create(
        record=record,
        variant_of_format=1,
        carrier="Vinyl",
        quantity=2,
        format_name='12"',
        details="Album, Yellow",
    )

    text = compose_record_text(record)

    print("\n=== VK POST PREVIEW: GORILLAZ THE MOUNTAIN ===")
    print(text)
    print("=== END PREVIEW ===\n")

    assert "Gorillaz — पर्वत (The Mountain)" in text
    assert "Label: Kong – KONG001LPI" in text
    assert 'Format: 2x12" Vinyl (Album, Yellow)' in text
    assert "Release Date: Feb 27, 2026" in text
