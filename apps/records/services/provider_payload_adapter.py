"""
Преобразователь данных от провайдеров (Redeye, Discogs) к единому формату,
который использует модуль сборки записи `record_assembly.build_record_from_payload`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)


def _to_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _to_optional_str(value: Any) -> Optional[str]:
    text = _to_str(value)
    return text or None


def _to_str_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        result: List[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    result.append(text)
        return result
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []


def _to_int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_price_str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return None


def _mapping_get(source: Any, key: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(key)
    getter = getattr(source, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except Exception:  # noqa: BLE001
            return None
    return None


def _extract_discogs_release_id(release: Any) -> Optional[int]:
    release_id = _to_int_or_none(getattr(release, "id", None))
    if release_id is not None:
        return release_id

    fetcher = getattr(release, "fetch", None)
    if callable(fetcher):
        try:
            fetched_id = _to_int_or_none(fetcher("id"))
            if fetched_id is not None:
                return fetched_id
        except Exception:  # noqa: BLE001
            pass

    data = getattr(release, "data", None)
    return _to_int_or_none(_mapping_get(data, "id"))


def _extract_discogs_release_date_parts(
    release: Any,
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    released_raw: Any = _to_optional_str(getattr(release, "released", None))
    fetcher = getattr(release, "fetch", None)
    if released_raw is None and callable(fetcher):
        try:
            released_raw = fetcher("released")
        except Exception:  # noqa: BLE001
            released_raw = None

    if released_raw is None:
        data = getattr(release, "data", None)
        released_raw = _mapping_get(data, "released")

    released_text = _to_str(released_raw)
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    if released_text:
        parts = released_text.split("T", 1)[0].split("-")
        year = _to_int_or_none(parts[0] if len(parts) > 0 else None)
        month = _to_int_or_none(parts[1] if len(parts) > 1 else None)
        day = _to_int_or_none(parts[2] if len(parts) > 2 else None)

        if month is not None and not 1 <= month <= 12:
            month = None
        if day is not None and not 1 <= day <= 31:
            day = None
        if month is None:
            day = None

    if year is not None:
        return year, month, day

    # Резервный источник: если released отсутствует/битый, сохраняем хотя бы год.
    fallback_year = _to_int_or_none(getattr(release, "year", None))
    if fallback_year is not None:
        return fallback_year, None, None
    if callable(fetcher):
        try:
            fetched_year = _to_int_or_none(fetcher("year"))
            if fetched_year is not None:
                return fetched_year, None, None
        except Exception:  # noqa: BLE001
            pass
    data = getattr(release, "data", None)
    data_year = _to_int_or_none(_mapping_get(data, "year"))
    if data_year is not None:
        return data_year, None, None

    return None, None, None


def _extract_discogs_identifier_type_value(identifier: Any) -> tuple[str, Optional[str]]:
    if isinstance(identifier, Mapping):
        ident_type = _to_str(identifier.get("type")).lower()
        ident_value = _to_optional_str(identifier.get("value"))
        return ident_type, ident_value

    ident_type = _to_str(getattr(identifier, "type", None)).lower()
    ident_value = _to_optional_str(getattr(identifier, "value", None))
    return ident_type, ident_value


def _normalize_barcode_or_none(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits_only = "".join(ch for ch in value if ch.isdigit())
    return digits_only or value


def _normalize_common_fields(dst: Dict[str, Any], src: Mapping[str, Any]) -> None:
    """
    Нормализует общий набор полей в словаре назначения dst на основе src.
    Устраняет дублирующийся код между адаптерами.
    """
    dst["title"] = _to_str(src.get("title"))
    dst["discogs_id"] = _to_int_or_none(src.get("discogs_id"))
    dst["label"] = _to_optional_str(src.get("label"))
    dst["catalog_number"] = _to_optional_str(src.get("catalog_number"))
    dst["barcode"] = _to_optional_str(src.get("barcode"))
    dst["country"] = _to_optional_str(src.get("country"))
    dst["notes"] = _to_optional_str(src.get("notes"))
    release_year = src.get("release_year")
    if release_year is None:
        release_year = src.get("year")
    dst["release_year"] = _to_int_or_none(release_year)
    dst["release_month"] = _to_int_or_none(src.get("release_month"))
    dst["release_day"] = _to_int_or_none(src.get("release_day"))
    dst["artists"] = _to_str_list(src.get("artists"))
    dst["genres"] = _to_str_list(src.get("genres"))
    dst["styles"] = _to_str_list(src.get("styles"))
    dst["formats"] = _to_str_list(src.get("formats"))


def _normalize_tracks(seq: Any) -> List[Dict[str, Any]]:
    """
    Нормализует список треков к виду:
      {"position": str, "title": str, "duration": str|None, "position_index": int}
    """
    normalized: List[Dict[str, Any]] = []
    if not isinstance(seq, list):
        return normalized
    for index, item in enumerate(seq, start=1):
        if not isinstance(item, dict):
            continue
        title = _to_str(item.get("title"))
        if not title:
            continue
        normalized.append(
            {
                "position": _to_str(item.get("position")),
                "title": title,
                "duration": _to_optional_str(item.get("duration")),
                "position_index": int(item.get("position_index") or index),
            }
        )
    return normalized


def adapt_redeye_payload(raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Метод нормализует словарь, полученный от RedeyeService, к формату сборки записи.
    """
    if not raw_payload:
        logger.warning("adapt_redeye_payload: пустой входной словарь.")
        return {}

    src: Dict[str, Any] = dict(raw_payload)  # копия
    out: Dict[str, Any] = {}

    # особые поля Redeye
    src["catalog_number"] = _to_str(src.get("catalog_number") or "").upper() or None
    price = _to_price_str_or_none(src.get("price_gbp"))
    if price is not None:
        out["price_gbp"] = price

    _normalize_common_fields(out, src)
    out["tracks"] = _normalize_tracks(src.get("tracks"))

    logger.debug(
        "adapt_redeye_payload: нормализовано: title=%r, artists=%d, tracks=%d",
        out.get("title"),
        len(out.get("artists", [])),
        len(out.get("tracks", [])),
    )
    return out


