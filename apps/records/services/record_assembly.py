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
   discogs_id: int | None
   release_year, release_month, release_day: int | None
  genres, styles, formats: list[str] (опционально)
  structured_formats: последовательность словарей с ключами:
      - variant_of_format: int
      - carrier: str | None
      - quantity: int
      - format_name: str | None
      - details: str | None
  tracks: последовательность словарей c ключами:
      - title: str
      - duration: str | None
      - position: str (например 'A1') — опционально
      - position_index: int (1..N) — используется для будущей привязки аудио
"""

from __future__ import annotations

import logging
import re
from typing import Mapping, Sequence

from django.db import transaction
from config.logging import NOTICE_LEVEL, log_event

from records.models import (
    Artist,
    Format,
    FormatChoices,
    Genre,
    Label,
    Record,
    Style,
    StructuredFormat,
)
from records.services.tracklist_writer import create_tracks_for_record

logger = logging.getLogger(__name__)
_RECORD_ASSEMBLY_COMPONENT = "record_assembly"
_INVALID_CATALOG_VALUES = {"NONE", "NULL", "N/A", "N-A", "-", "—"}
_DEFAULT_LEGACY_FORMAT_NAME = FormatChoices.NOT_SPECIFIED
_ALLOWED_LEGACY_FORMAT_NAMES = tuple(choice.value for choice in FormatChoices)
_DISCOGS_DISAMBIGUATION_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")
STRUCTURED_FORMAT_INCOMPLETE_ERROR = (
    "Поля структурированного формата заполнен не полностью. "
    'Обязательны к заполнению: "Носитель", "Количество" и "Формат". '
    "Либо очистите поля структурированного формата и выберите значение "
    "в стандартном справочнике форматов."
)


def _log_record_assembly_event(
    level: int,
    event: str,
    message: str,
    **context,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_RECORD_ASSEMBLY_COMPONENT,
        event=event,
        **context,
    )


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

    catalog_number = _clean_catalog_number_or_none(data.get("catalog_number"))
    discogs_id = _int_or_none(data.get("discogs_id"))
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
            discogs_id=discogs_id,
            catalog_number=catalog_number,
            barcode=barcode,
            country=country,
            notes=notes,
            release_year=release_year,
            release_month=release_month,
            release_day=release_day,
        )
        attach_relations(record, data)
        sync_format_state(record, data, preserve_existing_legacy_formats=False)
        create_tracklist(record, tracks_payload, replace=True)

    _log_record_assembly_event(
        logging.INFO,
        "record_created",
        "Создана запись Record из нормализованного payload.",
        record_id=record.pk,
        catalog_number=record.catalog_number or "—",
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
    discogs_id = _int_or_none(data.get("discogs_id"))
    if discogs_id is not None:
        record.discogs_id = discogs_id

    catalog_number = _clean_catalog_number_or_none(data.get("catalog_number"))
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
    # Для update из Discogs artists должны отражать актуальный состав, без накопления старых связей.
    if "artists" in data:
        record.artists.clear()
    attach_relations(record, data)
    sync_format_state(record, data, preserve_existing_legacy_formats=True)
    tracks_payload = _seq_of_maps(data.get("tracks"))
    create_tracklist(
        record,
        tracks_payload,
        replace=True,
        preserve_existing_audio_previews=True,
    )

    _log_record_assembly_event(
        logging.INFO,
        "record_updated",
        "Запись обновлена из нормализованного payload.",
        record_id=record.pk,
        catalog_number=record.catalog_number or "—",
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
        artist = _find_or_create_entity_by_name(Artist, name)
        if artist is None:
            continue
        record.artists.add(artist)

    label_name = _clean_or_none(data.get("label"))
    if label_name:
        label = _find_or_create_entity_by_name(Label, label_name)
        if label is None:
            return
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


def create_tracklist(
    record: Record,
    tracks: Sequence[Mapping[str, object]],
    *,
    replace: bool = True,
    preserve_existing_audio_previews: bool = False,
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
    created_tracks = create_tracks_for_record(
        record,
        tracks,
        replace=replace,
        preserve_existing_audio_previews=preserve_existing_audio_previews,
    )
    created_count = len(created_tracks)
    _log_record_assembly_event(
        logging.INFO,
        "tracklist_created",
        "Создан треклист записи.",
        record_id=record.pk,
        created_tracks=created_count,
        replace=replace,
        preserve_existing_audio_previews=preserve_existing_audio_previews,
    )
    return created_count


def sync_format_state(
    record: Record,
    data: Mapping[str, object],
    *,
    preserve_existing_legacy_formats: bool,
) -> None:
    """
    Синхронизирует structured-format слой и временную legacy-проекцию.

    Правила:
      - structured rows и legacy `Record.formats` живут независимо;
      - если в payload есть `structured_formats`, они полностью заменяют structured-слой;
      - если в payload есть `formats`, они заменяют legacy M2M;
      - если `formats` не переданы:
          * при preserve_existing_legacy_formats=True сохраняем текущее значение
            или выставляем дефолт `Not specified`, если значение отсутствует;
          * иначе выставляем дефолт `Not specified`.
    """
    if "structured_formats" in data:
        normalized_rows = normalize_structured_format_rows(
            _seq_of_maps(data.get("structured_formats"))
        )
        replace_structured_format_rows(record, normalized_rows)

    if "formats" in data:
        replace_legacy_formats(record, _list_of_str(data.get("formats")))
        return

    if preserve_existing_legacy_formats:
        ensure_legacy_formats(record)
        return

    replace_legacy_formats(record, [])


def normalize_structured_format_rows(
    rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """
    Приводит structured rows к стабильному контракту хранения.

    Отбрасывает полностью пустые строки, заполняет `quantity=1` по умолчанию
    и выставляет `variant_of_format`, если он не был задан.
    """
    normalized: list[dict[str, object]] = []
    for default_order, row in enumerate(rows, start=1):
        carrier = _clean_or_none(row.get("carrier"))
        format_name = _clean_or_none(row.get("format_name"))
        details = _clean_or_none(row.get("details"))
        if not any((carrier, format_name, details)):
            continue

        quantity = _positive_int_or_default(row.get("quantity"), default=1)
        variant_of_format = _positive_int_or_default(
            row.get("variant_of_format") or row.get("sort_order"),
            default=default_order,
        )
        normalized.append(
            {
                "variant_of_format": variant_of_format,
                "carrier": carrier or "",
                "quantity": quantity,
                "format_name": format_name or "",
                "details": details or "",
            }
        )
    return normalized


def get_structured_format_incomplete_error(
    *,
    carrier: object,
    quantity: object,
    format_name: object,
    details: object,
) -> str | None:
    """
    Возвращает текст ошибки, если structured format заполнен частично.

    Пустая строка допускается. Для полностью заполненного structured format
    обязательны carrier, quantity и format_name.
    """
    normalized_carrier = _clean_or_none(carrier) or ""
    normalized_format_name = _clean_or_none(format_name) or ""
    normalized_details = _clean_or_none(details) or ""
    normalized_quantity = _int_or_none(quantity)

    if not any((normalized_carrier, normalized_format_name, normalized_details)):
        if normalized_quantity in (None, 1):
            return None
        return STRUCTURED_FORMAT_INCOMPLETE_ERROR

    if (
        normalized_carrier
        and normalized_format_name
        and normalized_quantity is not None
        and normalized_quantity > 0
    ):
        return None

    return STRUCTURED_FORMAT_INCOMPLETE_ERROR


def replace_structured_format_rows(
    record: Record,
    rows: Sequence[Mapping[str, object]],
) -> list[StructuredFormat]:
    """
    Полностью заменяет structured-format строки записи на новый набор.
    """
    normalized_rows = normalize_structured_format_rows(rows)
    record.structured_formats.all().delete()

    objects = [
        StructuredFormat(
            record=record,
            variant_of_format=int(row["variant_of_format"]),
            carrier=str(row["carrier"]),
            quantity=int(row["quantity"]),
            format_name=str(row["format_name"]),
            details=str(row["details"]),
        )
        for row in normalized_rows
    ]
    if objects:
        StructuredFormat.objects.bulk_create(objects)

    _sync_active_structured_format_variant(
        record,
        [int(row["variant_of_format"]) for row in normalized_rows],
    )

    _log_record_assembly_event(
        logging.INFO,
        "structured_formats_synced",
        "Синхронизированы structured format-строки записи.",
        record_id=record.pk,
        structured_rows=len(objects),
    )
    return objects


def ensure_active_structured_format_variant(record: Record) -> None:
    """Гарантирует, что активный вариант указывает на существующий structured format."""
    if not getattr(record, "pk", None):
        return

    variants = list(
        record.structured_formats.order_by("variant_of_format", "id").values_list(
            "variant_of_format",
            flat=True,
        )
    )
    _sync_active_structured_format_variant(record, variants)


def _sync_active_structured_format_variant(
    record: Record,
    variants: Sequence[int],
) -> None:
    """Сохраняет на записи активный вариант формата."""
    selected_variant: int | None = None
    if variants:
        current_variant = record.active_structured_format_variant
        selected_variant = (
            current_variant if current_variant in variants else variants[0]
        )

    if record.active_structured_format_variant == selected_variant:
        return

    record.active_structured_format_variant = selected_variant
    record.save(update_fields=["active_structured_format_variant", "modified"])


def ensure_legacy_formats(record: Record) -> None:
    """
    Гарантирует, что у записи всегда есть хотя бы один legacy-формат.

    Если выбран валидный формат из библиотеки, состояние не меняется.
    Если формат не выбран или содержит неканонические значения,
    запись приводится к библиотеке или получает дефолт `Not specified`.
    """
    if not getattr(record, "pk", None):
        return

    current_names = list(record.formats.values_list("name", flat=True))
    normalized_names: list[str] = []
    seen: set[str] = set()

    for raw_name in current_names:
        canonical_name = _canon_legacy_format(raw_name)
        if not canonical_name:
            normalized_names = []
            break

        canonical_key = canonical_name.casefold()
        if canonical_key in seen:
            continue
        seen.add(canonical_key)
        normalized_names.append(canonical_name)

    if normalized_names and normalized_names == current_names:
        _log_record_assembly_event(
            logging.INFO,
            "legacy_formats_unchanged",
            "Legacy format-проекция сохранена без изменений.",
            record_id=record.pk,
        )
        return

    replace_legacy_formats(record, normalized_names)


def replace_legacy_formats(record: Record, format_names: Sequence[str]) -> None:
    """
    Полностью заменяет legacy M2M `Record.formats`.

    Если входные значения пустые или не входят в библиотеку допустимых форматов,
    выставляется дефолт `Not specified`.
    """
    objects: list[Format] = []
    seen: set[str] = set()
    for raw in format_names:
        name = _canon_legacy_format(raw)
        if not name:
            continue
        normalized_key = name.casefold()
        if normalized_key in seen:
            continue
        seen.add(normalized_key)
        obj = Format.objects.filter(name__iexact=name).first()
        if obj is None:
            _log_record_assembly_event(
                NOTICE_LEVEL,
                "legacy_format_replaced_with_default",
                "Значение legacy format отсутствует в справочнике и заменено на дефолт.",
                record_id=record.pk,
                format_name=name,
            )
            continue
        objects.append(obj)

    if not objects:
        default_obj = _get_default_legacy_format()
        objects = [default_obj]

    record.formats.set(objects)
    _log_record_assembly_event(
        logging.INFO,
        "legacy_formats_synced",
        "Обновлена legacy format-проекция записи.",
        record_id=record.pk,
        legacy_formats_count=len(objects),
    )


def _get_default_legacy_format() -> Format:
    default_obj = Format.objects.filter(
        name__iexact=_DEFAULT_LEGACY_FORMAT_NAME
    ).first()
    if default_obj is not None:
        return default_obj

    return Format.objects.create(name=_DEFAULT_LEGACY_FORMAT_NAME)


def _canon_legacy_format(value: object) -> str:
    """
    Канонизирует значение legacy-формата к фиксированной библиотеке.
    """
    raw = _clean_or_none(value)
    if not raw:
        return ""

    key = _norm_vocab_key(raw)
    if key in {"not specified", "не указан", "не указано"}:
        return _DEFAULT_LEGACY_FORMAT_NAME

    for allowed_name in _ALLOWED_LEGACY_FORMAT_NAMES:
        if key == _norm_vocab_key(allowed_name):
            return allowed_name

    return ""


def _clean_or_none(value: object) -> str | None:
    """Возвращает обрезанную строку или None, если пусто/None/нестрока."""
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


def _clean_catalog_number_or_none(value: object) -> str | None:
    """Очищает catalog_number и отбрасывает псевдо-пустые значения."""
    cleaned = _clean_or_none(value)
    if not cleaned:
        return None
    if cleaned.strip().upper() in _INVALID_CATALOG_VALUES:
        return None
    return cleaned


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


def _normalize_entity_name_for_match(value: object) -> str:
    text = _clean_or_none(value) or ""
    normalized = _DISCOGS_DISAMBIGUATION_SUFFIX_RE.sub("", text).strip()
    return normalized


def _find_or_create_entity_by_name(model_cls: type[Artist] | type[Label], name: str):
    exact = model_cls.objects.filter(name__iexact=name).first()
    if exact is not None:
        return exact

    normalized_name = _normalize_entity_name_for_match(name)
    if not normalized_name:
        return None

    prefix = normalized_name[:255]
    candidates = model_cls.objects.filter(name__istartswith=prefix).only("id", "name")
    for candidate in candidates:
        if (
            _normalize_entity_name_for_match(candidate.name).casefold()
            != normalized_name.casefold()
        ):
            continue
        if candidate.name != normalized_name:
            candidate.name = normalized_name
            candidate.save(update_fields=["name", "modified"])
        return candidate

    return model_cls.objects.create(name=normalized_name)


def _positive_int_or_default(value: object, *, default: int) -> int:
    """Возвращает положительное целое число или default."""
    result = _int_or_none(value)
    if result is None or result < 1:
        return default
    return result


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
