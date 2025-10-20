from __future__ import annotations

import argparse
import logging
import random
import re
import time
from dataclasses import dataclass
from textwrap import dedent
from typing import Iterator, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from records.constants import REDEYE_USER_AGENTS

logger = logging.getLogger(__name__)

_PRODUCT_HREF_PATTERNS = (
    re.compile(r"/vinyl/\d+[-a-z0-9]+", re.IGNORECASE),
    re.compile(r"/(?:downloads|cd)/\d+[-a-z0-9]+", re.IGNORECASE),
)


@dataclass(frozen=True)
class _PagerDecision:
    """Результат анализа пагинации в #pageLinks."""

    has_pagelinks: bool
    next_absolute_url: Optional[str]


class RedeyeListingScraper:
    """
    Класс реализует обход страниц категории Redeye (например, `/bass-music/pre-orders`)
    и генерацию абсолютных URL карточек товаров.

    Порядок перехода по страницам:
      1) Если на странице есть `<div id="pageLinks">` — используем только его:
         - сначала кнопка `>>` (`a.ml-2`);
         - иначе `<select id="pageNumber">` и берём следующий `option`;
         - если ни того, ни другого — это последняя страница (остановка).
      2) Если `#pageLinks` отсутствует — пробуем:
         - `rel="next"` / элементы с классом `next` / текстовые эвристики;
         - при их отсутствии — резервный синтез `.../page-{n+1}` с проверкой,
           что страница действительно содержит карточки.
    """

    def __init__(
        self,
        *,
        user_agent: Optional[str] = None,
        delay_sec: float = 0.6,
        timeout: float = 15.0,
        session: Optional[requests.Session] = None,
        jitter_sec: float = 0.5,
        max_retries: int = 4,
        cooldown_sec: int = 90,
        stop_on_block: bool = False,
    ) -> None:
        """
        Инициализирует скрапер.

        Args:
            user_agent: строка User-Agent; если не указана — берётся случайная из пула.
            delay_sec: базовая задержка между страницами.
            timeout: таймаут HTTP-запроса в секундах.
            session: внешняя requests.Session (опционально).
            jitter_sec: случайная прибавка к задержке (0..jitter_sec).
            max_retries: количество повторов при сбоях.
            cooldown_sec: охлаждение (сек.) при кодах 403/429.
            stop_on_block: прекращать работу при повторном блоке на последней попытке.
        """
        self.delay_sec = max(0.0, float(delay_sec))
        self.jitter_sec = max(0.0, float(jitter_sec))
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.cooldown_sec = int(cooldown_sec)
        self.stop_on_block = bool(stop_on_block)

        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent or random.choice(REDEYE_USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
            }
        )

    def iter_product_urls(
        self, category_url: str, limit: Optional[int] = None
    ) -> Iterator[str]:
        """
        Генерирует абсолютные URL карточек для всех страниц категории.

        Args:
            category_url: абсолютный URL страницы категории.
            limit: максимальное число ссылок (None — без ограничения).

        Yields:
            Абсолютные URL карточек.
        """
        origin = self._base_origin(category_url)
        seen_keys: set[str] = set()
        emitted = 0

        current_url = category_url
        current_page_num = self._extract_page_number(current_url) or 1

        while current_url:
            logger.info("fetch listing page %s: %s", current_page_num, current_url)
            html = self._fetch(current_url)
            if not html:
                logger.warning(
                    "skip page %s (no HTML / bad response)", current_page_num
                )
                break

            soup = BeautifulSoup(html, "html.parser")

            page_count = 0
            for rel_href in self._extract_product_hrefs(soup):
                abs_url = urljoin(origin, rel_href)
                key = self._canon_product_key(abs_url)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                logger.debug("product url: %s", abs_url)
                yield abs_url

                page_count += 1
                emitted += 1
                if limit is not None and emitted >= limit:
                    logger.info("limit reached: %s items", emitted)
                    return

            logger.info(
                "page %s: collected %s items (total %s)",
                current_page_num,
                page_count,
                emitted,
            )

            decision = self._next_via_pagelinks(soup, current_url, origin)
            if decision.has_pagelinks:
                if not decision.next_absolute_url:
                    logger.info("pagination finished by #pageLinks: last page reached")
                    break
                current_url = decision.next_absolute_url
                current_page_num = self._extract_page_number(current_url) or (
                    current_page_num + 1
                )
                self._sleep_polite()
                continue

            next_rel = self._find_next_page_href(soup)
            next_abs: Optional[str] = None
            if next_rel:
                next_abs = (
                    next_rel
                    if self._is_absolute(next_rel)
                    else urljoin(origin, next_rel)
                )
            else:
                guess = self._guess_next_page_path(current_url, current_page_num)
                if guess:
                    logger.debug("no explicit pager, try guessed next page: %s", guess)
                    html_next = self._fetch(guess)
                    if html_next and self._page_has_products(html_next):
                        next_abs = (
                            guess
                            if self._is_absolute(guess)
                            else urljoin(origin, guess)
                        )

            if next_abs:
                current_url = next_abs
                current_page_num = self._extract_page_number(current_url) or (
                    current_page_num + 1
                )
                self._sleep_polite()
            else:
                if page_count == 0:
                    logger.info(
                        "pagination finished: no products on page (likely beyond last)"
                    )
                else:
                    logger.info("pagination finished: no next page detected")
                break

    def _fetch(self, url: str, *, referer: Optional[str] = None) -> Optional[str]:
        """
        Выполняет HTTP GET с ретраями и «охлаждением» при 403/429.

        Args:
            url: абсолютный URL страницы.
            referer: заголовок Referer (опционально).

        Returns:
            Текст HTML либо None при неуспехе.
        """
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self.session.headers["User-Agent"] = random.choice(
                    REDEYE_USER_AGENTS
                )  # лёгкая ротация
                if referer:
                    self.session.headers["Referer"] = referer

                resp = self.session.get(url, timeout=self.timeout)
                status = resp.status_code

                if status == 200:
                    ctype = resp.headers.get("Content-Type") or ""
                    if "text/html" not in ctype:
                        logger.warning("unexpected content-type %s for %s", ctype, url)
                        return None
                    return resp.text

                if 500 <= status < 600:
                    backoff = min(2 ** (attempt - 1), 8) + random.uniform(0.0, 0.8)
                    logger.warning(
                        "server %s for %s (attempt %s/%s), backoff=%.1fs",
                        status,
                        url,
                        attempt,
                        self.max_retries,
                        backoff,
                    )
                    time.sleep(backoff)
                    continue

                if status in (403, 429):
                    logger.warning(
                        "possible block %s for %s (attempt %s/%s) → cooldown %ss",
                        status,
                        url,
                        attempt,
                        self.max_retries,
                        self.cooldown_sec,
                    )
                    time.sleep(self.cooldown_sec)
                    if attempt == self.max_retries:
                        if self.stop_on_block:
                            logger.error(
                                "stop_on_block=True → прекращаем работу на %s", url
                            )
                            return None
                        logger.warning("skip blocked page: %s", url)
                        return None
                    continue  # ещё попытка после охлаждения

                logger.warning("bad status %s for %s", status, url)
                return None

            except requests.RequestException as err:
                last_error = err
                backoff = min(2 ** (attempt - 1), 8) + random.uniform(0.0, 0.8)
                logger.warning(
                    "request error for %s: %s (attempt %s/%s), backoff=%.1fs",
                    url,
                    err,
                    attempt,
                    self.max_retries,
                    backoff,
                )
                time.sleep(backoff)

        logger.error(
            "giving up on %s after %s attempts; last error: %s",
            url,
            self.max_retries,
            last_error,
        )
        return None

    def _sleep_polite(self) -> None:
        """Делает паузу между запросами: базовая задержка + джиттер."""
        time.sleep(self.delay_sec + random.uniform(0.0, self.jitter_sec))

    @staticmethod
    def _is_absolute(url: str) -> bool:
        return bool(re.match(r"^https?://", url, re.IGNORECASE))

    @staticmethod
    def _base_origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _extract_product_hrefs(soup: BeautifulSoup) -> Iterator[str]:
        """
        Извлекает относительные пути карточек с текущей страницы.

        Returns:
            Итератор относительных путей вида `/vinyl/186174-onef079-...`.
        """
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href.startswith("#"):
                continue

            relative = href
            if href.startswith(("http://", "https://")):
                parsed = urlparse(href)
                relative = parsed.path or "/"

            for pattern in _PRODUCT_HREF_PATTERNS:
                if pattern.search(relative):
                    yield relative
                    break

    @staticmethod
    def _find_next_page_href(soup: BeautifulSoup) -> Optional[str]:
        """Пытается найти ссылку на следующую страницу без учёта #pageLinks."""
        tag = soup.find("a", attrs={"rel": "next"}, href=True)
        if tag:
            return tag["href"]

        tag = soup.find("a", class_=re.compile(r"\bnext\b", re.IGNORECASE), href=True)
        if tag:
            return tag["href"]

        for anchor in soup.find_all("a", href=True):
            text = (anchor.get_text() or "").strip().lower()
            if text in {"next", "older", "›", "»"}:
                return anchor["href"]

        for anchor in soup.select("ul.pagination a[href], nav.pagination a[href]"):
            classes = " ".join(anchor.get("class", []))
            if re.search(r"\bnext\b", classes, re.IGNORECASE):
                return anchor["href"]

        return None

    @staticmethod
    def _extract_page_number(url: str) -> Optional[int]:
        """Извлекает номер страницы из пути вида `/page-3`."""
        match = re.search(r"/page-(\d+)", url)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _guess_next_page_path(current_url: str, current_page_num: int) -> Optional[str]:
        """
        Синтезирует путь следующей страницы (резервный вариант),
        корректно обрабатывая случаи без `page-1`.
        """
        match = re.search(r"(/page-)(\d+)(/?)$", current_url)
        if match:
            base = current_url[: match.start(1)]
            next_num = int(match.group(2)) + 1
            tail = match.group(3) or ""
            return f"{base}/page-{next_num}{tail}"

        if re.search(r"/pre-orders/?$", current_url):
            return f"{current_url.rstrip('/')}/page-2"

        return None

    @staticmethod
    def _canon_product_key(url: str) -> str:
        """
        Нормализует ссылку карточки для дедупликации:
        - берётся только path (без схемы/хоста/параметров/якорей),
        - срезается хвостовой слэш,
        - приводится к нижнему регистру.
        """
        parsed = urlparse(url)
        path = (parsed.path or "/").rstrip("/")
        return path.lower() or "/"

    def _page_has_products(self, html: str) -> bool:
        """Возвращает True, если HTML содержит хотя бы одну ссылку карточки."""
        soup = BeautifulSoup(html, "html.parser")
        for _ in self._extract_product_hrefs(soup):
            return True
        return False

    def _next_via_pagelinks(
        self, soup: BeautifulSoup, current_url: str, origin: str
    ) -> _PagerDecision:
        """
        Анализирует `<div id="pageLinks">` и определяет ссылку на следующую страницу.

        Returns:
            _PagerDecision(has_pagelinks=<bool>, next_absolute_url=<str|None>)

            Если has_pagelinks == True и next_absolute_url == None — это последняя страница.
        """
        page_links = soup.find(id="pageLinks")
        if not page_links:
            return _PagerDecision(False, None)

        button_next = page_links.find(
            "a", class_=re.compile(r"\bml-2\b", re.IGNORECASE), href=True
        )
        if button_next:
            href = (button_next.get("href") or "").strip()
            if href:
                next_abs = href if self._is_absolute(href) else urljoin(origin, href)
                logger.debug("pageLinks: next via button -> %s", next_abs)
                return _PagerDecision(True, next_abs)

        select = page_links.find("select", id="pageNumber")
        if select:
            options = select.find_all("option")
            current_index: Optional[int] = None

            for idx, opt in enumerate(options):
                value = (opt.get("value") or "").strip()
                if opt.has_attr("selected"):
                    current_index = idx
                    break
                if value and self._urls_equivalent(value, current_url):
                    current_index = idx
                    break

            if current_index is not None and (current_index + 1) < len(options):
                next_value = (options[current_index + 1].get("value") or "").strip()
                if next_value:
                    next_abs = (
                        next_value
                        if self._is_absolute(next_value)
                        else urljoin(origin, next_value)
                    )
                    logger.debug("pageLinks: next via select -> %s", next_abs)
                    return _PagerDecision(True, next_abs)

        logger.debug("pageLinks: no next page (last page reached)")
        return _PagerDecision(True, None)

    @staticmethod
    def _urls_equivalent(a: str, b: str) -> bool:
        """Сравнивает URL без учёта trailing slash; если один относительный — сравниваем только path."""

        def _norm(u: str) -> str:
            parsed = urlparse(u)
            path = (parsed.path or "/").rstrip("/")
            return path or "/"

        return _norm(a) == _norm(b)


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Собрать ВСЕ ссылки карточек Redeye с указанной страницы категории (с пагинацией).",
        epilog=dedent(
            """\
            Примеры запуска:

              1) Локально:
                 python apps/records/scrapers/redeye_listing.py --url https://www.redeyerecords.co.uk/bass-music/pre-orders --limit 20 --debug

              2) В Docker-контейнере:
                 docker compose exec django uv run -m apps.records.scrapers.redeye_listing --url "https://www.redeyerecords.co.uk/drum-and-bass/pre-orders" --limit 10 --debug
            """
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--url", required=True, help="URL страницы категории Redeye.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Максимум ссылок (по умолчанию — без ограничения).",
    )
    parser.add_argument(
        "--delay", type=float, default=0.6, help="Задержка между страницами (сек.)."
    )
    parser.add_argument(
        "--timeout", type=float, default=15.0, help="HTTP таймаут (сек.)."
    )
    parser.add_argument("--debug", action="store_true", help="Подробный лог (DEBUG).")
    parser.add_argument(
        "--jitter",
        type=float,
        default=0.5,
        help="Случайная прибавка к задержке (сек.).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=4,
        help="Число повторов при сетевых/серверных ошибках.",
    )
    parser.add_argument(
        "--cooldown", type=int, default=90, help="Охлаждение при 403/429 (сек.)."
    )
    parser.add_argument(
        "--stop-on-block",
        action="store_true",
        help="Остановиться при повторном 403/429.",
    )

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    scraper = RedeyeListingScraper(
        delay_sec=args.delay,
        timeout=args.timeout,
        jitter_sec=args.jitter,
        max_retries=args.max_retries,
        cooldown_sec=args.cooldown,
        stop_on_block=args.stop_on_block,
    )

    count = 0
    for link in scraper.iter_product_urls(args.url, limit=args.limit):
        print(link)
        count += 1
    logger.info("Готово. Всего ссылок: %s", count)


if __name__ == "__main__":
    _cli()
