"""
Сборка модели Record из нормализованного payload провайдера.

Модуль предоставляет функции:
  - build_record_from_payload(data) -> Record:
        Создаёт Record и сразу привязывает связанные сущности, а также записывает треклист.
  - attach_relations(record, data) -> None:
        Привязывает артистов/лейбл/жанры/стили/форматы по данным payload.
  - write_tracklist(record, tracks, replace=True) -> int:
        Создаёт (или пересоздаёт) треклист записи через сервис треков.

Требования к payload (минимум):
  title: str
  artists: list[str]            (опционально)
  label: str | None             (опционально)
  catalog_number: str | None    (опционально, но желательно уникально)
  barcode, country, notes: опционально
  release_year, release_month, release_day: int | None
  genres, styles, formats: list[str] (опционально)
  tracks: последовательность словарей c ключами:
      - title: str
      - duration: str | None
      - position: str (например 'A1') — опционально
      - position_index: int (1..N) — используется для будущей привязки аудио
"""

from __future__ import annotations

import logging
from typing import Mapping, Sequence

from django.db import transaction

from records.models import Artist, Format, Genre, Label, Record, Style
from records.services.tracklist_writer import create_tracks_for_record

logger = logging.getLogger(__name__)


def build_record_from_payload(data: Mapping[str, object]) -> Record:
    """
    Метод создаёт запись `Record` из нормализованного payload провайдера.

    Выполняемые шаги:
      1) Создаётся `Record` с базовыми полями (title, catalog_number, дата, ...).
      2) Привязываются связанные объекты (artists/label/genres/styles/formats).
      3) Создаётся треклист (через сервис `create_tracks_for_record`).

    Args:
        data: Нормализованный словарь полей провайдера (см. описание модуля).

    Returns:
        Созданная модель `Record`.

    Raises:
        ValueError: если отсутствует обязательное поле 'title'.
    """
    title = _str_or_empty(data.get("title"))
    if not title:
        raise ValueError("Поле 'title' обязательно для создания записи Record.")

    catalog_number = _clean_or_none(data.get("catalog_number"))
    release_year = _int_or_none(data.get("release_year"))
    release_month = _int_or_none(data.get("release_month"))
    release_day = _int_or_none(data.get("release_day"))
    barcode = _clean_or_none(data.get("barcode"))
    country = _clean_or_none(data.get("country"))
    notes = _clean_or_none(data.get("notes"))

    tracks_payload: Sequence[Mapping[str, object]] = _seq_of_maps(data.get("tracks"))

    with transaction.atomic():
        record = Record.objects.create(
            title=title,
            catalog_number=catalog_number,
            barcode=barcode,
            country=country,
            notes=notes,
            release_year=release_year,
            release_month=release_month,
            release_day=release_day,
        )
        attach_relations(record, data)
        create_tracklist(record, tracks_payload, replace=True)

    logger.info(
        "Создана запись Record (pk=%s, CAT=%s)", record.pk, record.catalog_number or "—"
    )
    return record


def update_record_from_payload(record: Record, data: Mapping[str, object]) -> Record:
    """
    Метод обновляет существующую запись `Record` по нормализованной структуре данных.
    Обновляет базовые поля, связи и полностью пересоздаёт треклист.

    Args:
        record: Запись, которую нужно обновить.
        data:   Нормализованная структура данных (как у адаптеров).

    Returns:
        Record: Обновлённая запись.
    """
    title = _str_or_empty(data.get("title"))
    if title and record.title != title:
        record.title = title

    # базовые идентификаторы/метаданные (обновляем только если данные даны)
    catalog_number = _clean_or_none(data.get("catalog_number"))
    if catalog_number:
        record.catalog_number = catalog_number

    record.barcode = _clean_or_none(data.get("barcode")) or record.barcode
    record.country = _clean_or_none(data.get("country")) or record.country
    record.notes = _clean_or_none(data.get("notes")) or record.notes

    ry = _int_or_none(data.get("release_year"))
    rm = _int_or_none(data.get("release_month"))
    rd = _int_or_none(data.get("release_day"))
    record.release_year = ry if ry is not None else record.release_year
    record.release_month = rm if rm is not None else record.release_month
    record.release_day = rd if rd is not None else record.release_day

    record.save()

    # связи и треклист — одинаково для всех источников
    attach_relations(record, data)
    tracks_payload = _seq_of_maps(data.get("tracks"))
    create_tracklist(record, tracks_payload, replace=True)

    logger.info(
        "Запись обновлена из нормализованных данных (pk=%s, CAT=%s)",
        record.pk,
        record.catalog_number or "—",
    )
    return record


