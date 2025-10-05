"""
Парсер списка треков для источника Redeye.

Назначение
----------
Принять HTML/BeautifulSoup карточки товара Redeye, найти блок `.tracks`,
разобрать строки в устойчивый список словарей одного вида:

    {
        "position": str,          # 'A1', 'B2' или '' если сторон нет
        "title": str,             # нормализованный заголовок трека
        "duration": Optional[str],# 'mm:ss' если найдено, иначе None
        "position_index": int,    # натуральная нумерация 1..N, независимая от сторон
    }

Поддерживаемые варианты вёрстки
-------------------------------
1) Многострочный список через <br>:
   "A1 Flow Key 06:19<br>A2 Reso 02 05:58<br>..."
2) Одна строка, треки разделены слэшем:
   "Moon Cruise / Never Stop / ..."
3) Форматы с пунктуацией после позиции:
   "A1. Title", "A1) Title", "A1 - Title", "1. Title", "2) Title"
4) Заголовки сторон ("Side A", "A", "side B") игнорируются.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, TypedDict, Union

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class TrackPayload(TypedDict):
    position: str
    title: str
    duration: Optional[str]
    position_index: int


def _text_with_sep(soup_or_tag, sep: str) -> str:
    """Извлекает текст, соединяя текстовые узлы заданным разделителем, с триммингом."""
    return sep.join([s.strip() for s in getattr(soup_or_tag, "stripped_strings", [])])


def parse_redeye_tracks(soup_or_html: Union[BeautifulSoup, str]) -> List[TrackPayload]:
    """
    Разбирает список треков Redeye в список TrackPayload.

    Args:
        soup_or_html: BeautifulSoup документа ИЛИ HTML-строка с блоком `.tracks`.

    Returns:
        Список словарей TrackPayload. Всегда с проставленными индексами 1..N.
    """
    soup = BeautifulSoup(soup_or_html, "html.parser") if isinstance(soup_or_html, str) else soup_or_html
    node = soup.find(attrs={"class": "tracks"})
    if not node:
        logger.debug("parse_redeye_tracks: .tracks not found")
        return []

    # сырой html нужен, чтобы поймать все варианты <br>, <br/>, <br />
    html = node.decode_contents()

    # ── одна строка со слэшами ────────────────────────────────────────────
    if "<br" not in html and "/" in html:
        parts = [p.strip(" \t\r\n-–—") for p in html.split("/") if p.strip()]
        items: List[TrackPayload] = []
        for i, p in enumerate(parts, start=1):
            txt = _text_with_sep(BeautifulSoup(p, "html.parser"), " ")
            if not txt:
                continue
            items.append(
                {
                    "position": "",
                    "title": txt,
                    "duration": None,
                    "position_index": i,
                }
            )
        logger.debug("parse_redeye_tracks: slash-style parsed %d tracks", len(items))
        return items

    # ── иначе нормализуем <br> в переносы строк и вытаскиваем текст ─────────────
    for token in ("<br>", "<br/>", "<br />"):
        html = html.replace(token, "\n")
    # Получаем текст с переводами строк как разделителями
    text = _text_with_sep(BeautifulSoup(html, "html.parser"), "\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        logger.debug("parse_redeye_tracks: empty lines after normalization")
        return []

    # регулярные выражения для основных случаев
    rx_side = re.compile(r"^\s*(?:side\s+)?([A-D])\s*$", re.I)  # Side A / A / side B
    # в [] дефис ставим в конец, ':' не экранируем
    rx_pos_alpha = re.compile(r"^\s*([A-D]\d{1,2})[.) :-]?\s+(.*)$", re.I)  # A1. Title / A1) Title / A1 - Title
    rx_pos_num = re.compile(r"^\s*(\d{1,2})[.)]\s+(.*)$")  # 1. Title / 2) Title
    rx_duration = re.compile(r"^(.*?)(?:\s+(\d{1,2}:\d{2}))?$")  # optional mm:ss в конце

    parsed: List[Dict[str, Optional[str]]] = []
    for ln in lines:
        # убираем заголовки сторон пластинки
        if rx_side.match(ln):
            continue

        m = rx_pos_alpha.match(ln) or rx_pos_num.match(ln)
        if m:
            pos = m.group(1).strip()
            tail = m.group(2).strip()
        else:
            pos = ""
            tail = ln

        md = rx_duration.fullmatch(tail)
        if md:
            title = (md.group(1) or "").strip()
            duration = ((md.group(2) or "").strip()) or None
        else:
            title = tail
            duration = None

        if not title:
            continue

        parsed.append({"position": pos, "title": title, "duration": duration})

    # удаление дубликатов по position и title с приоритетом варианта, где есть длительность трека
    dedup: Dict[tuple[str, str], Dict[str, Optional[str]]] = {}
    for track in parsed:
        key = ((track.get("position") or "").lower(), (track.get("title") or "").lower())
        if key not in dedup or (track.get("duration") and not dedup[key].get("duration")):
            dedup[key] = track

    items: List[TrackPayload] = []
    for i, track in enumerate(dedup.values(), start=1):
        items.append(
            TrackPayload(
                position=(track.get("position") or ""),
                title=track["title"] or "",
                duration=(track.get("duration") or None),
                position_index=i,
            )
        )

    logger.debug("parse_redeye_tracks: parsed %d tracks", len(items))
    return items


__all__ = ["TrackPayload", "parse_redeye_tracks"]
