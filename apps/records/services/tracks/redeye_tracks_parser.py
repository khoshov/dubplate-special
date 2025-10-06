"""
Парсер списка треков для источника Redeye.

Задача
------
Найти блок `.tracks` в карточке товара и вернуть устойчивый список словарей:

    {
        "position": str,           # 'A1', 'B2' или '' если сторон/позиций нет
        "title": str,              # нормализованное название трека
        "duration": Optional[str], # 'mm:ss' если найдено, иначе None
        "position_index": int,     # натуральная нумерация 1..N, независимая от сторон
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

Доп. эвристика
--------------
Если распознан ровно один трек без позиции, из его title удаляется префикс
вида "A.", "B.", "Side A" и т.п. — это исправляет страницы, где весь
треклист воткнут одной строкой в <h2 class="tracks">A. Title ...</h2>.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Iterable, List, Optional, TypedDict, Union

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ──────────────────────────────── Регулярные выражения (на уровне модуля) ────────────────────────────────

# Заголовки сторон: "Side A", "A", "Disc 1", "CD 2"
# RX_SIDE = re.compile(r"^\s*(?:side\s+)?([A-D])\s*$", re.I)
RX_SIDE = re.compile(r"^\s*(?:side\s+[A-D]|[A-D]|disc\s+\d+|cd\s*\d+)\s*$", re.I)

# Удаление префикса стороны в title, если трек один и позиция не распознана.
# RX_SIDE_PREFIX_IN_TITLE = re.compile(r"^\s*(?:side\s+)?([A-D])\s*[.) :\-–—]\s*", re.I)
RX_SIDE_PREFIX_IN_TITLE = re.compile(
    r"^\s*(?:side\s+[A-D]|[A-D]|disc\s+\d+|cd\s*\d+)\s*[.) :\-–—]+\s*", re.I
)

# Позиция с буквой и номером, с опциональной пунктуацией: "A1. Title" / "A1) Title" / "A1 - Title"
RX_POS_ALPHA = re.compile(r"^\s*([A-D]\d{1,2})\s*[.) :\-–—]?\s+(.*)$", re.I)

# Чисто числовая позиция: "1. Title" / "2) Title"
RX_POS_NUM = re.compile(r"^\s*(\d{1,2})[.)]\s+(.*)$")

# Длительность в конце строки (опционально)
RX_DURATION = re.compile(r"^(.*?)(?:\s+(\d{1,2}:\d{2}))?$")


class TrackPayload(TypedDict):
    position: str
    title: str
    duration: Optional[str]
    position_index: int


def _text_with_sep(soup_or_tag: object, sep: str) -> str:
    """
    Собирает текст из узлов BeautifulSoup, соединяя элементы заданным разделителем.
    Работает через .stripped_strings, чтобы избежать спорных аргументов get_text().
    """
    parts: Iterable[str] = getattr(soup_or_tag, "stripped_strings", [])  # type: ignore[attr-defined]
    return sep.join(s.strip() for s in parts)


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

    # Берём сырой HTML, чтобы корректно обработать все варианты <br>, <br/>, <br />
    html = node.decode_contents()

    # ── Кейс: одна строка со слэшами ────────────────────────────────────────────
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

    # ── Иначе нормализуем <br> в переносы строк и вытаскиваем текст ─────────────
    for token in ("<br>", "<br/>", "<br />"):
        html = html.replace(token, "\n")
    text = _text_with_sep(BeautifulSoup(html, "html.parser"), "\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        logger.debug("parse_redeye_tracks: empty lines after normalization")
        return []

    parsed: List[Dict[str, Optional[str]]] = []
    for ln in lines:
        # Убираем заголовки сторон пластинки
        if RX_SIDE.match(ln):
            continue

        m = RX_POS_ALPHA.match(ln) or RX_POS_NUM.match(ln)
        if m:
            pos = m.group(1).strip()
            tail = m.group(2).strip()
        else:
            pos = ""
            tail = ln

        md = RX_DURATION.fullmatch(tail)
        if md:
            title = (md.group(1) or "").strip()
            duration = ((md.group(2) or "").strip()) or None
        else:
            title = tail
            duration = None

        if title:
            parsed.append({"position": pos, "title": title, "duration": duration})

    # Дедуп по (position, title) с приоритетом варианта, где есть duration
    dedup: Dict[tuple[str, str], Dict[str, Optional[str]]] = {}
    for t in parsed:
        key = ((t.get("position") or "").lower(), (t.get("title") or "").lower())
        if key not in dedup or (t.get("duration") and not dedup[key].get("duration")):
            dedup[key] = t

    items: List[TrackPayload] = []
    for i, t in enumerate(dedup.values(), start=1):
        items.append(
            TrackPayload(
                position=(t.get("position") or ""),
                title=t["title"] or "",
                duration=(t.get("duration") or None),
                position_index=i,
            )
        )

    # Эвристика: один трек без позиции → убираем префикс "A." / "B." / "Side A" из title
    if len(items) == 1 and not items[0].get("position"):
        cleaned = RX_SIDE_PREFIX_IN_TITLE.sub("", items[0]["title"]).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)  # прибираем двойные пробелы
        items[0]["title"] = cleaned

    logger.debug("parse_redeye_tracks: parsed %d tracks", len(items))
    return items


__all__ = ["TrackPayload", "parse_redeye_tracks"]
