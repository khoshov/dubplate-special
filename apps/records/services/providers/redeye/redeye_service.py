from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urljoin, quote

from bs4 import BeautifulSoup
from records.constants import REDEYE_BASE_URL
from .http import RedeyeHTTPClient
from .page_product_scraper import RedeyeProductParser

logger = logging.getLogger(__name__)


@dataclass
class RedeyeFetchResult:
    """
    Класс описывает результат выборки карточки товара Redeye.

    Атрибуты:
        source_url: Страница товара Redeye, из которой извлечены данные.
        payload:    Словарь с распарсенными полями релиза (см. RedeyeProductParser).
    """
    source_url: str
    payload: Dict


class RedeyeService:
    """
    Сервис реализует получение и разбор карточек Redeye.

    Публичные методы:
        - fetch_by_catalog_number(catalog_number): ищет карточку по каталожному номеру.
        - parse_product_by_url(url): парсит карточку по прямому URL.
    """

    def __init__(self, http: Optional[RedeyeHTTPClient] = None) -> None:
        self.http = http or RedeyeHTTPClient()
        self.parser = RedeyeProductParser()

    def fetch_by_catalog_number(self, catalog_number: str) -> RedeyeFetchResult:
        """
        Метод получает карточку товара по каталожному номеру.

        Шаги:
            1) Находит URL карточки через поисковую страницу.
            2) Запрашивает HTML карточки.
            3) Делегирует разбор в RedeyeProductParser и возвращает результат.
        """
        cat = (catalog_number or "").strip()
        if not cat:
            raise ValueError("Catalogue number is required.")

        logger.info("[Redeye] search by CAT: '%s'", cat)
        product_url = self._product_page_url_by_catalog_number(cat)
        if not product_url:
            raise ValueError(f"Redeye: release not found by Catalogue No. '{cat}'")

        abs_url = product_url if re.match(r"^https?://", product_url, re.I) else urljoin(REDEYE_BASE_URL, product_url)
        logger.info("[Redeye] opening product: %s", abs_url)
        html_text = self.http.get_text(abs_url)

        payload = self.parser.parse(abs_url, html_text)

        parsed_cat = (payload.get("catalog_number") or "").strip().upper()
        req_cat = cat.upper()
        if not parsed_cat:
            payload["catalog_number"] = req_cat
        elif parsed_cat != req_cat:
            logger.warning("[Redeye] CAT mismatch: requested '%s' vs parsed '%s' (%s)", req_cat, parsed_cat, abs_url)

        return RedeyeFetchResult(source_url=abs_url, payload=payload)

    def parse_redeye_product_by_url(self, url: str) -> RedeyeFetchResult:
        """Метод получает карточку товара по прямому URL и возвращает распарсенные поля."""
        if not url:
            raise ValueError("Product URL is required.")

        abs_url = url if re.match(r"^https?://", url, re.I) else urljoin(REDEYE_BASE_URL, url)
        logger.info("[Redeye] opening product by URL: %s", abs_url)
        html_text = self.http.get_text(abs_url)
        payload = self.parser.parse(abs_url, html_text)
        return RedeyeFetchResult(source_url=abs_url, payload=payload)


    def _product_page_url_by_catalog_number(self, catalog_number: str) -> Optional[str]:
        """
        Метод возвращает URL карточки по каталожному номеру.

        Алгоритм:
            - Запрашивает страницу поиска с параметрами searchType=CAT&keywords=<CAT>.
            - Извлекает первую ссылку, ведущую на /vinyl/....
        """


        search_url = f"{REDEYE_BASE_URL}/search/?searchType=CAT&keywords={quote(catalog_number)}"
        logger.info("[Redeye] search URL: %s", search_url)

        html_text = self.http.get_text(search_url, referer=f"{REDEYE_BASE_URL}/", slow=True)
        soup = BeautifulSoup(html_text, "html.parser")

        for link in soup.select('a[href*="/vinyl/"]'):
            href = link.get("href", "")
            if not href:
                continue
            return href if href.startswith("http") else urljoin(REDEYE_BASE_URL, href)

        return None
