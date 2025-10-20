from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import NavigableString

from .helpers import parse_expected_date_parts_from_text
from .helpers import text_join, page_text, normalize_abs_url
from .redeye_tracks_parser import parse_redeye_tracks, TrackPayload

logger = logging.getLogger(__name__)


class RedeyeProductParser:
    """
    Класс реализует разбор HTML карточки Redeye в словарь полей записи.

    Публичный метод:
        - parse(url, html_text) -> dict
    """

    PRICE_RE = re.compile(r"£\s*(\d+(?:\.\d{1,2})?)")

    def parse(self, url: str, html_text: str) -> Dict:
        """
        Метод разбирает HTML карточки Redeye в словарь с данными записи.

        Args:
            url (str): Абсолютный URL карточки.
            html_text (str): HTML страницы карточки.

        Returns:
            dict: Поля (title, artists, label, catalog_number, tracks, price_gbp, availability, image_url, ...).
        """
        soup = BeautifulSoup(html_text, "html.parser")

        title_text = self._extract_title(soup)
        artists, record_title = self._split_artist_title(title_text)
        label_name = (self._extract_label(soup) or "")[:255] or None
        catalog_number = self._extract_catalog_number(soup)
        price, availability = self._extract_price_and_availability(soup)

        full_text: str = page_text(soup)
        year, month, day = parse_expected_date_parts_from_text(full_text)

        image_url = self._extract_image_url(soup)

        tracks: List[TrackPayload] = parse_redeye_tracks(soup)

        has_audio_previews: bool = bool(
            soup.select_one(".play a.btn-play[data-sample]")
        )

        notes = (
            f"Цена пластинки на redeyerecords.co.uk составляет: {price:.2f} GBP"
            if price is not None
            else None
        )

        logger.info(
            "[Redeye] page parsed: title='%s' artists=%s label='%s' cat='%s' price=%s avail=%s img=%s has_audio_previews=%s",
            record_title or title_text,
            artists,
            label_name,
            catalog_number,
            price,
            availability,
            bool(image_url),
            has_audio_previews,
        )

        return {
            "title": record_title or title_text,
            "artists": artists,
            "label": label_name,
            "catalog_number": catalog_number,
            "barcode": None,
            "country": None,
            "year": None,
            "genres": [],
            "styles": [],
            "formats": [],
            "tracks": tracks or [],
            "price_gbp": f"{price}" if price is not None else None,
            "availability": availability,
            "image_url": image_url,
            "notes": notes,
            "release_year": year or None,
            "release_month": month or None,
            "release_day": day or None,
            "source": {"name": "redeye", "url": url},
            "has_audio_previews": has_audio_previews,
        }

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        header = soup.find("h1")
        if header:
            return text_join(header)
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return "Unknown Title"

    @staticmethod
    def _split_artist_title(full: str) -> Tuple[List[str], Optional[str]]:
        if " - " in full:
            artist_part, title_part = full.split(" - ", 1)
            # класс символов вместо альтернаций одного символа
            artists = [
                name.strip()
                for name in re.split(r"\s*[,&/]\s*", artist_part)
                if name.strip()
            ]
            return artists, title_part.strip()
        return [], full

    @staticmethod
    def _extract_label(soup: BeautifulSoup) -> Optional[str]:
        for paragraph in soup.find_all("p"):
            if not paragraph.contents:
                continue
            first_child = paragraph.contents[0]
            if (
                isinstance(first_child, NavigableString)
                and first_child.strip().lower() == "label"
            ):
                link = paragraph.find("a")
                if link and link.get_text(strip=True):
                    return link.get_text(strip=True)
                span = paragraph.find("span")
                if span and span.get_text(strip=True):
                    return span.get_text(strip=True)
                text = text_join(paragraph)
                tail = re.sub(r"^label\s*", "", text, flags=re.IGNORECASE).strip()
                return tail or None
        return None

    @staticmethod
    def _extract_catalog_number(soup: BeautifulSoup) -> Optional[str]:
        for paragraph in soup.find_all("p"):
            text = text_join(paragraph)
            if re.match(r"^catalogue\s+no\.?\b", text, re.IGNORECASE):
                span = paragraph.find("span")
                if span:
                    value = span.get_text(strip=True)
                    return value or None
                tail = re.sub(
                    r"^catalogue\s+no\.?\s*", "", text, flags=re.IGNORECASE
                ).strip()
                return tail or None
        return None

    def _extract_price_and_availability(
        self, soup: BeautifulSoup
    ) -> Tuple[Optional[Decimal], Optional[str]]:
        full_text = page_text(soup)

        match = re.search(
            r"£\s*(\d+(?:\.\d{1,2})?)\s*\(\s*£\s*\d+(?:\.\d{1,2})?\s*inc\.?\s*vat",
            full_text,
            re.I,
        )
        if match:
            price = Decimal(match.group(1))
        else:
            prices: List[Decimal] = []
            for value in re.findall(self.PRICE_RE, full_text):
                try:
                    prices.append(Decimal(value))
                except ValueError:
                    continue
            price = min(prices) if prices else None

        availability: Optional[str] = None
        lowered = full_text.lower()
        if "pre-order" in lowered or "expected" in lowered:
            availability = "preorder"
        elif "out of stock" in lowered:
            availability = "out_of_stock"
        elif "add to basket" in lowered or "in stock" in lowered:
            availability = "in_stock"

        return price, availability

    @staticmethod
    def _extract_image_url(soup: BeautifulSoup) -> Optional[str]:
        open_graph = soup.find("meta", attrs={"property": "og:image"})
        if open_graph and open_graph.get("content"):
            return normalize_abs_url(open_graph["content"])

        image = soup.select_one("img[src]")
        if image:
            return normalize_abs_url(str(image.get("src")))
        return None
