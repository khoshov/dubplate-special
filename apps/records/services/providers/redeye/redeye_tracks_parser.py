"""
Парсер списка треков для источника Redeye.

Задача:
    Метод реализует извлечение треклиста из блока `.tracks` карточки товара и
    возвращает устойчивый список структур TrackPayload:
        {
            "position": str,            # 'A1', 'B2' или '' если сторон/позиций нет
            "title": str,               # нормализованное название трека
            "duration": Optional[str],  # 'mm:ss' если найдено, иначе None
            "position_index": int,      # натуральная нумерация 1..N, независимая от сторон
        }

Поддерживаемые варианты вёрстки:
    1) Многострочный список через <br>:
       "A1 Flow Key 06:19<br>A2 Reso 02 05:58<br>..."
    2) Одна строка, треки разделены слэшем:
       "Moon Cruise / Never Stop / ..."
    3) Пунктуация после позиции:
       "A1. Title", "A1) Title", "A1 - Title", "1. Title", "2) Title"
    4) Заголовки сторон ("Side A", "A", "Disc 1", "CD 2") игнорируются.

Доп. эвристика:
    Если распознан ровно один трек без позиции, из его title удаляется префикс вида
    "A.", "B.", "Side A" и т.п. — это исправляет страницы, где треклист в <h2 class="tracks">A. Title ...</h2>.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Sequence, TypedDict, Union

from bs4 import BeautifulSoup
from bs4.element import Tag

from .helpers import text_join

logger = logging.getLogger(__name__)

# Заголовки сторон: "Side A", "A", "Disc 1", "CD 2"
RX_SIDE = re.compile(r"^\s*(?:side\s+[A-D]|[A-D]|disc\s+\d+|cd\s*\d+)\s*$", re.I)

# Удаление префикса стороны в title, если трек один и позиция не распознана
RX_SIDE_PREFIX_IN_TITLE = re.compile(
    r"^\s*(?:side\s+[A-D]|[A-D]|disc\s+\d+|cd\s*\d+)\s*[.) :\-–—]+\s*", re.I
)

# Позиция с буквой и номером: "A1. Title" / "A1) Title" / "A1 - Title"
RX_POS_ALPHA = re.compile(r"^\s*([A-D]\d{1,2})\s*[.) :\-–—]?\s+(.*)$", re.I)

# Чисто числовая позиция: "1. Title" / "2) Title"
RX_POS_NUM = re.compile(r"^\s*(\d{1,2})[.)]\s+(.*)$")

# Встроенные маркеры позиций в одной строке: "A1. ... A2. ... B1. ..."
RX_INLINE_POS = re.compile(r"\b(?:[A-D]\d{1,2}(?:[.) :\-–—]?\s+)|\d{1,2}[.)]\s+)", re.I)

# Длительность в конце строки (опционально)
RX_DURATION = re.compile(r"^(.*?)(?:\s+(\d{1,2}:\d{2}))?$")


class TrackPayload(TypedDict):
    """Структура трека, возвращаемая парсером Redeye."""

    position: str
    title: str
    duration: Optional[str]
    position_index: int


def _synthesize_from_player(sample_buttons: Sequence[Tag]) -> List[TrackPayload]:
    """
    Метод создаёт N плейсхолдеров-треков по числу найденных кнопок плеера.

    Позиции оставляются пустыми (""), индексы — 1..N, чтобы сопоставление по index было стабильным.

    Args:
        sample_buttons: Список тегов кнопок плеера с атрибутом data-sample.

    Returns:
        Список TrackPayload длины N.
    """
    items: List[TrackPayload] = []
    for index, _ in enumerate(sample_buttons, start=1):
        items.append(
            TrackPayload(
                position="",
                title=f"Untitled {index}",
                duration=None,
                position_index=index,
            )
        )
    return items


def _html_lines_to_tracks(html_fragment: str) -> List[TrackPayload]:
    """
    Метод превращает HTML фрагмент внутри `.tracks` в список треков.

    Обрабатывает как случаи со слэшами в одну строку, так и многострочный список через <br>.
    """
    # ── Кейс: одна строка со слэшами ────────────────────────────────────────────
    if "<br" not in html_fragment and "/" in html_fragment:
        parts = [
            part.strip(" \t\r\n-–—")
            for part in html_fragment.split("/")
            if part.strip()
        ]
        items: List[TrackPayload] = []
        for index, part in enumerate(parts, start=1):
            text = text_join(BeautifulSoup(part, "html.parser"))
            if not text:
                continue
            items.append(
                TrackPayload(
                    position="",
                    title=text,
                    duration=None,
                    position_index=index,
                )
            )
        return items

    # ── Нормализуем <br> в переносы строк и вытаскиваем текст ──────────────────
    normalized = html_fragment
    for token in ("<br>", "<br/>", "<br />"):
        normalized = normalized.replace(token, "\n")
    text_block = text_join(BeautifulSoup(normalized, "html.parser"))
    lines = [line.strip() for line in text_block.splitlines() if line.strip()]
    if not lines:
        return []

    # Разбор строк с позициями/длительностями
    parsed: List[Dict[str, Optional[str]]] = []

    def _split_inline_position_chunks(line: str) -> List[str]:
        """
        Делит одну строку на несколько, если позиции треков записаны inline.

        Пример:
            "A1. Foo A2. Bar B1. Baz" -> ["A1. Foo", "A2. Bar", "B1. Baz"]
        """
        normalized_line = re.sub(r"\s+", " ", line).strip()
        markers = list(RX_INLINE_POS.finditer(normalized_line))
        if len(markers) < 2:
            return [line]
        if markers[0].start() != 0:
            return [line]

        chunks: List[str] = []
        for idx, marker in enumerate(markers):
            start = marker.start()
            end = (
                markers[idx + 1].start()
                if idx + 1 < len(markers)
                else len(normalized_line)
            )
            chunk = normalized_line[start:end].strip(" \t\r\n-–—")
            if chunk:
                chunks.append(chunk)
        return chunks or [line]

    for line in lines:
        for chunk in _split_inline_position_chunks(line):
            # Пропускаем заголовки сторон пластинки
            if RX_SIDE.match(chunk):
                continue

            match = RX_POS_ALPHA.match(chunk) or RX_POS_NUM.match(chunk)
            if match:
                position = match.group(1).strip()
                tail = match.group(2).strip()
            else:
                position = ""
                tail = chunk

            md = RX_DURATION.fullmatch(tail)
            if md:
                title = (md.group(1) or "").strip()
                duration = ((md.group(2) or "").strip()) or None
            else:
                title = tail
                duration = None

            if title:
                parsed.append(
                    {"position": position, "title": title, "duration": duration}
                )

    # Дедуп по (position, title) с приоритетом варианта, где есть duration
    dedup: Dict[tuple[str, str], Dict[str, Optional[str]]] = {}
    for row in parsed:
        key = ((row.get("position") or "").lower(), (row.get("title") or "").lower())
        if key not in dedup or (row.get("duration") and not dedup[key].get("duration")):
            dedup[key] = row

    items: List[TrackPayload] = []
    for index, row in enumerate(dedup.values(), start=1):
        items.append(
            TrackPayload(
                position=(row.get("position") or ""),
                title=row["title"] or "",
                duration=(row.get("duration") or None),
                position_index=index,
            )
        )
    return items


def parse_redeye_tracks(soup_or_html: Union[BeautifulSoup, str]) -> List[TrackPayload]:
    """
    Метод разбирает список треков Redeye в список TrackPayload.

    Поведение:
        - Ищет блок `.tracks` в переданном HTML/BS-супе и парсит его содержимое.
        - Если `.tracks` не найден или пуст, но на странице видны кнопки плеера —
          генерирует плейсхолдеры по числу кнопок, чтобы корректно сопоставлять аудио «по порядку».
        - Гарантирует `position_index` как 1..N.

    Args:
        soup_or_html: BeautifulSoup документа ИЛИ HTML-строка с разметкой карточки.

    Returns:
        List[TrackPayload]: Устойчивый список треков.
    """
    soup = (
        BeautifulSoup(soup_or_html, "html.parser")
        if isinstance(soup_or_html, str)
        else soup_or_html
    )

    sample_buttons: List[Tag] = list(
        soup.select(".play a.btn-play[data-sample], .btn-play[data-sample]")
    )
    has_audio_previews = bool(sample_buttons)

    tracks_node: Optional[Tag] = soup.find(attrs={"class": "tracks"})  # type: ignore[assignment]
    if not tracks_node:
        logger.debug("parse_redeye_tracks: .tracks не найден")
        if has_audio_previews:
            synthesized = _synthesize_from_player(sample_buttons)
            logger.debug(
                "parse_redeye_tracks: синтезировано %d треков по кнопкам плеера (нет .tracks)",
                len(synthesized),
            )
            return synthesized
        return []

    html_fragment = tracks_node.decode_contents()
    items = _html_lines_to_tracks(html_fragment)

    if not items:
        logger.debug("parse_redeye_tracks: .tracks пуст после нормализации")
        if has_audio_previews:
            synthesized = _synthesize_from_player(sample_buttons)
            logger.debug(
                "parse_redeye_tracks: синтезировано %d треков по кнопкам плеера (пустой .tracks)",
                len(synthesized),
            )
            return synthesized
        return []

    # Эвристика: один трек без позиции → убираем префикс "A." / "B." / "Side A" из title
    if len(items) == 1 and not items[0].get("position"):
        cleaned = RX_SIDE_PREFIX_IN_TITLE.sub("", items[0]["title"]).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)  # прибираем двойные пробелы
        items[0]["title"] = cleaned

    def _is_empty_title(title: str) -> bool:
        normalized = (title or "").strip().lower()
        return normalized in ("", "-", "—", "n/a", "untitled", "unknown")

    if has_audio_previews:
        titles = [track.get("title") or "" for track in items]
        if titles and all(_is_empty_title(t) for t in titles):
            synthesized = _synthesize_from_player(sample_buttons)
            logger.debug(
                "parse_redeye_tracks: все названия пустые — синтезировано %d треков по кнопкам",
                len(synthesized),
            )
            return synthesized

    logger.debug("parse_redeye_tracks: распознано %d треков", len(items))
    return items


__all__ = ["TrackPayload", "parse_redeye_tracks"]
