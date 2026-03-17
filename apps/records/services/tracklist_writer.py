"""
Запись треклиста в базу данных.

Назначение:
- Создать треки для записи с плотной натуральной нумерацией 1..N (position_index).
- По желанию полностью заменить существующий треклист (replace=True).
- Игнорировать пустые/битые элементы входа, вести понятный лог.

Формат входного элемента:
    {
        "title": str,           # обязательно (непустая строка)
        "position": str,        # опционально
        "duration": str|None,   # опционально
        "youtube_url": str|None # опционально
        # входящий position_index игнорируется — нумерация всегда 1..N
    }
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, List, Mapping

from django.db import transaction

from records.models import Record, Track

logger = logging.getLogger(__name__)
_TRACK_KEY_NON_WORD_RE = re.compile(r"[^\w]+", flags=re.UNICODE)

TrackInput = Mapping[str, Any]


def _normalize_input_row(data: TrackInput) -> dict:
    """
    Метод нормализует один входной словарь трека (без индекса).

    Возвращает словарь с ключами: title, position, duration, youtube_url.
    Пустые строки приводятся к ''/None в зависимости от поля.
    """
    title = (data.get("title") or "").strip()
    position = (data.get("position") or "").strip()
    duration = (data.get("duration") or None) or None
    youtube_url = (data.get("youtube_url") or "").strip() or None

    return {
        "title": title,
        "position": position,
        "duration": duration,
        "youtube_url": youtube_url,
    }


def _normalize_track_key(value: Any) -> str:
    text = (value or "").strip().casefold()
    text = _TRACK_KEY_NON_WORD_RE.sub(" ", text)
    return " ".join(text.split())


def _pick_preserved_audio_preview_name(
    *,
    existing_rows: list[dict[str, Any]],
    title: str,
    position: str,
) -> str | None:
    title_key = _normalize_track_key(title)
    position_key = _normalize_track_key(position)

    for row in existing_rows:
        if row["used"]:
            continue
        if row["title_key"] == title_key and row["position_key"] == position_key:
            row["used"] = True
            return row["audio_preview_name"]

    for row in existing_rows:
        if row["used"]:
            continue
        if row["title_key"] == title_key:
            row["used"] = True
            return row["audio_preview_name"]

    return None


@transaction.atomic
def create_tracks_for_record(
    record: Record,
    tracks: Iterable[TrackInput],
    *,
    replace: bool = True,
    preserve_existing_audio_previews: bool = False,
) -> List[Track]:
    """
    Метод создаёт треки для указанной записи с новой плотной нумерацией 1..N.

    Args:
        record: Объект Record, для которого пишутся треки.
        tracks: Последовательность словарей (см. формат входного элемента).
        replace: Если True — предварительно удаляет старые треки записи.

    Returns:
        Список созданных объектов Track (в порядке добавления).
    """
    items: List[TrackInput] = list(tracks or [])
    existing_rows: list[dict[str, Any]] = []
    if replace and preserve_existing_audio_previews:
        for existing in Track.objects.filter(record=record).only(
            "title", "position", "audio_preview"
        ):
            existing_rows.append(
                {
                    "title_key": _normalize_track_key(existing.title),
                    "position_key": _normalize_track_key(existing.position),
                    "audio_preview_name": (
                        existing.audio_preview.name if existing.audio_preview else None
                    ),
                    "used": False,
                }
            )

    if not items:
        logger.info("create_tracks_for_record(%s): входной список пуст.", record.pk)
        if replace:
            Track.objects.filter(record=record).delete()
        return []

    # нормализация + отсев пустых заголовков
    normalized_rows = [
        _normalize_input_row(data)
        for data in items
        if data and (data.get("title") or "").strip()
    ]

    if replace:
        deleted = Track.objects.filter(record=record).delete()[0]
        if deleted:
            logger.debug(
                "create_tracks_for_record(%s): удалено старых треков: %d",
                record.pk,
                deleted,
            )

    to_create: List[Track] = []
    for index, row in enumerate(normalized_rows, start=1):
        preserved_audio_preview_name = _pick_preserved_audio_preview_name(
            existing_rows=existing_rows,
            title=row["title"],
            position=row["position"],
        )
        to_create.append(
            Track(
                record=record,
                position=row["position"],
                position_index=index,
                title=row["title"],
                duration=row["duration"],
                youtube_url=row["youtube_url"],
                audio_preview=preserved_audio_preview_name,
            )
        )

    if not to_create:
        logger.info(
            "create_tracks_for_record(%s): отсутствуют валидные элементы после нормализации.",
            record.pk,
        )
        return []

    Track.objects.bulk_create(to_create)
    logger.info(
        "create_tracks_for_record(%s): создано треков: %d", record.pk, len(to_create)
    )
    return to_create


__all__ = ["TrackInput", "create_tracks_for_record"]
