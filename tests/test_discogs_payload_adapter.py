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
    def __init__(
        self,
        title: str,
        position: str = "",
        duration: str | None = None,
        track_type: str | None = "track",
    ):
        self.title = title
        self.position = position
        self.duration = duration
        self.type_ = track_type
        self.data = {"type_": track_type} if track_type is not None else {}


class DummyVideo:
    def __init__(self, title: str, uri: str):
        self.title = title
        self.uri = uri


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
        videos: list[DummyVideo] | None = None,
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
        self.videos = videos if videos is not None else []
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


def test_adapt_discogs_release_matches_track_video_by_title():
    release = DummyRelease(
        release_id=34690935,
        year=2025,
        released="2025-08-01",
        videos=[
            DummyVideo("Agrimony", "https://www.youtube.com/watch?v=2GAdpaJO6os"),
            DummyVideo(
                "Agrimony (part.2)",
                "https://www.youtube.com/watch?v=2h82qqsxq0o",
            ),
        ],
    )
    release.tracklist = [
        DummyTrack("Agrimony [part 1]", "A", "3:49"),
        DummyTrack("Agrimony [part 2]", "B", "3:50"),
    ]

    payload = adapt_discogs_release(release)

    assert (
        payload["tracks"][0]["youtube_url"]
        == "https://www.youtube.com/watch?v=2GAdpaJO6os"
    )
    assert (
        payload["tracks"][1]["youtube_url"]
        == "https://www.youtube.com/watch?v=2h82qqsxq0o"
    )


def test_adapt_discogs_release_leaves_track_video_empty_when_titles_do_not_match():
    release = DummyRelease(
        release_id=1000,
        year=2025,
        released="2025-08-01",
        videos=[
            DummyVideo("Completely Different", "https://www.youtube.com/watch?v=abc")
        ],
    )
    release.tracklist = [DummyTrack("Track A", "A1", "03:00")]

    payload = adapt_discogs_release(release)

    assert payload["tracks"][0]["youtube_url"] is None


def test_adapt_discogs_release_skips_headings_and_matches_video_by_position_with_duplicates():
    release = DummyRelease(
        release_id=25202965,
        year=2022,
        released="2022-11-15",
        videos=[
            DummyVideo(
                "Aladdin - Mash Up Yer Know (Aphrodite Recordings APH-69 A1) 2022",
                "https://www.youtube.com/watch?v=Sr6seAWf83A",
            ),
            DummyVideo(
                "Aladdin - Mash Up Yer Know (Aphrodite Recordings APH-69 A1) 2022",
                "https://www.youtube.com/watch?v=Sr6seAWf83A",
            ),
            DummyVideo(
                "Aladdin / DJ Aphrodite - Mash Up Yer Know (1994)",
                "https://www.youtube.com/watch?v=DW-blpeIweo",
            ),
            DummyVideo(
                "Aladdin - So Good (Aphrodite Recordings APH-69 A2) 2022",
                "https://www.youtube.com/watch?v=QrKxoTWiyZU",
            ),
            DummyVideo(
                "DJ Aphrodite / Aladdin - So Good (1994)",
                "https://www.youtube.com/watch?v=JjLd2cZ5Liw",
            ),
            DummyVideo(
                "Aladdin - We Enter (Heavenly Remix) (Aphrodite Recordings APH-69 B1) 2022",
                "https://www.youtube.com/watch?v=XoinYdLw1M4",
            ),
            DummyVideo(
                "Aladdin / DJ Aphrodite - We Enter (Heavenly Remix 1994)",
                "https://www.youtube.com/watch?v=AOnzFBHqBh4",
            ),
            DummyVideo(
                "Aladdin - Geni (Lost In Zanzibar) (Aphrodite Recordings APH-69 B2) 2022",
                "https://www.youtube.com/watch?v=0sJCUAKoTIo",
            ),
        ],
    )
    release.tracklist = [
        DummyTrack("That", track_type="heading"),
        DummyTrack("Mash Up Yer Know", "A1", None, track_type="track"),
        DummyTrack("So Good", "A2", None, track_type="track"),
        DummyTrack("This", track_type="heading"),
        DummyTrack("We Enter (Heavenly Remix)", "B1", None, track_type="track"),
        DummyTrack("Geni (Lost In Zanzibar)", "B2", None, track_type="track"),
    ]

    payload = adapt_discogs_release(release)

    tracks = payload["tracks"]
    assert [track["title"] for track in tracks] == [
        "Mash Up Yer Know",
        "So Good",
        "We Enter (Heavenly Remix)",
        "Geni (Lost In Zanzibar)",
    ]
    assert [track["position_index"] for track in tracks] == [1, 2, 3, 4]

    by_title = {track["title"]: track for track in tracks}
    assert (
        by_title["Mash Up Yer Know"]["youtube_url"]
        == "https://www.youtube.com/watch?v=Sr6seAWf83A"
    )
    assert (
        by_title["So Good"]["youtube_url"]
        == "https://www.youtube.com/watch?v=QrKxoTWiyZU"
    )
    assert (
        by_title["We Enter (Heavenly Remix)"]["youtube_url"]
        == "https://www.youtube.com/watch?v=XoinYdLw1M4"
    )
    assert (
        by_title["Geni (Lost In Zanzibar)"]["youtube_url"]
        == "https://www.youtube.com/watch?v=0sJCUAKoTIo"
    )


