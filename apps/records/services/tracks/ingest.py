"""
Ингест треков в БД.

Единая точка для записи Track из любых источников:
- гарантирует натуральную нумерацию 1..N (position_index),
- создаёт записи атомарно (транзакция),
- игнорирует пустые/битые элементы с внятным логом.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, List, Mapping

from django.db import transaction

from ...models import Record, Track

logger = logging.getLogger(__name__)

# минимальные требования к элементу входного списка
TrackLike = Mapping[str, Any]  # ожидаем "title"; опц. "position","duration","youtube_url","position_index"


def create_tracks_for_record(record: Record, tracks: Iterable[TrackLike]) -> List[Track]:
    """
    Создаёт треки для указанной записи.

    Args:
        record: объект Record, для которого пишем треки.
        tracks: последовательность словарей, например результат парсера:
                {
                    "title": str,                 # обязательно
                    "position": str,              # опционально
                    "duration": str|None,         # опционально
                    "youtube_url": str|None,      # опционально
                    "position_index": int|None,   # опционально (если нет — нумеруем 1..N)
                }

    Returns:
        Список созданных объектов Track (в порядке добавления).
    """
    items: List[TrackLike] = list(tracks or [])
    if not items:
        logger.info("create_tracks_for_record(%s): empty input", record.pk)
        return []

    objs: List[Track] = []
    with transaction.atomic():
        for i, t in enumerate(items, start=1):
            title = (t.get("title") or "").strip()
            if not title:
                logger.warning("skip track without title for record=%s: %r", record.pk, t)
                continue

            objs.append(
                Track(
                    record=record,
                    position=(t.get("position") or ""),
                    position_index=int(t.get("position_index") or i),
                    title=title,
                    duration=(t.get("duration") or None),
                    youtube_url=t.get("youtube_url"),
                )
            )

        Track.objects.bulk_create(objs)

    logger.debug("create_tracks_for_record(%s): created %d tracks", record.pk, len(objs))
    return objs


__all__ = ["TrackLike", "create_tracks_for_record"]
