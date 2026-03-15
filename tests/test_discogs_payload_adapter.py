from __future__ import annotations

from records.services.provider_payload_adapter import adapt_discogs_release


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
        self.formats = [{"qty": "1", "descriptions": ["LP", "Album"]}]
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
    assert payload["formats"] == ["LP", "ALBUM"]


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