def test_adapt_discogs_release_matches_remastered_titles_and_skips_full_album_video():
    release = DummyRelease(
        release_id=934560,
        year=2002,
        released="2002",
        videos=[
            DummyVideo(
                "Blew (Remastered)", "https://www.youtube.com/watch?v=7E-KAP359ys"
            ),
            DummyVideo(
                "Floyd The Barber (Remastered)",
                "https://www.youtube.com/watch?v=cBm-XeOVIeY",
            ),
            DummyVideo(
                "Nirvana - Paper Cuts - RadioShack",
                "https://www.youtube.com/watch?v=R2dcjXpgIlc",
            ),
            DummyVideo(
                "Nirvana Bleach full album",
                "https://www.youtube.com/watch?v=lIOcBTKuuok",
            ),
        ],
    )
    release.tracklist = [
        DummyTrack("Blew", "1", "2:54"),
        DummyTrack("Floyd The Barber", "2", "2:17"),
        DummyTrack("Paper Cuts", "6", "4:05"),
        DummyTrack("Big Cheese", "12", "3:42"),
    ]

    payload = adapt_discogs_release(release)

    assert (
        payload["tracks"][0]["youtube_url"]
        == "https://www.youtube.com/watch?v=7E-KAP359ys"
    )
    assert (
        payload["tracks"][1]["youtube_url"]
        == "https://www.youtube.com/watch?v=cBm-XeOVIeY"
    )
    assert (
        payload["tracks"][2]["youtube_url"]
        == "https://www.youtube.com/watch?v=R2dcjXpgIlc"
    )
    assert payload["tracks"][3]["youtube_url"] is None


