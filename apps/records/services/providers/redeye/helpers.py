from __future__ import annotations
"""
Вспомогательные функции провайдера Redeye.

Содержимое:
  - text_join(node):        собирает текст узла с нормализацией пробелов;
  - page_text(soup):        возвращает нормализованный текст всей страницы;
  - normalize_abs_url(url): приводит относительный URL к абсолютному;
  - validate_redeye_product_url(url): валидация URL карточки Redeye;
  - parse_expected_date_parts_from_text(text): парсинг даты предзаказа 'Expected 24 Oct 2025';
  - format_expected_date_ru(year, month, day): форматирование даты на русском.
"""

from typing import Iterable, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from records.constants import REDEYE_BASE_URL, REDEYE_HOST, MONTHS_EN_TO_NUM, MONTHS_EN_TO_RU_GENITIVE


def text_join(node: object) -> str:
    """
    Метод собирает текст из узла BeautifulSoup с нормализацией пробелов.

    Args:
        node: Узел/фрагмент дерева BeautifulSoup.

    Returns:
        Строка с нормализованными пробелами.
    """
    parts: Iterable[str] = getattr(node, "stripped_strings", []) or []
    return " ".join(s.strip() for s in parts)


def page_text(soup: BeautifulSoup) -> str:
    """
    Метод возвращает нормализованный текст всей страницы (все stripped_strings).

    Args:
        soup: Корневой объект BeautifulSoup.

    Returns:
        Строка — конкатенация всех текстовых узлов страницы.
    """
    return " ".join(soup.stripped_strings)


def normalize_abs_url(url: str) -> str:
    """
    Метод приводит относительный URL к абсолютному с учётом REDEYE_BASE_URL.

    Args:
        url: Исходный URL (абсолютный, //, /path, относительный).

    Returns:
        Абсолютный URL или исходная строка, если она уже абсолютная.
    """
    u = (url or "").strip()
    if not u:
        return u
    if u.startswith("http"):
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return urljoin(REDEYE_BASE_URL, u)
    return urljoin(REDEYE_BASE_URL + "/", u)


def validate_redeye_product_url(url: str) -> None:
    """
    Метод выполняет быструю валидацию URL карточки Redeye.

    Правила:
      - URL обязателен (не пустой).
      - Хост ровно www.redeyerecords.co.uk.
      - Схема допускается http или https.

    Args:
        url (str): Проверяемый URL карточки.

    Raises:
        ValueError: Если URL пустой или нарушает правила.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL карточки Redeye не задан.")

    scheme, netloc, _path, _q, _f = urlsplit(raw)
    if netloc != REDEYE_HOST:
        raise ValueError(f"Ожидался хост {REDEYE_HOST}, получено: {netloc or '—'}.")

    if scheme and scheme not in {"http", "https"}:
        raise ValueError(f"Недопустимая схема URL: {scheme}. Ожидается http/https.")

def parse_expected_date_parts_from_text(text: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Метод извлекает из произвольного текста дату формата
    'Expected 24 Oct 2025' → (year, month, day), где month — число (1..12).

    Args:
        text: Произвольный текст страницы.

    Returns:
        (year, month, day) — каждое значение либо int, либо None.
    """
    import re

    m = re.search(r"Expected\s+(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})", text, re.I)
    if not m:
        return None, None, None
    day = int(m.group(1))
    mon_raw = m.group(2).lower().strip(".")
    year = int(m.group(3))
    return year, MONTHS_EN_TO_NUM.get(mon_raw[:3]), day


def format_expected_date_ru(year: int, month: int, day: int) -> Optional[str]:
    """
    Метод форматирует дату предзаказа в русской форме: '24 октября 2025 года'.

    Args:
        year:  Год.
        month: Номер месяца (1..12).
        day:   День месяца.

    Returns:
        Строка формата 'D <месяц в родительном падеже> YYYY года' либо None, если месяц неизвестен.
    """
    inv = {v: k for k, v in MONTHS_EN_TO_NUM.items()}
    mon_key = inv.get(month)
    if not mon_key:
        return None
    mon_ru = MONTHS_EN_TO_RU_GENITIVE.get(mon_key)
    return f"{day} {mon_ru} {year} года" if mon_ru else None
