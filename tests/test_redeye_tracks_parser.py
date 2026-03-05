from __future__ import annotations

from records.services.providers.redeye.redeye_tracks_parser import parse_redeye_tracks


def test_parse_redeye_tracks_splits_inline_positions() -> None:
    html = """
    <div class="tracks">
      A1. Ghosts Of Massilia A2. Haunted Hill A3. Augmented Love
      B1. 99 Sand Attack B2. Glass Limits
    </div>
    """

    tracks = parse_redeye_tracks(html)

    assert tracks == [
        {
            "position": "A1",
            "title": "Ghosts Of Massilia",
            "duration": None,
            "position_index": 1,
        },
        {
            "position": "A2",
            "title": "Haunted Hill",
            "duration": None,
            "position_index": 2,
        },
        {
            "position": "A3",
            "title": "Augmented Love",
            "duration": None,
            "position_index": 3,
        },
        {
            "position": "B1",
            "title": "99 Sand Attack",
            "duration": None,
            "position_index": 4,
        },
        {
            "position": "B2",
            "title": "Glass Limits",
            "duration": None,
            "position_index": 5,
        },
    ]


def test_parse_redeye_tracks_multiline_with_duration_regression() -> None:
    html = """
    <div class="tracks">
      A1 Flow Key 06:19<br>A2 Reso 02 05:58<br>B1 Orbit 07:00
    </div>
    """

    tracks = parse_redeye_tracks(html)

    assert tracks == [
        {
            "position": "A1",
            "title": "Flow Key",
            "duration": "06:19",
            "position_index": 1,
        },
        {
            "position": "A2",
            "title": "Reso 02",
            "duration": "05:58",
            "position_index": 2,
        },
        {
            "position": "B1",
            "title": "Orbit",
            "duration": "07:00",
            "position_index": 3,
        },
    ]


def test_parse_redeye_tracks_slash_delimited_regression() -> None:
    html = '<div class="tracks">Moon Cruise / Never Stop / Last One</div>'

    tracks = parse_redeye_tracks(html)

    assert tracks == [
        {"position": "", "title": "Moon Cruise", "duration": None, "position_index": 1},
        {"position": "", "title": "Never Stop", "duration": None, "position_index": 2},
        {"position": "", "title": "Last One", "duration": None, "position_index": 3},
    ]
