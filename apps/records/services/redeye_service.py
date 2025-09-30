# apps/records/services/redeye_service.py
from __future__ import annotations
import logging, random, re, time, html
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, List, Dict, Tuple
import requests
from bs4 import BeautifulSoup, NavigableString

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

    def __init__(self):
        self.session = requests.Session()
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        ]
        self.base_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        }

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

    # ---------------- NETWORK ----------------

    def _get(self, url: str, *, referer: Optional[str] = None, slow: bool = False) -> str:
        if slow:
            time.sleep(0.6)
        headers = dict(self.base_headers)
        headers["User-Agent"] = random.choice(self.user_agents)
        if referer:
            headers["Referer"] = referer
        resp = self.session.get(url, headers=headers, timeout=self.TIMEOUT)
        resp.raise_for_status()
        return html.unescape(resp.text)

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

        # 2) Правый блок: Label и Catalogue No. — вытаскиваем точечно
        catno, label_name = self._extract_catalog_and_label(soup)

        # 3) Цена/наличие
        price, availability = self._extract_price_and_availability(soup)
        # --- Expected date parts (если это предзаказ) ---
        y, m, d = _parse_expected_date_parts(soup.get_text(" ", strip=True))
        release_year = y or None
        release_month = m or None
        release_day = d or None

        # 4) Картинка
        image_url = self._extract_image_url(soup, base=url)

        # 5) Простой треклист из субтайтла (если есть)
        tracks = self._extract_tracks_from_subtitle(soup)

        # 6) Формат: на Redeye нет структурированных данных → не заполняем
        formats: List[str] = []

        # аккуратная длина label (у тебя CharField(255))
        if label_name:
            label_name = label_name[:255]

        # 7) Notes: добавляем в текст чистую цену (ex-VAT), если нашли
        notes = None
        if price:
            # форматируем 2 знака после запятой, точка как в исходнике
            notes = (f"Цена пластинки на redeyerecords.co.uk составляет: {price:.2f} GBP")

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

    def _extract_catalog_and_label(self, soup: BeautifulSoup):
        catno = None
        label = None

        # --- LABEL ---
        # Ищем <p>Label <span><a href="...">Liquid Luve Discs</a></span></p>
        for p in soup.find_all("p"):
            # первый узел в <p> — текст "Label"
            if not p.contents:
                continue
            first = p.contents[0]
            if isinstance(first, NavigableString) and first.strip().lower() == "label":
                a = p.find("a")
                if a and a.get_text(strip=True):
                    label = a.get_text(strip=True)[:255]
                    break

        # --- CATALOG NO. ---
        cat_node = soup.find(string=re.compile(r"Catalogue\s*No\.?", re.I))
        if cat_node:
            sib = cat_node.find_parent().find_next_sibling()
            txt = (sib.get_text(" ", strip=True) if sib else cat_node.parent.get_text(" ", strip=True))
            m = self.CAT_RE.search(txt)
            if m:
                catno = m.group(1).upper()

        return catno, label

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
            if u.startswith("http"): return u
            if u.startswith("//"): return "https:" + u
            if u.startswith("/"): return RedeyeService.BASE + u
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



