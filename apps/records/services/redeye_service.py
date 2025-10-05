# apps/records/services/redeye_service.py
from __future__ import annotations
import logging
import random
import re
import time
import html
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, List, Dict, Tuple
import requests
from bs4 import BeautifulSoup, NavigableString
from urllib.parse import urljoin

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


_MONTHS_EN_TO_RU = {
    "jan": "января",
    "feb": "февраля",
    "mar": "марта",
    "apr": "апреля",
    "may": "мая",
    "jun": "июня",
    "jul": "июля",
    "aug": "августа",
    "sep": "сентября",
    "oct": "октября",
    "nov": "ноября",
    "dec": "декабря",
}

logger = logging.getLogger(__name__)



def _format_expected_date_ru(text: str) -> Optional[str]:
    """
    Ищет в произвольном тексте фразу вида:
      'Expected 24 Oct 2025' / 'expected 7 September 2025'
    Возвращает строку: '24 октября 2025 года' или None.
    """
    m = re.search(
        r"Expected\s+(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    day = int(m.group(1))
    mon = m.group(2).lower().strip(".")
    year = int(m.group(3))

    # принимаем как полные, так и сокращённые названия месяцев
    key = mon[:3]
    mon_ru = _MONTHS_EN_TO_RU.get(key)
    if not mon_ru:
        return None

    return f"{day} {mon_ru} {year} года"


@dataclass
class RedeyeFetchResult:
    source_url: str
    payload: Dict


def _parse_expected_date_parts(text: str):
    """
    Ищет 'Expected 24 Oct 2025' / 'Expected 7 September 2025' и
    возвращает кортеж (year, month, day) как ints, либо (None, None, None).
    """
    m = re.search(r"Expected\s+(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})", text, re.I)
    if not m:
        return None, None, None
    day = int(m.group(1))
    mon_raw = m.group(2).lower().strip(".")
    mon = _MONTHS.get(mon_raw[:3])
    year = int(m.group(3))
    if not mon:
        return None, None, None
    return year, mon, day


class RedeyeService:
    BASE = "https://www.redeyerecords.co.uk"
    TIMEOUT = 20

    GBP_PRICE_RE = re.compile(r"£\s*(\d+(?:\.\d{1,2})?)")
    # Кат.№ — короткий токен из букв/цифр/дефиса, 2..24 символа
    CAT_RE = re.compile(r"\b([A-Z0-9\-]{2,24})\b", re.I)

    def __init__(self, *, delay_sec: float = 0.6, jitter_sec: float = 0.5,
                 max_retries: int = 4, cooldown_sec: int = 90, stop_on_block: bool = False):
        self.session = requests.Session()
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        ]
        self.base_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        }
        self.delay_sec = float(delay_sec); self.jitter_sec = float(jitter_sec)
        self.max_retries = int(max_retries); self.cooldown_sec = int(cooldown_sec)
        self.stop_on_block = bool(stop_on_block)

    # ---------------- PUBLIC ----------------

    def fetch_by_catalog_number(self, catalog_number: str) -> RedeyeFetchResult:
        cat = (catalog_number or "").strip()
        if not cat:
            raise ValueError("Catalogue number is required")

        logger.info("[Redeye] search by CAT: '%s'", cat)
        url = self._resolve_product_url_by_catno(cat)
        if not url:
            raise ValueError(f"Redeye: release not found by Catalogue No. '{cat}'")

        logger.info("[Redeye] opening product: %s", url)
        html_text = self._get(url)
        payload = self._parse_product_page(url, html_text)

        found_cat = (payload.get("catalog_number") or "").strip().upper()
        if not found_cat:
            # страховка: если вдруг не смогли вытащить — запишем то, что искали
            payload["catalog_number"] = cat.upper()
        elif found_cat != cat.upper():
            logger.warning(
                "[Redeye] CAT mismatch: requested '%s' vs parsed '%s' (%s)",
                cat, found_cat, url
            )

        return RedeyeFetchResult(source_url=url, payload=payload)

    def parse_product_by_url(self, url: str) -> RedeyeFetchResult:
        """
        Открывает страницу товара по переданному URL и возвращает распарсенные поля.
        Возвращаем тот же тип, что и fetch_by_catalog_number: RedeyeFetchResult(source_url, payload).
        """
        if not url:
            raise ValueError("Product URL is required")

        # Нормализуем до абсолютного
        abs_url = url if re.match(r"^https?://", url, re.I) else urljoin(self.BASE, url)

        logger.info("[Redeye] opening product by URL: %s", abs_url)
        html_text = self._get(abs_url)
        payload = self._parse_product_page(abs_url, html_text)

        # Подстраховка: если каталог.№ не извлёкся, не заполняем тут — пусть остаётся как распарсилось
        return RedeyeFetchResult(source_url=abs_url, payload=payload)


    # ---------------- NETWORK ----------------

    def _polite_sleep(self):
        time.sleep(self.delay_sec + random.uniform(0.0, self.jitter_sec))

    def _get(self, url: str, *, referer: Optional[str] = None, slow: bool = False) -> str:
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if slow:
                    self._polite_sleep()
                headers = dict(self.base_headers)
                headers["User-Agent"] = random.choice(self.user_agents)
                if referer:
                    headers["Referer"] = referer

                resp = self.session.get(url, headers=headers, timeout=self.TIMEOUT)
                status = resp.status_code

                if status == 200:
                    return html.unescape(resp.text)

                if 500 <= status < 600:
                    backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.8)
                    logger.warning("server %s for %s (attempt %s/%s) backoff=%.1fs",
                                   status, url, attempt, self.max_retries, backoff)
                    time.sleep(backoff);
                    continue

                if status in (403, 429):
                    logger.warning("possible block %s for %s → cooldown %ss (attempt %s/%s)",
                                   status, url, self.cooldown_sec, attempt, self.max_retries)
                    time.sleep(self.cooldown_sec)
                    if attempt == self.max_retries:
                        if self.stop_on_block:
                            raise requests.HTTPError(f"blocked: {status} {url}")
                        else:
                            logger.warning("skip blocked page: %s", url);
                            break
                    continue

                resp.raise_for_status()

            except requests.RequestException as e:
                last_exc = e
                backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.8)
                logger.warning("request error for %s: %s (attempt %s/%s) backoff=%.1fs",
                               url, e, attempt, self.max_retries, backoff)
                _polite_sleep(backoff)

        if last_exc:
            raise last_exc
        return ""  # мягкий skip при неустранимом блоке

    def _resolve_product_url_by_catno(self, cat: str) -> Optional[str]:
        search_url = f"{self.BASE}/search/?searchType=CAT&keywords={requests.utils.quote(cat)}"
        logger.info("[Redeye] search URL: %s", search_url)
        html_text = self._get(search_url, referer=f"{self.BASE}/", slow=True)

        # на странице поиска обычно одна большая карточка с ссылкой вида /vinyl/…
        soup = BeautifulSoup(html_text, "html.parser")
        for a in soup.select('a[href*="/vinyl/"]'):
            href = a.get("href", "")
            if not href:
                continue
            return href if href.startswith("http") else self.BASE + href
        return None

    # ---------------- PARSE PRODUCT ----------------

    def _parse_product_page(self, url: str, html_text: str) -> Dict:
        soup = BeautifulSoup(html_text, "html.parser")

        # 1) Заголовок «Artist - Title»
        title_text = self._extract_title(soup)
        artists, record_title = self._split_artist_title(title_text)

        # 2) Label
        label_name = self._extract_label(soup)

        # 3) Catalogue No.
        catno = self._extract_catalog_number(soup)

        # 4) Цена/наличие
        price, availability = self._extract_price_and_availability(soup)

        # --- Expected date parts (если это предзаказ) ---
        y, m, d = _parse_expected_date_parts(soup.get_text(" ", strip=True))
        release_year = y or None
        release_month = m or None
        release_day = d or None

        # 5) Картинка
        image_url = self._extract_image_url(soup, base=url)

        # 6) Простой треклист из субтайтла (если есть)
        tracks = self._extract_tracks_from_subtitle(soup)

        # 7) Формат: на Redeye нет структурированных данных → не заполняем
        formats: List[str] = []

        # аккуратная длина label (у тебя CharField(255))
        if label_name:
            label_name = label_name[:255]

        # 8) Notes: добавляем в текст чистую цену (ex-VAT), если нашли
        notes = None
        if price is not None:
            notes = f"Цена пластинки на redeyerecords.co.uk составляет: {price:.2f} GBP"

        logger.info(
            "[Redeye] page parsed: title='%s' artists=%s label='%s' cat='%s' price=%s avail=%s img=%s",
            record_title or title_text, artists, label_name, catno, price, availability, bool(image_url),
        )

        return {
            "title": record_title or title_text,
            "artists": artists,
            "label": label_name,
            "catalog_number": catno,
            "barcode": None,
            "country": None,
            "year": None,
            "genres": [],
            "styles": [],
            "formats": formats or [],
            "tracks": tracks or [],
            "price_gbp": str(price) if price is not None else None,
            "availability": availability,
            "image_url": image_url,
            "notes": notes,
            "release_year": release_year,
            "release_month": release_month,
            "release_day": release_day,
            "source": {"name": "redeye", "url": url},
        }

    # ---------------- HELPERS ----------------

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(" ", strip=True)
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return "Unknown Title"

    @staticmethod
    def _split_artist_title(full: str) -> Tuple[List[str], Optional[str]]:
        if " - " in full:
            artist_part, title_part = full.split(" - ", 1)
            artists = [a.strip() for a in re.split(r"\s*(?:,|&|/)\s*", artist_part) if a.strip()]
            return artists, title_part.strip()
        return [], full

    def _extract_label(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Ищет лейбл в блоке вида:
            <p>Label <span><a href="...">Some Label</a></span></p>
        Возвращает строку (<=255 символов) или None.
        """
        for p in soup.find_all("p"):
            if not p.contents:
                continue
            first = p.contents[0]
            if isinstance(first, NavigableString) and first.strip().lower() == "label":
                # приоритет: <a> внутри <span>
                a = p.find("a")
                if a and a.get_text(strip=True):
                    return a.get_text(strip=True)[:255]
                # запасной: текст в <span> или во всём <p> после слова "Label"
                span = p.find("span")
                if span and span.get_text(strip=True):
                    return span.get_text(strip=True)[:255]
                txt = p.get_text(" ", strip=True)
                tail = re.sub(r"^label\s*", "", txt, flags=re.IGNORECASE).strip()
                return tail[:255] if tail else None
        return None

    def _extract_catalog_number(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Ищет каталожный номер в блоке вида:
            <p>Catalogue No. <span>TEMPA124</span></p>
        Возвращает строку без префиксов (например, 'TEMPA124') или None.
        """
        for p in soup.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if re.match(r"^catalogue\s+no\.?\b", txt, re.IGNORECASE):
                # приоритет: содержимое <span>
                span = p.find("span")
                if span:
                    val = span.get_text(strip=True)
                    return val or None
                # запасной путь: вырезать префикс "Catalogue No."
                tail = re.sub(r"^catalogue\s+no\.?\s*", "", txt, flags=re.IGNORECASE).strip()
                return tail or None
        return None

    def _parse_catalog_number(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Резервный способ: пройтись по <p> и найти вариант с 'Catalogue No.'.
        Возвращает строку без префикса, например 'TEMPA124'.
        """
        for p in soup.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if re.match(r"^catalogue\s+no\.?\b", txt, re.IGNORECASE):
                span = p.find("span")
                if span:
                    val = span.get_text(strip=True)
                    return val or None
                tail = re.sub(r"^catalogue\s+no\.?\s*", "", txt, flags=re.IGNORECASE).strip()
                return tail or None
        return None

    def _extract_price_and_availability(self, soup: BeautifulSoup) -> Tuple[Optional[Decimal], Optional[str]]:
        text = soup.get_text(" ", strip=True)

        # приоритет: "£X ( £Y inc.vat )" → берём X
        m = re.search(r"£\s*(\d+(?:\.\d{1,2})?)\s*\(\s*£\s*\d+(?:\.\d{1,2})?\s*inc\.?\s*vat", text, re.I)
        if m:
            price = Decimal(m.group(1))
        else:
            # иначе соберём все £… и возьмём МИНИМАЛЬНУЮ (обычно это ex-VAT)
            prices = []
            for p in re.findall(r"£\s*(\d+(?:\.\d{1,2})?)", text):
                try:
                    prices.append(Decimal(p))
                except Exception:
                    pass
            price = min(prices) if prices else None

        availability = None
        t = text.lower()
        if "pre-order" in t or "expected" in t:
            availability = "preorder"
        elif "out of stock" in t:
            availability = "out_of_stock"
        elif "add to basket" in t or "in stock" in t:
            availability = "in_stock"

        return price, availability

    @staticmethod
    def _extract_image_url(soup: BeautifulSoup, base: str) -> Optional[str]:
        # сначала og:image
        og = soup.find("meta", attrs={"property": "og:image"})

        def _norm(u: str) -> str:
            if u.startswith("http"):
                return u
            if u.startswith("//"):
                return "https:" + u
            if u.startswith("/"):
                return RedeyeService.BASE + u
            return u

        if og and og.get("content"):
            return _norm(og["content"])
        # затем обычные <img>
        for img in soup.select("img"):
            src = img.get("src")
            if src:
                return _norm(src)
        return None

    @staticmethod
    @staticmethod
    def _extract_tracks_from_subtitle(soup: BeautifulSoup) -> List[Dict]:
        """
        Берём треклист из элемента с class="tracks".
        Поддерживаем два формата:
          1) "A1 Flow Key 06:19<br>A2 Reso 02 05:58<br>..."
          2) "Moon Cruise / Never Stop / ..."
        """
        node = soup.find(attrs={"class": "tracks"})
        if not node:
            return []

        # Берём сырой HTML, чтобы надёжно выловить <br>, <br />, <br/> и т.п.
        html = node.decode_contents()

        # 1) Если есть слеши и нет <br> — это формат "A / B / C"
        if "<br" not in html and "/" in html:
            parts = [p.strip(" \t\r\n-–—") for p in html.split("/") if p.strip()]
            return [{"position": "", "title": BeautifulSoup(p, "html.parser").get_text(" ", strip=True)} for p in parts]

        # 2) Иначе режем по <br>, <br/>, <br />
        for token in ("<br>", "<br/>", "<br />"):
            html = html.replace(token, "\n")
        text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        out: List[Dict] = []
        rx_pos = re.compile(r"^\s*([A-D]\d)\s+(.*)$")
        for ln in lines:
            m = rx_pos.match(ln)
            if m:
                # В title оставляем и тайминг, как просили
                out.append({"position": m.group(1), "title": m.group(2).strip()})
            else:
                out.append({"position": "", "title": ln})
        return out