def attach_relations(record: Record, data: Mapping[str, object]) -> None:
    """
    Метод привязывает к записи связанные сущности по данным payload:
    артисты, лейбл, жанры, стили, форматы.

    Правила:
      - Сопоставление по name нечувствительно к регистру (iexact).
      - 'not specified' / 'не указан' канонизируется в 'Not specified'.
      - Пустые строки игнорируются.

    Args:
        record: Модель записи, к которой выполняется привязка.
        data:   Нормализованный payload провайдера.
    """
    for raw in _list_of_str(data.get("artists")):
        name = _clean_or_none(raw)
        if not name:
            continue
        artist = Artist.objects.filter(name__iexact=name).first()
        if artist is None:
            artist = Artist.objects.create(name=name)
        record.artists.add(artist)

    label_name = _clean_or_none(data.get("label"))
    if label_name:
        label = Label.objects.filter(name__iexact=label_name).first()
        if label is None:
            label = Label.objects.create(name=label_name)
        if record.label_id != label.id:
            record.label = label
            record.save(update_fields=["label"])

    for raw in _list_of_str(data.get("genres")):
        name = _canon_genre(raw)
        if not name:
            continue
        obj = Genre.objects.filter(name__iexact=name).first() or Genre.objects.create(
            name=name
        )
        record.genres.add(obj)

    for raw in _list_of_str(data.get("styles")):
        name = _canon_style(raw)
        if not name:
            continue
        obj = Style.objects.filter(name__iexact=name).first() or Style.objects.create(
            name=name
        )
        record.styles.add(obj)

    for raw in _list_of_str(data.get("formats")):
        name = _clean_or_none(raw)
        if not name:
            continue
        obj = Format.objects.filter(name__iexact=name).first() or Format.objects.create(
            name=name
        )
        record.formats.add(obj)


def create_tracklist(
    record: Record,
    tracks: Sequence[Mapping[str, object]],
    *,
    replace: bool = True,
) -> int:
    """
    Метод создаёт треклист записи.

    На практике это тонкая обёртка над `create_tracks_for_record`, чтобы централизовать
    место вызова и оставить SRP: сборка записи использует отдельный сервис треков.

    Args:
        record: Запись, в которую добавляются треки.
        tracks: Последовательность словарей (title, duration, position, position_index).
        replace: При True существующие треки записи удаляются перед созданием.

    Returns:
        int: Количество созданных треков.
    """
    created_tracks = create_tracks_for_record(record, tracks, replace=replace)
    created_count = len(created_tracks)
    logger.info("Создан треклист (%d треков) для записи %s", created_count, record.pk)
    return created_count


def _clean_or_none(value: object) -> str | None:
    """Возвращает обрезанную строку или None, если пусто/None/нестрока."""
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


def _str_or_empty(value: object) -> str:
    """Возвращает строку (обрезанную) или пустую строку, если значение некорректно."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _int_or_none(value: object) -> int | None:
    """Пытается привести значение к int; при неудаче — возвращает None."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _list_of_str(value: object) -> list[str]:
    """Возвращает список строк, отфильтрованных от пустых значений."""
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for it in value:
            if isinstance(it, str):
                s = it.strip()
                if s:
                    out.append(s)
        return out
    return []


def _seq_of_maps(value: object) -> Sequence[Mapping[str, object]]:
    """Гарантирует, что вернётся последовательность отображений (по умолчанию пустой список)."""
    if isinstance(value, (list, tuple)):
        return [it for it in value if isinstance(it, Mapping)]
    return []


def _norm_vocab_key(value: str) -> str:
    """Нормализует строку словаря для устойчивого сравнения."""
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def _canon_genre(name: object) -> str:
    """
    Возвращает канонизированное имя жанра.
    - not specified / не указан / не указано -> Not specified
    """
    s = _clean_or_none(name)
    if not s:
        return ""

    key = _norm_vocab_key(s)
    if key in {"not specified", "не указан", "не указано"}:
        return "Not specified"
    return s


def _canon_style(name: object) -> str:
    """
    Возвращает канонизированное имя стиля.
    - not specified / не указан / не указано -> Not specified
    """
    s = _clean_or_none(name)
    if not s:
        return ""

    key = _norm_vocab_key(s)
    if key in {"not specified", "не указан", "не указано"}:
        return "Not specified"
    return s
