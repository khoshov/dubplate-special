from __future__ import annotations

"""
Адаптеры (нормализаторы) payload-данных внешних провайдеров (Redeye, Discogs и др.).

Задача модуля — привести «сырой» словарь, полученный от конкретного источника,
к единому внутреннему контракту, который понимает `record_assembly.build_record_from_payload()`.

Контракт полей см. в модуле `record_assembly.py`.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def adapt_redeye_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Метод нормализует payload, полученный от RedeyeService, в формат,
    пригодный для сборки записи (`record_assembly.build_record_from_payload`).

    Приводимые элементы:
      - catalog_number: верхний регистр, без лишних пробелов;
      - title, label, notes: str.strip();
      - price_gbp: Decimal/float → строка "12.34" или None;
      - release_year/month/day: int или None;
      - tracks: список словарей (title, duration, position, position_index);
      - artists, genres, styles, formats: гарантированно списки строк;
      - image_url/source остаются без изменений (используются вне сборки).

    Args:
        raw: Словарь, возвращённый `RedeyeService.parse_product_page`.

    Returns:
        Dict[str, Any]: нормализованный словарь данных.
    """
    if not raw:
        logger.warning("adapt_redeye_payload: пустой входной словарь.")
        return {}

    payload: Dict[str, Any] = dict(raw)  # копия, чтобы не трогать оригинал

    def _clean_str(val: Any) -> str:
        return val.strip() if isinstance(val, str) else ""

    def _clean_or_none(val: Any) -> Optional[str]:
        s = _clean_str(val)
        return s or None

    def _as_list(val: Any) -> list[str]:
        if isinstance(val, (list, tuple)):
            out = [str(x).strip() for x in val if isinstance(x, str) and x.strip()]
            return out
        if isinstance(val, str):
            return [val.strip()] if val.strip() else []
        return []

    def _as_int_or_none(val: Any) -> Optional[int]:
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    def _as_price_str(val: Any) -> Optional[str]:
        if val is None:
            return None
        try:
            return f"{float(val):.2f}"
        except (TypeError, ValueError):
            return None

    # --- нормализация ключевых полей ---
    payload["title"] = _clean_str(payload.get("title"))
    payload["label"] = _clean_or_none(payload.get("label"))
    payload["catalog_number"] = (
            _clean_str(payload.get("catalog_number") or "").upper() or None
    )
    payload["barcode"] = _clean_or_none(payload.get("barcode"))
    payload["country"] = _clean_or_none(payload.get("country"))
    payload["notes"] = _clean_or_none(payload.get("notes"))

    payload["price_gbp"] = _as_price_str(payload.get("price_gbp"))
    payload["release_year"] = _as_int_or_none(payload.get("release_year") or payload.get("year"))
    payload["release_month"] = _as_int_or_none(payload.get("release_month"))
    payload["release_day"] = _as_int_or_none(payload.get("release_day"))

    payload["artists"] = _as_list(payload.get("artists"))
    payload["genres"] = _as_list(payload.get("genres"))
    payload["styles"] = _as_list(payload.get("styles"))
    payload["formats"] = _as_list(payload.get("formats"))

    # --- треки ---
    tracks = payload.get("tracks")
    if not isinstance(tracks, list):
        payload["tracks"] = []
    else:
        norm_tracks: list[dict[str, Any]] = []
        for idx, t in enumerate(tracks, start=1):
            if not isinstance(t, dict):
                continue
            title = _clean_str(t.get("title"))
            if not title:
                continue
            norm_tracks.append(
                {
                    "position": _clean_str(t.get("position")),
                    "title": title,
                    "duration": _clean_or_none(t.get("duration")),
                    "position_index": int(t.get("position_index") or idx),
                }
            )
        payload["tracks"] = norm_tracks

    logger.debug(
        "adapt_redeye_payload: нормализовано: title=%r, artists=%d, tracks=%d",
        payload.get("title"),
        len(payload.get("artists", [])),
        len(payload.get("tracks", [])),
    )

    return payload


def adapt_discogs_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Заглушка под будущую реализацию адаптера для Discogs.
    Возвращает входной словарь без изменений.
    """
    logger.debug("adapt_discogs_payload: пока без изменений.")
    return dict(raw)
