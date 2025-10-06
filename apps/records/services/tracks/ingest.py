"""
Ингест треков в БД.

Единая точка для записи Track из любых источников:
- гарантирует натуральную нумерацию 1..N (position_index) без дырок,
- создаёт записи атомарно (транзакция),
- игнорирует пустые/битые элементы с внятным логом,
- умеет заменять существующий треклист (replace=True).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Mapping

from django.db import transaction

from ...models import Record, Track

logger = logging.getLogger(__name__)

# минимальные требования к элементу входного списка
TrackLike = Mapping[str, Any]  # ожидаем "title"; опц. "position","duration","youtube_url","position_index"


def _normalize_row(t: TrackLike) -> dict:
    """Минимальная нормализация входного словаря трека (без индекса)."""
    return {
        "title": (t.get("title") or "").strip(),
        "position": (t.get("position") or "").strip(),
        "duration": (t.get("duration") or None) or None,
        "youtube_url": (t.get("youtube_url") or "").strip() or None,
    }


@transaction.atomic
def create_tracks_for_record(
        record: Record,
        tracks: Iterable[TrackLike],
        *,
        replace: bool = True,
) -> List[Track]:
    """
    Создаёт треки для указанной записи, с новой плотной нумерацией 1..N.

    Args:
        record: объект Record, для которого пишем треки.
        tracks: последовательность словарей, например результат парсера:
                {
                    "title": str,                 # обязательно
                    "position": str,              # опционально
                    "duration": str|None,         # опционально
                    "youtube_url": str|None,      # опционально
                    # входящий position_index игнорируется — индексация всегда 1..N
                }
        replace: если True — предварительно удаляет старые треки записи.

    Returns:
        Список созданных объектов Track (в порядке добавления).
    """
    items: List[TrackLike] = list(tracks or [])
    if not items:
        logger.info("create_tracks_for_record(%s): empty input", record.pk)
        if replace:
            Track.objects.filter(record=record).delete()
        return []

    rows = [_normalize_row(t) for t in items if (t and (t.get("title") or "").strip())]

    if replace:
        Track.objects.filter(record=record).delete()

    objs: List[Track] = []
    for i, r in enumerate(rows, start=1):
        objs.append(
            Track(
                record=record,
                position=r["position"],
                position_index=i,
                title=r["title"],
                duration=r["duration"],
                youtube_url=r["youtube_url"],
            )
        )

    if not objs:
        logger.info("create_tracks_for_record(%s): nothing to create after normalization", record.pk)
        return []

    Track.objects.bulk_create(objs)
    logger.debug("create_tracks_for_record(%s): created %d tracks", record.pk, len(objs))
    return objs


__all__ = ["TrackLike", "create_tracks_for_record"]
