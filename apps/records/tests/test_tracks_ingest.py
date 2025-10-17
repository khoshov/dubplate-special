import io
import logging

import pytest
from ..models import Record, Track
from ..services.tracks.ingest import create_tracks_for_record

pytestmark = pytest.mark.django_db


def test_create_tracks_assigns_indices_when_missing():
    rec = Record.objects.create(title="Test Release", catalog_number="TEST001")

    payload = [
        {"position": "A1", "title": "Alpha", "duration": "03:00"},
        {"position": "A2", "title": "Beta", "duration": None},
        {"position": "B1", "title": "Gamma"},
    ]
    objs = create_tracks_for_record(rec, payload)

    assert len(objs) == 3
    rows = list(Track.objects.filter(record=rec).order_by("position_index"))
    assert [(t.position_index, t.position, t.title, t.duration) for t in rows] == [
        (1, "A1", "Alpha", "03:00"),
        (2, "A2", "Beta", None),
        (3, "B1", "Gamma", None),
    ]


def test_respects_explicit_position_index_and_does_not_renumber():
    rec = Record.objects.create(title="Indexed", catalog_number="TEST002")
    payload = [
        {"position": "A1", "title": "Alpha", "position_index": 10},
        {"position": "A2", "title": "Beta", "position_index": 5},
    ]
    create_tracks_for_record(rec, payload)

    rows = list(Track.objects.filter(record=rec).order_by("position_index"))
    assert [(t.position_index, t.position, t.title) for t in rows] == [
        (5, "A2", "Beta"),
        (10, "A1", "Alpha"),
    ]


def test_skips_items_without_title_and_logs_warning():
    rec = Record.objects.create(title="Skip Empty", catalog_number="TEST003")
    payload = [
        {"position": "A1", "title": ""},
        {"position": "A2"},  # вовсе без title
        {"position": "A3", "title": "Ok"},
    ]

    # Вешаем временный handler на проектный логгер, чтобы гарантированно поймать запись,
    # даже если propagate=False и caplog её не видит.
    log = logging.getLogger("records.services.tracks.ingest")
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.WARNING)

    log.addHandler(handler)
    try:
        create_tracks_for_record(rec, payload)
    finally:
        log.removeHandler(handler)

    # 1) Проверяем БД — должна остаться только одна валидная запись
    assert Track.objects.filter(record=rec).count() == 1

    # 2) Проверяем, что предупреждение действительно залогировано
    text = buf.getvalue()
    assert "skip track without title" in text


def test_empty_input_returns_empty_and_creates_nothing(caplog):
    rec = Record.objects.create(title="Empty", catalog_number="TEST004")
    with caplog.at_level("INFO"):
        objs = create_tracks_for_record(rec, [])
    assert objs == []
    assert Track.objects.filter(record=rec).count() == 0
