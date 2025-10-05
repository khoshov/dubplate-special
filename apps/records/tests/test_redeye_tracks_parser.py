from records.services.tracks.redeye_tracks_parser import parse_redeye_tracks

def _wrap_tracks_html(inner: str) -> str:
    return f'<div class="tracks">{inner}</div>'

def test_parse_br_with_positions_and_duration():
    html = _wrap_tracks_html(
        "A1 Flow Key 06:19<br>"
        "A2 Reso 02 05:58<br>"
        "B1 Back To The Mood 07:31<br>"
        "B2 Push Yourself 04:41"
    )
    items = parse_redeye_tracks(html)
    assert [(t["position_index"], t["position"], t["title"], t["duration"]) for t in items] == [
        (1, "A1", "Flow Key", "06:19"),
        (2, "A2", "Reso 02", "05:58"),
        (3, "B1", "Back To The Mood", "07:31"),
        (4, "B2", "Push Yourself", "04:41"),
    ]

def test_parse_slash_line_splits():
    html = _wrap_tracks_html("Moon Cruise / Never Stop / Another Tune")
    items = parse_redeye_tracks(html)
    assert [t["title"] for t in items] == ["Moon Cruise", "Never Stop", "Another Tune"]
    assert [t["position"] for t in items] == ["", "", ""]
    assert [t["position_index"] for t in items] == [1, 2, 3]

def test_parse_numeric_positions_and_alpha_punct():
    html = _wrap_tracks_html("1. Alpha<br>2) Beta<br>A1. Gamma<br>A1 - Delta<br>A2) Epsilon")
    items = parse_redeye_tracks(html)
    # Проверяем, что парсятся позиции и заголовки
    assert [(t["position"], t["title"]) for t in items] == [
        ("1", "Alpha"),
        ("2", "Beta"),
        ("A1", "Gamma"),
        ("A1", "Delta"),
        ("A2", "Epsilon"),
    ]

def test_ignore_side_headers_and_deduplicate_prefers_with_duration():
    # Два одинаковых трека: второй с duration — он должен остаться
    html = _wrap_tracks_html("Side A<br>A1. Foo<br>A1 Foo 03:30")
    items = parse_redeye_tracks(html)
    assert len(items) == 1
    assert items[0]["position"] == "A1"
    assert items[0]["title"] == "Foo"
    assert items[0]["duration"] == "03:30"
    assert items[0]["position_index"] == 1

def test_supports_two_digit_track_numbers():
    html = _wrap_tracks_html("A10 Big Song 04:00<br>A11 Next Song 05:00")
    items = parse_redeye_tracks(html)
    assert [(t["position"], t["title"], t["duration"]) for t in items] == [
        ("A10", "Big Song", "04:00"),
        ("A11", "Next Song", "05:00"),
    ]

def test_returns_empty_when_tracks_block_absent():
    # Нет блока .tracks — должно вернуть пустой список
    html = "<div class='nope'>nothing here</div>"
    items = parse_redeye_tracks(html)
    assert items == []
