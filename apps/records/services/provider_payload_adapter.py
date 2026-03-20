"""
Преобразователь данных от провайдеров (Redeye, Discogs) к единому формату,
который использует модуль сборки записи `record_assembly.build_record_from_payload`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Mapping, Optional

from config.logging import NOTICE_LEVEL, log_event

logger = logging.getLogger(__name__)
_PAYLOAD_ADAPTER_COMPONENT = "provider_payload_adapter"
_INVALID_CATALOG_VALUES = {"NONE", "NULL", "N/A", "N-A", "-", "—"}
_CANONICAL_CARRIERS = {
    "VINYL": "Vinyl",
    "CD": "CD",
    "CASSETTE": "Cassette",
}
_DISCOGS_DISAMBIGUATION_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")
_PART_NUMBER_RE = re.compile(r"\bpart[.\s_-]*(\d+)\b", flags=re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^\w]+", flags=re.UNICODE)
_TRACK_VIDEO_NOISE_TOKENS = {
    "remaster",
    "remastered",
    "radio",
    "radioshack",
    "official",
    "video",
    "audio",
    "lyrics",
    "lyric",
    "version",
}
_TRACK_VIDEO_COLLECTION_MARKERS = {
    "full album",
    "vinyl rip",
}
_TRACK_VIDEO_MULTI_MARKERS = {"/", ","}
_TRACK_VIDEO_EXTRA_MARKERS = {
    "bonus",
    "exclusive",
}
_TRACK_VIDEO_DASH_SPLIT_RE = re.compile(r"\s+-\s+", flags=re.UNICODE)
_TRACK_VIDEO_SPLIT_ARTIST_RE = re.compile(r"^\s*[^-]+-\s*(.+)$")
_TRACK_VIDEO_FEAT_SPLIT_RE = re.compile(
    r"\b(?:ft|feat|featuring)\.?\b",
    flags=re.IGNORECASE,
)
_TRACK_VIDEO_NOISE_SUFFIX_RE = re.compile(
    r"\b(?:official|visualiser|visualizer|video|audio|lyric|lyrics)\b.*$",
    flags=re.IGNORECASE,
)
_TRACK_VIDEO_POSITION_TOKEN_RE = re.compile(
    r"\b([A-Z]{1,3}\s*[-./]?\s*\d{1,3}|\d{1,3})\b",
    flags=re.IGNORECASE,
)


def _log_payload_adapter_event(
    level: int,
    event: str,
    message: str,
    **context: object,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_PAYLOAD_ADAPTER_COMPONENT,
        event=event,
        **context,
    )


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


def _extract_discogs_identifier_type_value(
    identifier: Any,
) -> tuple[str, Optional[str]]:
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


def _normalize_catalog_number_or_none(value: Any) -> Optional[str]:
    text = _to_optional_str(value)
    if not text:
        return None
    if text.strip().upper() in _INVALID_CATALOG_VALUES:
        return None
    return text


def _normalize_discogs_entity_name(value: Any) -> Optional[str]:
    text = _to_optional_str(value)
    if not text:
        return None
    normalized = _DISCOGS_DISAMBIGUATION_SUFFIX_RE.sub("", text).strip()
    return normalized or None


def _normalize_discogs_title_for_match(value: Any) -> str:
    text = _to_str(value).casefold()
    text = _PART_NUMBER_RE.sub(lambda m: f" part {m.group(1)} ", text)
    text = text.replace("&", " and ")
    text = _NON_WORD_RE.sub(" ", text)
    return " ".join(text.split())


def _extract_discogs_part_number(value: Any) -> Optional[int]:
    match = _PART_NUMBER_RE.search(_to_str(value))
    if not match:
        return None
    return _to_int_or_none(match.group(1))


def _discogs_base_title(value: Any) -> str:
    normalized = _normalize_discogs_title_for_match(value)
    base = _PART_NUMBER_RE.sub(" ", normalized)
    return " ".join(base.split())


def _discogs_match_tokens(value: Any) -> set[str]:
    tokens = {
        token
        for token in _normalize_discogs_title_for_match(value).split()
        if token and token not in _TRACK_VIDEO_NOISE_TOKENS
    }
    return tokens


def _is_discogs_collection_video_title(value: Any) -> bool:
    normalized = _normalize_discogs_title_for_match(value)
    return any(marker in normalized for marker in _TRACK_VIDEO_COLLECTION_MARKERS)


def _discogs_video_track_core(value: Any) -> str:
    raw = _to_str(value)
    if not raw:
        return ""
    artist_split = _TRACK_VIDEO_SPLIT_ARTIST_RE.match(raw)
    core = artist_split.group(1) if artist_split else raw
    core = _TRACK_VIDEO_FEAT_SPLIT_RE.split(core, maxsplit=1)[0]
    core = _TRACK_VIDEO_NOISE_SUFFIX_RE.sub("", core)
    return _normalize_discogs_title_for_match(core)


def _discogs_video_side_core(value: Any) -> str:
    raw = _to_str(value)
    if not raw:
        return ""
    core = _TRACK_VIDEO_FEAT_SPLIT_RE.split(raw, maxsplit=1)[0]
    core = _TRACK_VIDEO_NOISE_SUFFIX_RE.sub("", core)
    return _normalize_discogs_title_for_match(core)


def _extract_discogs_release_artist_clues(release: Any) -> List[Dict[str, Any]]:
    clues: List[Dict[str, Any]] = []
    seen_bases: set[str] = set()
    for artist_obj in getattr(release, "artists", []) or []:
        if isinstance(artist_obj, Mapping):
            artist_name = _normalize_discogs_entity_name(artist_obj.get("name"))
        else:
            artist_name = _normalize_discogs_entity_name(
                getattr(artist_obj, "name", None)
            )
        if not artist_name:
            continue
        base_title = _discogs_base_title(artist_name)
        if not base_title or base_title in seen_bases:
            continue
        seen_bases.add(base_title)
        clues.append(
            {
                "base_title": base_title,
                "tokens": _discogs_match_tokens(artist_name),
            }
        )
    return clues


def _score_discogs_video_artist_side(
    core_value: str,
    release_artist_clues: List[Dict[str, Any]],
) -> int:
    if not core_value or not release_artist_clues:
        return 0

    base_title = _discogs_base_title(core_value)
    tokens = _discogs_match_tokens(core_value)
    score = 0
    for clue in release_artist_clues:
        clue_base_title = _to_str(clue.get("base_title"))
        clue_tokens = clue.get("tokens")
        if base_title and base_title == clue_base_title:
            score = max(score, 10)
            continue
        if (
            isinstance(clue_tokens, set)
            and clue_tokens
            and tokens
            and clue_tokens.issubset(tokens)
        ):
            score = max(score, len(clue_tokens))
    return score


def _discogs_video_track_core_candidates(
    value: Any,
    *,
    release_artist_clues: List[Dict[str, Any]],
) -> List[str]:
    raw = _to_str(value)
    if not raw:
        return []

    split_parts = [part.strip() for part in _TRACK_VIDEO_DASH_SPLIT_RE.split(raw, 1)]
    candidates: List[str]
    if len(split_parts) == 2:
        left_core = _discogs_video_side_core(split_parts[0])
        right_core = _discogs_video_side_core(split_parts[1])
        left_score = _score_discogs_video_artist_side(
            left_core,
            release_artist_clues,
        )
        right_score = _score_discogs_video_artist_side(
            right_core,
            release_artist_clues,
        )
        if left_score > right_score:
            candidates = [right_core, left_core]
        elif right_score > left_score:
            candidates = [left_core, right_core]
        else:
            candidates = [right_core, left_core]
    else:
        candidates = [_discogs_video_track_core(raw)]

    seen_candidates: set[str] = set()
    result: List[str] = []
    for candidate in candidates:
        if not candidate or candidate in seen_candidates:
            continue
        seen_candidates.add(candidate)
        result.append(candidate)
    return result


def _is_discogs_single_track_video_title(
    value: Any,
    *,
    release_artist_clues: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    normalized_candidates = _discogs_video_track_core_candidates(
        value,
        release_artist_clues=release_artist_clues or [],
    )
    normalized = normalized_candidates[0] if normalized_candidates else ""
    if not normalized:
        return False
    if any(marker in normalized for marker in _TRACK_VIDEO_EXTRA_MARKERS):
        return False
    if any(marker in normalized for marker in _TRACK_VIDEO_MULTI_MARKERS):
        return False
    if " and " in normalized:
        return False
    return True


def _normalize_discogs_position_marker(value: Any) -> Optional[str]:
    text = _to_str(value).upper()
    if not text:
        return None
    marker = re.sub(r"[^A-Z0-9]+", "", text)
    if not marker or not any(ch.isdigit() for ch in marker):
        return None
    if marker.isdigit():
        try:
            return str(int(marker))
        except ValueError:
            return None
    alnum_match = re.match(r"^([A-Z]+)(\d+)$", marker)
    if alnum_match:
        prefix, digits = alnum_match.groups()
        try:
            return f"{prefix}{int(digits)}"
        except ValueError:
            return None
    return marker


def _extract_discogs_position_markers(value: Any) -> set[str]:
    text = _to_str(value).upper()
    if not text:
        return set()

    markers: set[str] = set()
    for match in _TRACK_VIDEO_POSITION_TOKEN_RE.finditer(text):
        marker = _normalize_discogs_position_marker(match.group(1))
        if marker:
            markers.add(marker)
    return markers


def _extract_discogs_track_type(track_obj: Any) -> str:
    if isinstance(track_obj, Mapping):
        return _to_str(track_obj.get("type_") or track_obj.get("type")).lower()

    direct_type = _to_str(
        getattr(track_obj, "type_", None) or getattr(track_obj, "type", None)
    ).lower()
    if direct_type:
        return direct_type

    data = getattr(track_obj, "data", None)
    if isinstance(data, Mapping):
        return _to_str(data.get("type_") or data.get("type")).lower()
    return ""


def _extract_discogs_sub_tracks(track_obj: Any) -> List[Any]:
    if isinstance(track_obj, Mapping):
        sub_tracks = track_obj.get("sub_tracks")
        if isinstance(sub_tracks, list):
            return sub_tracks
        return []

    data = getattr(track_obj, "data", None)
    if isinstance(data, Mapping):
        sub_tracks = data.get("sub_tracks")
        if isinstance(sub_tracks, list):
            return sub_tracks

    nested = getattr(track_obj, "sub_tracks", None)
    if isinstance(nested, list):
        return nested
    return []


def _extract_discogs_video_rows(release: Any) -> List[Dict[str, Any]]:
    raw_videos = getattr(release, "videos", None) or []
    release_artist_clues = _extract_discogs_release_artist_clues(release)
    rows: List[Dict[str, Any]] = []
    seen_video_keys: set[tuple[str, str]] = set()
    for video in raw_videos:
        if isinstance(video, Mapping):
            title = _to_str(video.get("title"))
            url = _to_optional_str(video.get("uri") or video.get("url"))
        else:
            title = _to_str(getattr(video, "title", ""))
            url = _to_optional_str(
                getattr(video, "uri", None) or getattr(video, "url", None)
            )
        if not title or not url or _is_discogs_collection_video_title(title):
            continue

        normalized_title = _normalize_discogs_title_for_match(title)
        video_key = (normalized_title, url)
        if video_key in seen_video_keys:
            continue
        seen_video_keys.add(video_key)

        core_candidates = _discogs_video_track_core_candidates(
            title,
            release_artist_clues=release_artist_clues,
        )
        primary_core_title = core_candidates[0] if core_candidates else ""
        rows.append(
            {
                "title": title,
                "url": url,
                "normalized_title": normalized_title,
                "base_title": _discogs_base_title(primary_core_title),
                "base_titles": {
                    _discogs_base_title(candidate)
                    for candidate in core_candidates
                    if _discogs_base_title(candidate)
                },
                "tokens": _discogs_match_tokens(title),
                "core_tokens": _discogs_match_tokens(primary_core_title),
                "core_token_sets": [
                    _discogs_match_tokens(candidate)
                    for candidate in core_candidates
                    if _discogs_match_tokens(candidate)
                ],
                "is_single_track": _is_discogs_single_track_video_title(
                    title,
                    release_artist_clues=release_artist_clues,
                ),
                "part_number": _extract_discogs_part_number(title),
                "position_markers": _extract_discogs_position_markers(title),
            }
        )
    return rows


def _match_discogs_video_url_for_track(
    track_title: str,
    videos: List[Dict[str, Any]],
    *,
    used_indexes: set[int],
    track_position: Optional[str] = None,
) -> Optional[str]:
    if not track_title or not videos:
        return None

    track_base = _discogs_base_title(track_title)
    track_tokens = _discogs_match_tokens(track_title)
    track_part = _extract_discogs_part_number(track_title)
    track_position_marker = _normalize_discogs_position_marker(track_position)

    def _prioritize_by_position(
        rows: List[tuple[int, Dict[str, Any]]],
    ) -> List[tuple[int, Dict[str, Any]]]:
        if not track_position_marker:
            return rows
        position_matched: List[tuple[int, Dict[str, Any]]] = []
        for index, video in rows:
            markers = video.get("position_markers")
            if isinstance(markers, set) and track_position_marker in markers:
                position_matched.append((index, video))
        return position_matched or rows

    candidates: List[tuple[int, Dict[str, Any]]] = []
    for index, video in enumerate(videos):
        if index in used_indexes:
            continue
        base_titles = video.get("base_titles")
        if isinstance(base_titles, set):
            if track_base not in base_titles:
                continue
        elif video.get("base_title") != track_base:
            continue
        candidates.append((index, video))
    if not candidates:
        fuzzy_candidates: List[tuple[int, Dict[str, Any]]] = []
        for index, video in enumerate(videos):
            if index in used_indexes:
                continue
            video_token_sets = video.get("core_token_sets")
            token_sets: List[set[str]] = []
            if isinstance(video_token_sets, list):
                token_sets = [
                    token_set
                    for token_set in video_token_sets
                    if isinstance(token_set, set)
                ]
            else:
                video_tokens = video.get("core_tokens")
                if isinstance(video_tokens, set):
                    token_sets = [video_tokens]

            if track_tokens and any(
                track_tokens.issubset(token_set) for token_set in token_sets
            ):
                fuzzy_candidates.append((index, video))
        fuzzy_candidates = _prioritize_by_position(fuzzy_candidates)
        if len(fuzzy_candidates) == 1 and bool(
            fuzzy_candidates[0][1].get("is_single_track")
        ):
            index, video = fuzzy_candidates[0]
            used_indexes.add(index)
            return _to_optional_str(video.get("url"))
        if len(fuzzy_candidates) > 1:
            single_track_candidates = [
                item
                for item in fuzzy_candidates
                if bool(item[1].get("is_single_track"))
            ]
            if len(single_track_candidates) == 1:
                index, video = single_track_candidates[0]
                used_indexes.add(index)
                return _to_optional_str(video.get("url"))
        return None

    candidates = _prioritize_by_position(candidates)

    if track_part is not None:
        for index, video in candidates:
            if video.get("part_number") == track_part:
                used_indexes.add(index)
                return _to_optional_str(video.get("url"))
        if track_part == 1:
            for index, video in candidates:
                if video.get("part_number") is None:
                    used_indexes.add(index)
                    return _to_optional_str(video.get("url"))
    else:
        for index, video in candidates:
            if video.get("part_number") is None:
                used_indexes.add(index)
                return _to_optional_str(video.get("url"))

    if len(candidates) == 1:
        index, video = candidates[0]
        used_indexes.add(index)
        return _to_optional_str(video.get("url"))

    return None


def _normalize_common_fields(dst: Dict[str, Any], src: Mapping[str, Any]) -> None:
    """
    Нормализует общий набор полей в словаре назначения dst на основе src.
    Устраняет дублирующийся код между адаптерами.
    """
    dst["title"] = _to_str(src.get("title"))
    dst["discogs_id"] = _to_int_or_none(src.get("discogs_id"))
    dst["label"] = _to_optional_str(src.get("label"))
    dst["catalog_number"] = _normalize_catalog_number_or_none(src.get("catalog_number"))
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
    if "structured_formats" in src:
        dst["structured_formats"] = _normalize_structured_rows(
            src.get("structured_formats")
        )


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
                "youtube_url": _to_optional_str(item.get("youtube_url")),
                "position_index": int(item.get("position_index") or index),
            }
        )
    return normalized


def _append_discogs_track_rows(
    *,
    track_obj: Any,
    tracks_src: List[Dict[str, Any]],
    video_rows: List[Dict[str, Any]],
    used_video_indexes: set[int],
) -> None:
    track_type = _extract_discogs_track_type(track_obj)

    if track_type == "track":
        if isinstance(track_obj, Mapping):
            title_track = _to_str(track_obj.get("title"))
            track_position = _to_str(track_obj.get("position"))
            track_duration = _to_optional_str(track_obj.get("duration"))
        else:
            title_track = _to_str(getattr(track_obj, "title", ""))
            track_position = _to_str(getattr(track_obj, "position", ""))
            track_duration = _to_optional_str(getattr(track_obj, "duration", None))

        if not title_track:
            return

        tracks_src.append(
            {
                "position": track_position,
                "title": title_track,
                "duration": track_duration,
                "youtube_url": _match_discogs_video_url_for_track(
                    title_track,
                    video_rows,
                    used_indexes=used_video_indexes,
                    track_position=track_position,
                ),
                "position_index": len(tracks_src) + 1,
            }
        )
        return

    for sub_track in _extract_discogs_sub_tracks(track_obj):
        _append_discogs_track_rows(
            track_obj=sub_track,
            tracks_src=tracks_src,
            video_rows=video_rows,
            used_video_indexes=used_video_indexes,
        )


def _normalize_discogs_carrier(value: Any) -> Optional[str]:
    text = _to_optional_str(value)
    if not text:
        return None
    return _CANONICAL_CARRIERS.get(text.upper(), text)


def _normalize_discogs_primary_format(value: Any) -> Optional[str]:
    text = _to_optional_str(value)
    if not text:
        return None
    if text.upper() == "LP":
        return '12"'
    return text


def _to_positive_int_or_default(value: Any, *, default: int = 1) -> int:
    parsed = _to_int_or_none(value)
    if parsed is None or parsed < 1:
        return default
    return parsed


def _normalize_structured_rows(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []

    rows: List[Dict[str, Any]] = []
    for index, row in enumerate(value, start=1):
        if not isinstance(row, Mapping):
            continue
        carrier = _to_optional_str(row.get("carrier"))
        format_name = _to_optional_str(row.get("format_name"))
        details = _to_optional_str(row.get("details"))
        if not any((carrier, format_name, details)):
            continue
        rows.append(
            {
                "variant_of_format": _to_positive_int_or_default(
                    row.get("variant_of_format") or row.get("sort_order"),
                    default=index,
                ),
                "carrier": carrier or "",
                "quantity": _to_positive_int_or_default(row.get("quantity"), default=1),
                "format_name": format_name or "",
                "details": details or "",
            }
        )
    return rows


def _build_discogs_structured_formats(raw_formats: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_formats, (list, tuple)):
        return []

    rows: List[Dict[str, Any]] = []
    for index, raw_format in enumerate(raw_formats, start=1):
        if not isinstance(raw_format, Mapping):
            continue
        carrier = _normalize_discogs_carrier(raw_format.get("name"))
        descriptions = _to_str_list(raw_format.get("descriptions"))
        format_name = _normalize_discogs_primary_format(
            descriptions[0] if descriptions else None
        )
        details_parts = descriptions[1:] if len(descriptions) > 1 else []
        free_text = _to_optional_str(raw_format.get("text"))
        if free_text:
            details_parts.append(free_text)
        details = ", ".join(part.strip() for part in details_parts if part.strip())

        if not any((carrier, format_name, details)):
            continue

        rows.append(
            {
                "variant_of_format": index,
                "carrier": carrier or "",
                "quantity": _to_positive_int_or_default(
                    raw_format.get("qty"), default=1
                ),
                "format_name": format_name or "",
                "details": details,
            }
        )
    return rows


def adapt_redeye_payload(raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Метод нормализует словарь, полученный от RedeyeService, к формату сборки записи.
    """
    if not raw_payload:
        _log_payload_adapter_event(
            NOTICE_LEVEL,
            "redeye_payload_empty",
            "Пустой payload Redeye: адаптация пропущена.",
        )
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

    _log_payload_adapter_event(
        logging.DEBUG,
        "redeye_payload_adapted",
        "Payload Redeye нормализован.",
        title=out.get("title") or "—",
        artists_total=len(out.get("artists", [])),
        tracks_total=len(out.get("tracks", [])),
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
        name = _normalize_discogs_entity_name(getattr(artist_obj, "name", ""))
        if name:
            src["artists"].append(name)

    # Лейбл (первый)
    labels = getattr(release, "labels", []) or []
    if labels:
        first_label = labels[0]
        if isinstance(first_label, Mapping):
            src["label"] = _normalize_discogs_entity_name(first_label.get("name"))
            catno = _to_optional_str(first_label.get("catno"))
        else:
            src["label"] = _normalize_discogs_entity_name(
                getattr(first_label, "name", None)
            )
            catno = _to_optional_str(getattr(first_label, "catno", None))
        if not src["catalog_number"]:
            src["catalog_number"] = catno

    raw_formats = getattr(release, "formats", None) or []
    if not raw_formats:
        data = getattr(release, "data", None)
        raw_formats = _mapping_get(data, "formats") or []
    structured_formats = _build_discogs_structured_formats(raw_formats)
    src["structured_formats"] = structured_formats

    # Треки (position_index = порядковый номер)
    video_rows = _extract_discogs_video_rows(release)
    used_video_indexes: set[int] = set()
    tracks_src: List[Dict[str, Any]] = []
    for track_obj in getattr(release, "tracklist", []) or []:
        _append_discogs_track_rows(
            track_obj=track_obj,
            tracks_src=tracks_src,
            video_rows=video_rows,
            used_video_indexes=used_video_indexes,
        )
    src["tracks"] = tracks_src

    out: Dict[str, Any] = {}
    _normalize_common_fields(out, src)
    out["tracks"] = _normalize_tracks(src.get("tracks"))

    _log_payload_adapter_event(
        logging.DEBUG,
        "discogs_release_adapted",
        "Release Discogs нормализован.",
        title=out.get("title") or "—",
        artists_total=len(out.get("artists", [])),
        tracks_total=len(out.get("tracks", [])),
        structured_formats_total=len(out.get("structured_formats", [])),
    )
    return out


def adapt_discogs_payload(raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Метод нормализует уже «плоский» словарь, полученный от
    DiscogsService.extract_release_data(...), к формату сборки записи.
    """
    src: Dict[str, Any] = dict(raw_payload)
    src["label"] = _normalize_discogs_entity_name(src.get("label"))
    src["artists"] = [
        name
        for name in (
            _normalize_discogs_entity_name(item)
            for item in _to_str_list(src.get("artists"))
        )
        if name
    ]
    formats_value = src.get("formats")
    if isinstance(formats_value, list) and any(
        isinstance(item, Mapping) for item in formats_value
    ):
        structured_formats = _build_discogs_structured_formats(formats_value)
        src["structured_formats"] = structured_formats
    out: Dict[str, Any] = {}
    _normalize_common_fields(out, src)
    out["tracks"] = _normalize_tracks(src.get("tracks"))
    _log_payload_adapter_event(
        logging.DEBUG,
        "discogs_payload_adapted",
        "Payload Discogs нормализован.",
        title=out.get("title") or "—",
        artists_total=len(out.get("artists", [])),
        tracks_total=len(out.get("tracks", [])),
        structured_formats_total=len(out.get("structured_formats", [])),
    )
    return out