def test_adapt_discogs_release_prefers_single_track_video_for_the_mountain_and_keeps_orange_county_empty():
    release = DummyRelease(
        release_id=36594097,
        year=2025,
        released="2025-09-26",
        videos=[
            DummyVideo(
                "Gorillaz - The Mountain, The Moon Cave and The Sad God",
                "https://www.youtube.com/watch?v=ucRulNQsuYQ",
            ),
            DummyVideo(
                "Gorillaz - The Hardest Thing/Orange County",
                "https://www.youtube.com/watch?v=M910sjgb3gk",
            ),
            DummyVideo(
                "Gorillaz - Bolly Noir - The Mountain (Exclusive Bonus Tracks)",
                "https://www.youtube.com/watch?v=InZ9WzfTzjE",
            ),
            DummyVideo(
                "Gorillaz - The Mountain (feat. Hindu Jea Band Jaipur)",
                "https://www.youtube.com/watch?v=DvGdqGi_USs",
            ),
        ],
    )
    release.videos.extend(
        [
            DummyVideo(
                "Gorillaz - The Happy Dictator ft. Sparks (Official Visualiser)",
                "https://www.youtube.com/watch?v=MG_npaLydKg",
            ),
            DummyVideo(
                "Gorillaz - The God of Lying ft. IDLES (Official Visualiser)",
                "https://www.youtube.com/watch?v=kJChWUcesJ4",
            ),
            DummyVideo(
                "Gorillaz  - The Empty Dream Machine ft. Black Thought, Johnny Marr and Anoushka Shankar",
                "https://www.youtube.com/watch?v=H4dfwJ_IuSw",
            ),
            DummyVideo(
                "Gorillaz - The Manifesto ft. Trueno & Proof (Official Visualiser)",
                "https://www.youtube.com/watch?v=6JIv1l96zN0",
            ),
            DummyVideo(
                "Gorillaz  - The Plastic Guru ft. Johnny Marr and Anoushka Shankar",
                "https://www.youtube.com/watch?v=5dhgbroe8zM",
            ),
            DummyVideo(
                "Gorillaz - Delirium ft. Mark E Smith",
                "https://www.youtube.com/watch?v=yw8ftPahxAg",
            ),
            DummyVideo(
                "Gorillaz - Damascus ft. Omar Souleyman and Yasiin Bey (Official Visualiser)",
                "https://www.youtube.com/watch?v=BrPffpg9KFM",
            ),
            DummyVideo(
                "Gorillaz - The Shadowy Light ft. Asha Bhosle, Gruff Rhys, Ajay Prasanna",
                "https://www.youtube.com/watch?v=P0gLOXQM6-c",
            ),
            DummyVideo(
                "Gorillaz - Casablanca ft. Paul Simonon and Johnny Marr",
                "https://www.youtube.com/watch?v=NiHj1L1UvQM",
            ),
            DummyVideo(
                "Gorillaz - The Sweet Prince ft. Ajay Prasanna, Johnny Marr and Anoushka Shankar",
                "https://www.youtube.com/watch?v=s62Usa_3j10",
            ),
            DummyVideo(
                "Gorillaz - The Sad God ft. Black Thought, Ajay Prasanna and Anoushka Shankar",
                "https://www.youtube.com/watch?v=fQE0FnpfqQo",
            ),
        ]
    )
    release.tracklist = [
        DummyTrack("The Mountain", "A1", "4:50"),
        DummyTrack("The Moon Cave", "A2", "4:57"),
        DummyTrack("The Happy Dictator", "A3", "4:44"),
        DummyTrack("The Hardest Thing", "B4", "2:18"),
        DummyTrack("Orange County", "B5", "3:28"),
        DummyTrack("The God Of Lying", "B6", "3:09"),
        DummyTrack("The Empty Dream Machine", "B7", "5:40"),
        DummyTrack("The Manifesto", "C8", "7:19"),
        DummyTrack("The Plastic Guru", "C9", "3:14"),
        DummyTrack("Delirium", "C10", "3:52"),
        DummyTrack("Damascus", "C11", "4:04"),
        DummyTrack("The Shadowy Light", "D12", "5:39"),
        DummyTrack("Casablanca", "D13", "3:46"),
        DummyTrack("The Sweet Prince", "D14", "4:33"),
        DummyTrack("The Sad God", "D15", "4:49"),
    ]

    payload = adapt_discogs_release(release)
    tracks = {t["title"]: t for t in payload["tracks"]}

    assert (
        tracks["The Mountain"]["youtube_url"]
        == "https://www.youtube.com/watch?v=DvGdqGi_USs"
    )
    assert (
        tracks["The Happy Dictator"]["youtube_url"]
        == "https://www.youtube.com/watch?v=MG_npaLydKg"
    )
    assert (
        tracks["The Hardest Thing"]["youtube_url"]
        == "https://www.youtube.com/watch?v=M910sjgb3gk"
    )
    assert (
        tracks["The God Of Lying"]["youtube_url"]
        == "https://www.youtube.com/watch?v=kJChWUcesJ4"
    )
    assert (
        tracks["The Empty Dream Machine"]["youtube_url"]
        == "https://www.youtube.com/watch?v=H4dfwJ_IuSw"
    )
    assert (
        tracks["The Manifesto"]["youtube_url"]
        == "https://www.youtube.com/watch?v=6JIv1l96zN0"
    )
    assert (
        tracks["The Plastic Guru"]["youtube_url"]
        == "https://www.youtube.com/watch?v=5dhgbroe8zM"
    )
    assert (
        tracks["Delirium"]["youtube_url"]
        == "https://www.youtube.com/watch?v=yw8ftPahxAg"
    )
    assert (
        tracks["Damascus"]["youtube_url"]
        == "https://www.youtube.com/watch?v=BrPffpg9KFM"
    )
    assert (
        tracks["The Shadowy Light"]["youtube_url"]
        == "https://www.youtube.com/watch?v=P0gLOXQM6-c"
    )
    assert (
        tracks["Casablanca"]["youtube_url"]
        == "https://www.youtube.com/watch?v=NiHj1L1UvQM"
    )
    assert (
        tracks["The Sweet Prince"]["youtube_url"]
        == "https://www.youtube.com/watch?v=s62Usa_3j10"
    )
    assert (
        tracks["The Sad God"]["youtube_url"]
        == "https://www.youtube.com/watch?v=fQE0FnpfqQo"
    )
    assert tracks["The Moon Cave"]["youtube_url"] is None
    assert tracks["Orange County"]["youtube_url"] is None