def adapt_discogs_release(release: Any) -> Dict[str, Any]:
    """
    Метод нормализует объект `discogs_client.Release` к формату сборки записи.

    Извлекаемые поля:
      - discogs_id, title, country, notes, barcode, catalog_number, release_year;
      - release_month/release_day, если Discogs вернул полную дату `released`;
      - artists: список имён артистов;
      - label: имя первого лейбла (если есть);
      - genres/styles: списки строк;
      - formats: списки человекочитаемых строк (например 'LP', '2LP', 'ALBUM', 'VINYL');
      - tracks: список словарей с position/title/duration и position_index=1..N.
    """
    released_year, released_month, released_day = _extract_discogs_release_date_parts(
        release
    )

    # Плоский источник для общей нормализации
    src: Dict[str, Any] = {
        "title": _to_str(getattr(release, "title", "")),
        "discogs_id": _extract_discogs_release_id(release),
        "country": _to_optional_str(getattr(release, "country", None)),
        "notes": _to_optional_str(getattr(release, "notes", None)),
        "release_year": released_year,
        "release_month": released_month,
        "release_day": released_day,
        "artists": [],
        "genres": _to_str_list(getattr(release, "genres", []) or []),
        "styles": _to_str_list(getattr(release, "styles", []) or []),
        "formats": [],
        "tracks": [],
        "label": None,
        "barcode": None,
        "catalog_number": None,
    }

    # Идентификаторы (barcode / catalog number)
    identifiers = getattr(release, "identifiers", None) or []
    if not identifiers:
        data = getattr(release, "data", None)
        identifiers = _mapping_get(data, "identifiers") or []
    barcode_candidates: List[str] = []
    for ident in identifiers:
        ident_type, value = _extract_discogs_identifier_type_value(ident)
        if ident_type == "barcode" and value:
            barcode_candidates.append(value)
        if ident_type in {"catalog number", "catno"} and not src["catalog_number"]:
            src["catalog_number"] = value
    for candidate in barcode_candidates:
        normalized_barcode = _normalize_barcode_or_none(candidate)
        if normalized_barcode:
            src["barcode"] = normalized_barcode
            break

    # Артисты
    for artist_obj in getattr(release, "artists", []) or []:
        name = _to_str(getattr(artist_obj, "name", ""))
        if name:
            src["artists"].append(name)

    # Лейбл (первый)
    labels = getattr(release, "labels", []) or []
    if labels:
        first_label = labels[0]
        if isinstance(first_label, Mapping):
            src["label"] = _to_optional_str(first_label.get("name"))
            catno = _to_optional_str(first_label.get("catno"))
        else:
            src["label"] = _to_optional_str(getattr(first_label, "name", None))
            catno = _to_optional_str(getattr(first_label, "catno", None))
        if not src["catalog_number"]:
            src["catalog_number"] = catno

    # Форматы
    out_formats: List[str] = []
    for fmt in getattr(release, "formats", []) or []:
        if not isinstance(fmt, dict):
            continue
        qty = _to_int_or_none(fmt.get("qty")) or 1
        descriptions = [
            d.upper() for d in (fmt.get("descriptions") or []) if isinstance(d, str)
        ]
        if "LP" in descriptions:
            out_formats.append(f"{qty}LP" if qty > 1 else "LP")
        for description in descriptions:
            if description not in {"LP", "2LP", "3LP", "4LP", "5LP", "6LP"}:
                out_formats.append(description)
    src["formats"] = out_formats

    # Треки (position_index = порядковый номер)
    tracks_src: List[Dict[str, Any]] = []
    for index, track_obj in enumerate(
        (getattr(release, "tracklist", []) or []), start=1
    ):
        title_track = _to_str(getattr(track_obj, "title", ""))
        if not title_track:
            continue
        tracks_src.append(
            {
                "position": _to_str(getattr(track_obj, "position", "")),
                "title": title_track,
                "duration": _to_optional_str(getattr(track_obj, "duration", None)),
                "position_index": index,
            }
        )
    src["tracks"] = tracks_src

    out: Dict[str, Any] = {}
    _normalize_common_fields(out, src)
    out["tracks"] = _normalize_tracks(src.get("tracks"))

    logger.debug(
        "adapt_discogs_release: нормализовано: title=%r, artists=%d, tracks=%d",
        out.get("title"),
        len(out.get("artists", [])),
        len(out.get("tracks", [])),
    )
    return out


def adapt_discogs_payload(raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Метод нормализует уже «плоский» словарь, полученный от
    DiscogsService.extract_release_data(...), к формату сборки записи.
    """
    src: Dict[str, Any] = dict(raw_payload)
    out: Dict[str, Any] = {}
    _normalize_common_fields(out, src)
    out["tracks"] = _normalize_tracks(src.get("tracks"))
    logger.debug(
        "adapt_discogs_payload: нормализовано: title=%r, artists=%d, tracks=%d",
        out.get("title"),
        len(out.get("artists", [])),
        len(out.get("tracks", [])),
    )
    return out
