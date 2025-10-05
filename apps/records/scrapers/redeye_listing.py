# apps/records/scrapers/redeye_listing.py
from __future__ import annotations

import argparse
import logging
import re
import time
import random
from textwrap import dedent
from typing import Iterator, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class RedeyeListingScraper:
    """
    Обходит категорию Redeye (например, /bass-music/pre-orders) с пагинацией
    и отдаёт абсолютные URL карточек товаров.

    Порядок определения "следующей страницы":
      1) Если есть <div id="pageLinks">:
         - берём <a class="ml-2" ...> (кнопка >>), если она есть;
         - иначе смотрим <select id="pageNumber"> и берём option, следующий за текущим selected;
         - если ничего нет — это последняя страница, ОСТАНАВЛИВАЕМСЯ (никаких guess /page-N).
      2) Если #pageLinks нет:
         - rel="next" / класс .next / текстовые эвристики;
         - если и их нет — пробуем аккуратно синтезировать /page-{n+1} (резерв).
    """

    _PRODUCT_HREF_PATTERNS = (
        re.compile(r"/vinyl/\d+[-a-z0-9]+", re.IGNORECASE),
        re.compile(r"/(downloads|cd)/\d+[-a-z0-9]+", re.IGNORECASE),
    )

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
        self.delay_sec = max(0.0, float(delay_sec))
        self.jitter_sec = max(0.0, float(jitter_sec))
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.cooldown_sec = int(cooldown_sec)
        self.stop_on_block = bool(stop_on_block)

        self.session = session or requests.Session()
        self._user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        ]
        self.session.headers.update({
            "User-Agent": user_agent or random.choice(self._user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        })

    # ---------- публичный API ----------
    def iter_product_urls(self, category_url: str, limit: Optional[int] = None) -> Iterator[str]:
        """
        Обходит ВСЕ страницы категории и yield'ит абсолютные URL карточек товаров.
        :param category_url: абсолютный URL страницы категории
        :param limit: максимум ссылок (None — без ограничения)
        """
        base = self._base_origin(category_url)
        seen: set[str] = set()
        collected = 0

        current_url = category_url
        current_page_num = self._extract_page_number(current_url) or 1

        while current_url:
            logger.info("fetch listing page %s: %s", current_page_num, current_url)
            html = self._fetch(current_url)
            if not html:
                logger.warning("skip page %s (no HTML / bad response)", current_page_num)
                break

            soup = BeautifulSoup(html, "html.parser")

            # 1) собрать ссылки карточек на текущей странице
            page_count = 0
            for href in self._extract_product_hrefs(soup):

                abs_url = urljoin(base, href)
                key = self._canon_product_key(abs_url)
                if key in seen:
                    continue
                seen.add(key)
                logger.debug("product url: %s", abs_url)
                yield abs_url

                collected += 1
                page_count += 1
                if limit is not None and collected >= limit:
                    logger.info("limit reached: %s items", collected)
                    return

            logger.info("page %s: collected %s items (total %s)", current_page_num, page_count, collected)

            # 2) сначала — строгая логика #pageLinks
            has_pagelinks, next_abs_from_pagelinks = self._next_via_pagelinks(soup, current_url, base)

            if has_pagelinks:
                # Если есть #pageLinks и в нём НЕТ следующей страницы — стоп
                if not next_abs_from_pagelinks:
                    logger.info("pagination finished by #pageLinks: last page reached")
                    break
                # Идём строго по ссылке из #pageLinks
                current_url = next_abs_from_pagelinks
                current_page_num = (self._extract_page_number(current_url) or (current_page_num + 1))
                self._sleep_polite()
                continue

            # 3) если #pageLinks нет — используем старые эвристики (rel='next'/guess)
            next_rel = self._find_next_page_href(soup)
            next_abs = None
            if not next_rel:
                guessed = self._guess_next_page_path(current_url, current_page_num)
                if guessed:
                    logger.debug("no explicit pager, try guessed next page: %s", guessed)
                    html_next = self._fetch(guessed)
                    if html_next and self._page_has_products(html_next):
                        next_abs = guessed if re.match(r"^https?://", guessed, re.I) else urljoin(base, guessed)
            else:
                next_abs = next_rel if re.match(r"^https?://", next_rel, re.I) else urljoin(base, next_rel)

            # 4) переход на следующую страницу или остановка
            if next_abs:
                current_url = next_abs
                current_page_num = (self._extract_page_number(current_url) or (current_page_num + 1))
                self._sleep_polite()
            else:
                # Доп. предохранитель: если на текущей странице 0 товаров — тоже стоп
                if page_count == 0:
                    logger.info("pagination finished: no products on page (likely beyond last)")
                else:
                    logger.info("pagination finished: no next page detected")
                break

    # ---------- внутренние помощники ----------
    def _fetch(self, url: str, *, referer: Optional[str] = None) -> Optional[str]:
        """
        Политный fetch с ретраями, бэкофом и охлаждением на 403/429.
        Возвращает текст HTML или None.
        """
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                # умеренная ротация агента
                self.session.headers["User-Agent"] = random.choice(self._user_agents)
                if referer:
                    self.session.headers["Referer"] = referer
                resp = self.session.get(url, timeout=self.timeout)
                status = resp.status_code

                if status == 200:
                    ctype = resp.headers.get("Content-Type", "")
                    if "text/html" not in ctype:
                        logger.warning("unexpected content-type %s for %s", ctype, url)
                        return None
                    return resp.text

                # мягкие сбои (5xx) — пробуем с бэкофом
                if 500 <= status < 600:
                    backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.8)
                    logger.warning("server %s for %s (attempt %s/%s), backoff=%.1fs",
                                   status, url, attempt, self.max_retries, backoff)
                    time.sleep(backoff)
                    continue

                # подозрение на блок
                if status in (403, 429):
                    logger.warning("possible block %s for %s (attempt %s/%s) → cooldown %ss",
                                   status, url, attempt, self.max_retries, self.cooldown_sec)
                    time.sleep(self.cooldown_sec)
                    if attempt == self.max_retries:
                        if self.stop_on_block:
                            logger.error("stop_on_block=True → прекращаем работу на %s", url)
                            return None
                        else:
                            logger.warning("skip blocked page: %s", url)
                            return None
                    # после охлаждения — ещё попытка (без лишнего сна)
                    continue

                # прочие статусы — без ретраев
                logger.warning("bad status %s for %s", status, url)
                return None

            except requests.RequestException as e:
                last_exc = e
                backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.8)
                logger.warning("request error for %s: %s (attempt %s/%s), backoff=%.1fs",
                               url, e, attempt, self.max_retries, backoff)
                time.sleep(backoff)

        logger.error("giving up on %s after %s attempts; last error: %s", url, self.max_retries, last_exc)
        return None

    def _sleep_polite(self):
        # базовая задержка + джиттер
        jitter = random.uniform(0.0, self.jitter_sec)
        time.sleep(self.delay_sec + jitter)

    @staticmethod
    def _base_origin(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _extract_product_hrefs(self, soup: BeautifulSoup) -> Iterator[str]:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("#"):
                continue
            rel = href
            if href.startswith("http://") or href.startswith("https://"):
                parsed = urlparse(href)
                rel = parsed.path or "/"
            for pat in self._PRODUCT_HREF_PATTERNS:
                if pat.search(rel):
                    yield rel
                    break

    @staticmethod
    def _find_next_page_href(soup: BeautifulSoup) -> Optional[str]:
        # rel="next"
        tag = soup.find("a", attrs={"rel": "next"}, href=True)
        if tag:
            return tag["href"]

        # класс next
        tag = soup.find("a", class_=re.compile(r"\bnext\b", re.I), href=True)
        if tag:
            return tag["href"]

        # текстовые эвристики
        for a in soup.find_all("a", href=True):
            text = (a.get_text() or "").strip().lower()
            if text in {"next", "older", "›", "»"}:
                return a["href"]

        # элементы пагинации (резерв)
        pagers = soup.select("ul.pagination a[href], nav.pagination a[href]")
        for a in pagers:
            classes = " ".join(a.get("class", []))
            if re.search(r"\bnext\b", classes, re.I):
                return a["href"]

        return None

    @staticmethod
    def _extract_page_number(url: str) -> Optional[int]:
        m = re.search(r"/page-(\d+)", url)
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

    @staticmethod
    def _guess_next_page_path(current_url: str, current_page_num: int) -> Optional[str]:
        # уже на /page-N
        m = re.search(r"(/page-)(\d+)(/?)$", current_url)
        if m:
            base = current_url[: m.start(1)]
            n = int(m.group(2)) + 1
            tail = m.group(3) or ""
            return f"{base}/page-{n}{tail}"

        # первая страница без page-1
        if re.search(r"/pre-orders/?$", current_url):
            tail_slash = "" if current_url.endswith("/") else ""
            return f"{current_url.rstrip('/')}/page-2"

        return None

    @staticmethod
    def _canon_product_key(url: str) -> str:
        """
        Канонизируем ссылку для дедупликации:
        - берём только path (без схемы/хоста/параметров/якорей);
        - режем хвостовой слэш;
        - приводим к нижнему регистру (пути у них не чувствительны к регистру).
        """
        p = urlparse(url)
        path = (p.path or "/").rstrip("/")
        return path.lower() or "/"

    def _page_has_products(self, html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        for _ in self._extract_product_hrefs(soup):
            return True
        return False

    # --- строгая пагинация по #pageLinks ---
    def _next_via_pagelinks(self, soup: BeautifulSoup, current_url: str, base: str) -> Tuple[bool, Optional[str]]:
        """
        Если на странице есть <div id="pageLinks">, вычисляет ссылку на следующую страницу.
        Возвращает (has_pagelinks: bool, next_abs_url_or_none).
        Если has_pagelinks == True и next == None -> это последняя страница (жёсткая остановка).
        """
        page_links = soup.find(id="pageLinks")
        if not page_links:
            return False, None

        # 1) Явная кнопка ">>"
        a_next = page_links.find("a", class_=re.compile(r"\bml-2\b", re.I), href=True)
        if a_next:
            href = a_next["href"].strip()
            if href:
                next_abs = href if re.match(r"^https?://", href, re.I) else urljoin(base, href)
                logger.debug("pageLinks: next via button -> %s", next_abs)
                return True, next_abs

        # 2) Селектор с вариантами страниц
        select = page_links.find("select", id="pageNumber")
        if select:
            options = select.find_all("option")
            # найти текущий selected (по атрибуту selected ИЛИ по совпадению value/current_url)
            cur_idx = None
            for i, opt in enumerate(options):
                val = (opt.get("value") or "").strip()
                is_selected = opt.has_attr("selected")
                if is_selected:
                    cur_idx = i
                    break
                if val and self._urls_equivalent(val, current_url):
                    cur_idx = i
                    break

            if cur_idx is not None and (cur_idx + 1) < len(options):
                next_val = (options[cur_idx + 1].get("value") or "").strip()
                if next_val:
                    next_abs = next_val if re.match(r"^https?://", next_val, re.I) else urljoin(base, next_val)
                    logger.debug("pageLinks: next via select -> %s", next_abs)
                    return True, next_abs

        # ничего нет → последняя страница
        logger.debug("pageLinks: no next page (last page reached)")
        return True, None

    @staticmethod
    def _urls_equivalent(a: str, b: str) -> bool:
        """Сравнивает URL без учёта trailing slash; если один относительный — сравниваем только path."""

        def norm(u: str) -> str:
            p = urlparse(u)
            path = p.path.rstrip("/")
            return path or "/"

        return norm(a) == norm(b)


# Удобный функциональный интерфейс
def iterate_category_urls(category_url: str, *, limit: Optional[int] = None, delay_sec: float = 0.6) -> Iterator[str]:
    scraper = RedeyeListingScraper(delay_sec=delay_sec)
    yield from scraper.iter_product_urls(category_url, limit=limit)


# ---------- CLI для тестового запуска ----------
def _cli():
    parser = argparse.ArgumentParser(
        description="Собрать ВСЕ ссылки карточек Redeye с указанной страницы категории (с пагинацией).",
        epilog=dedent("""\
                Примеры запуска:

                  1) Локально (из IDE/терминала) из директории файла:
                     python  records.scrapers.redeye_listing --url https://www.redeyerecords.co.uk/bass-music/pre-orders

                     Через uv:
                     uv run records.scrapers.redeye_listing --url https://www.redeyerecords.co.uk/drum-and-bass/pre-orders --debug

                  2) Внутри Docker-контейнера (рекомендуется для проекта):
                     docker compose exec django uv run -m records.scrapers.redeye_listing --url "https://www.redeyerecords.co.uk/bass-music/pre-orders" --debug

                     Подсказки:
                    - --limit N            : ограничить количество найденных ссылок (для быстрой проверки)
                    - --delay 0.6          : задержка между страницами (секунды)
                    - --timeout 15         : таймаут HTTP (секунды)
                    - --debug              : подробные логи, печатаем каждую найденную ссылку
            """),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--url",
        required=True,
        help="URL страницы категории, например: https://www.redeyerecords.co.uk/bass-music/pre-orders",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Максимум ссылок для вывода (по умолчанию — без ограничения)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.6,
        help="Задержка между страницами (сек.)",
    )
    parser.add_argument(
        "--timeout", type=float, default=15.0,
        help="HTTP таймаут (сек.)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Режим отладки (DEBUG): печатать каждую найденную ссылку",
    )
    parser.add_argument("--jitter", type=float, default=0.5, help="Случайная прибавка к задержке (сек.)")
    parser.add_argument("--max-retries", type=int, default=4, help="Число повторов при сетевых/серверных ошибках")
    parser.add_argument("--cooldown", type=int, default=90, help="Охлаждение при 403/429 (сек.)")
    parser.add_argument("--stop-on-block", action="store_true", help="Остановиться при повторном 403/429")
    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")

    scraper = RedeyeListingScraper(
        delay_sec=args.delay,
        timeout=args.timeout,
        jitter_sec=args.jitter,
        max_retries=args.max_retries,
        cooldown_sec=args.cooldown,
        stop_on_block=args.stop_on_block,
    )
    count = 0
    for u in scraper.iter_product_urls(args.url, limit=args.limit):
        print(u)
        count += 1

    logger.info("Готово. Всего ссылок: %s", count)


if __name__ == "__main__":
    _cli()
