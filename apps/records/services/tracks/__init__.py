"""
Утилиты работы с треками:
- парсинг треклистов из источников (Redeye и др.);
- единая точка записи треков в БД.
"""

from apps.records.services.providers.redeye.parsers.redeye_tracks_parser import TrackPayload, parse_redeye_tracks
from .ingest import TrackLike, create_tracks_for_record

__all__ = [
    # parsing
    "TrackPayload",
    "parse_redeye_tracks",
    # ingest
    "TrackLike",
    "create_tracks_for_record",
]
