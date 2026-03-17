from __future__ import annotations

from records.services.provider_payload_adapter import (
    adapt_discogs_payload,
    adapt_discogs_release,
)


class DummyArtist:
    def __init__(self, name: str):
        self.name = name


class DummyLabel:
    def __init__(self, name: str, catno: str | None = None):
        self.name = name
        self.catno = catno


class DummyIdentifier:
    def __init__(self, ident_type: str, value: str):
        self.type = ident_type
        self.value = value


class DummyTrack:
    def __init__(self, title: str, position: str = "", duration: str | None = None):
        self.title = title
        self.position = position
        self.duration = duration


class DummyRelease:
    def __init__(
        self,
        *,
        release_id: int | None,
        year: int | None,
        released: str | None,
        data_id: int | str | None = None,
        labels: list[DummyLabel] | None = None,
        identifiers: list[object] | None = None,
    ):
        self.id = release_id
        self.title = "Dummy Release"
        self.country = "UK"
        self.notes = "notes"
        self.year = year
        self.genres = ["Electronic"]
        self.styles = ["Drum n Bass"]
        self.formats = [{"name": "Vinyl", "qty": "1", "descriptions": ["LP", "Album"]}]
        self.tracklist = [DummyTrack("Track A", "A1", "03:00")]
        self.artists = [DummyArtist("Artist A")]
        self.labels = labels if labels is not None else [DummyLabel("Label A")]
        self.identifiers = identifiers if identifiers is not None else []
        self.data = {}
        if released is not None:
            self.data["released"] = released
        if data_id is not None:
            self.data["id"] = data_id


def test_adapt_discogs_release_extracts_discogs_id_and_full_release_date():
    release = DummyRelease(release_id=35401393, year=2025, released="2025-09-26")

    payload = adapt_discogs_release(release)

    assert payload["discogs_id"] == 35401393
    assert payload["release_year"] == 2025
    assert payload["release_month"] == 9
    assert payload["release_day"] == 26
    assert payload["formats"] == []
    assert payload["structured_formats"] == [
        {
            "variant_of_format": 1,
            "carrier": "Vinyl",
            "quantity": 1,
            "format_name": '12"',
            "details": "Album",
        }
    ]


def test_adapt_discogs_release_uses_data_id_and_handles_unknown_month_day():
    release = DummyRelease(
        release_id=None,
        year=2025,
        released="2025-00-00",
        data_id="35207368",
    )

    payload = adapt_discogs_release(release)

    assert payload["discogs_id"] == 35207368
    assert payload["release_year"] == 2025
    assert payload["release_month"] is None
    assert payload["release_day"] is None


def test_adapt_discogs_release_handles_year_and_month_without_day():
    release = DummyRelease(release_id=35207368, year=2025, released="2025-11")

    payload = adapt_discogs_release(release)

    assert payload["discogs_id"] == 35207368
    assert payload["release_year"] == 2025
    assert payload["release_month"] == 11
    assert payload["release_day"] is None


def test_adapt_discogs_release_falls_back_to_year_when_released_missing():
    release = DummyRelease(release_id=1001, year=1999, released=None)

    payload = adapt_discogs_release(release)

    assert payload["discogs_id"] == 1001
    assert payload["release_year"] == 1999
    assert payload["release_month"] is None
    assert payload["release_day"] is None


def test_adapt_discogs_release_reads_object_identifiers_and_label_catno():
    release = DummyRelease(
        release_id=36313477,
        year=2026,
        released="2026-02-06",
        labels=[DummyLabel("Parlophone", "1635679")],
        identifiers=[
            DummyIdentifier("Barcode", "5 021732 635679"),
            DummyIdentifier("Barcode", "5021732635679"),
        ],
    )

    payload = adapt_discogs_release(release)

    assert payload["barcode"] == "5021732635679"
    assert payload["catalog_number"] == "1635679"


def test_adapt_discogs_release_ignores_none_like_catalog_number_from_identifiers():
    release = DummyRelease(
        release_id=36313477,
        year=2026,
        released="2026-02-06",
        identifiers=[DummyIdentifier("Catalog Number", " none ")],
    )

    payload = adapt_discogs_release(release)

    assert payload["catalog_number"] is None


def test_adapt_discogs_release_defaults_quantity_to_one_when_missing():
    release = DummyRelease(release_id=2001, year=1999, released="1999-01-01")
    release.formats = [{"name": "Vinyl", "descriptions": ["LP", "Album"]}]

    payload = adapt_discogs_release(release)

    assert payload["structured_formats"][0]["quantity"] == 1


def test_adapt_discogs_release_keeps_row_when_descriptions_missing():
    release = DummyRelease(release_id=2002, year=1999, released="1999-01-01")
    release.formats = [{"name": "Cassette", "qty": "1", "descriptions": []}]

    payload = adapt_discogs_release(release)

    assert payload["structured_formats"] == [
        {
            "variant_of_format": 1,
            "carrier": "Cassette",
            "quantity": 1,
            "format_name": "",
            "details": "",
        }
    ]


def test_adapt_discogs_release_preserves_rare_carrier_values_and_discogs_text():
    release = DummyRelease(release_id=2003, year=1999, released="1999-01-01")
    release.formats = [
        {
            "name": "Lathe Cut",
            "qty": "1",
            "descriptions": ['7"', "Single Sided"],
            "text": "Hand-numbered",
        }
    ]

    payload = adapt_discogs_release(release)

    assert payload["structured_formats"] == [
        {
            "variant_of_format": 1,
            "carrier": "Lathe Cut",
            "quantity": 1,
            "format_name": '7"',
            "details": "Single Sided, Hand-numbered",
        }
    ]


def test_adapt_discogs_release_strips_disambiguation_suffixes_from_artist_and_label():
    release = DummyRelease(
        release_id=36594097,
        year=2025,
        released="2025-09-26",
        labels=[DummyLabel("Kong (7)", "KONG001LPI")],
    )
    release.artists = [DummyArtist("Jerome (24)")]

    payload = adapt_discogs_release(release)

    assert payload["artists"] == ["Jerome"]
    assert payload["label"] == "Kong"


def test_adapt_discogs_payload_strips_disambiguation_suffixes_from_artist_and_label():
    payload = adapt_discogs_payload(
        {
            "title": "Test",
            "artists": ["Jerome (24)"],
            "label": "Kong (7)",
            "tracks": [],
        }
    )

    assert payload["artists"] == ["Jerome"]
    assert payload["label"] == "Kong"
