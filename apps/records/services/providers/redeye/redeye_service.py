from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urljoin, quote

from bs4 import BeautifulSoup
from config.logging import NOTICE_LEVEL, log_event
from records.constants import REDEYE_BASE_URL
from .helpers import normalize_abs_url
from .http import RedeyeHTTPClient
from .page_product_scraper import RedeyeProductParser

logger = logging.getLogger(__name__)
_REDEYE_SERVICE_COMPONENT = "redeye_service"


def _log_redeye_service_event(
    level: int,
    event: str,
    message: str,
    **context: object,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_REDEYE_SERVICE_COMPONENT,
        event=event,
        **context,
    )


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

        _log_redeye_service_event(
            logging.INFO,
            "catalog_search_start",
            "Запущен поиск релиза Redeye по каталожному номеру.",
            catalog_number=cat,
        )
        product_urls = self._product_page_urls_by_catalog_number(cat)
        if not product_urls:
            raise ValueError(f"Redeye: release not found by Catalogue No. '{cat}'")

        req_cat = cat.upper()
        for abs_url in product_urls:
            _log_redeye_service_event(
                logging.INFO,
                "product_open",
                "Открыта карточка релиза Redeye.",
                catalog_number=req_cat,
                source=abs_url,
            )
            html_text = self.http.get_text(abs_url)

            payload = self.parser.parse(abs_url, html_text)
            parsed_cat = (payload.get("catalog_number") or "").strip().upper()
            if not parsed_cat:
                _log_redeye_service_event(
                    NOTICE_LEVEL,
                    "catalog_candidate_skipped",
                    "Карточка Redeye пропущена: не удалось извлечь каталожный номер.",
                    catalog_number=req_cat,
                    source=abs_url,
                )
                continue

            if parsed_cat != req_cat:
                _log_redeye_service_event(
                    NOTICE_LEVEL,
                    "catalog_candidate_mismatch",
                    "Карточка Redeye пропущена: каталожный номер не совпал.",
                    catalog_number=req_cat,
                    parsed_catalog_number=parsed_cat,
                    source=abs_url,
                )
                continue

            return RedeyeFetchResult(source_url=abs_url, payload=payload)

        raise ValueError(
            f"В Redeye не найден релиз с точным совпадением каталожного номера '{cat}'."
        )

    def parse_redeye_product_by_url(self, url: str) -> RedeyeFetchResult:
        """Метод получает карточку товара по-прямому URL и возвращает распарсенные поля."""
        if not url:
            raise ValueError("Product URL is required.")

        abs_url = normalize_abs_url(url)
        _log_redeye_service_event(
            logging.INFO,
            "product_open_by_url",
            "Открыта карточка релиза Redeye по прямому URL.",
            source=abs_url,
        )
        html_text = self.http.get_text(abs_url)
        payload = self.parser.parse(abs_url, html_text)
        return RedeyeFetchResult(source_url=abs_url, payload=payload)

    def _product_page_urls_by_catalog_number(self, catalog_number: str) -> list[str]:
        """
        Метод возвращает список URL карточек по каталожному номеру.

        Алгоритм:
            - Запрашивает страницу поиска с параметрами searchType=CAT&keywords=<CAT>.
            - Извлекает ссылки, ведущие на /vinyl/....
        """

        search_url = (
            f"{REDEYE_BASE_URL}/search/?searchType=CAT&keywords={quote(catalog_number)}"
        )
        _log_redeye_service_event(
            logging.DEBUG,
            "catalog_search_url_built",
            "Сформирован URL поиска Redeye по каталожному номеру.",
            catalog_number=catalog_number,
            source=search_url,
        )

        html_text = self.http.get_text(
            search_url, referer=f"{REDEYE_BASE_URL}/", slow=True
        )
        soup = BeautifulSoup(html_text, "html.parser")

        urls: list[str] = []
        seen: set[str] = set()
        for link in soup.select('a[href*="/vinyl/"]'):
            href = (link.get("href", "") or "").strip()
            if not href:
                continue
            abs_url = (
                href if href.startswith("http") else urljoin(REDEYE_BASE_URL, href)
            )
            abs_url = normalize_abs_url(abs_url)
            if abs_url in seen:
                continue
            seen.add(abs_url)
            urls.append(abs_url)

        return urls
