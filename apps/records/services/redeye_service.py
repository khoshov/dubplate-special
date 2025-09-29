# apps/records/services/redeye_service.py
from __future__ import annotations
import logging, random, re, time, html
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, List, Dict, Tuple
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class RedeyeFetchResult:
    source_url: str
    payload: Dict


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

        # 4) Картинка
        image_url = self._extract_image_url(soup, base=url)

        # 5) Простой треклист из субтайтла (если есть)
        tracks = self._extract_tracks_from_subtitle(soup)

        # 6) Очень грубое угадывание формата
        formats = self._guess_formats_from_page(soup)

        # аккуратная длина label (у тебя CharField(255))
        if label_name:
            label_name = label_name[:255]

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

    def _extract_catalog_and_label(self, soup: BeautifulSoup) -> Tuple[Optional[str], Optional[str]]:
        """
        На боевой карточке справа есть «Label …» и «Catalogue No. …».
        Ищем текстовые узлы и берём ближайшее значение.
        """
        catno: Optional[str] = None
        label: Optional[str] = None

        # --- Label ---
        # Самый точный путь на текущей верстке: найти текст 'Label' и взять следующий <a> либо следующий текстовый узел
        label_node = soup.find(string=re.compile(r"^\s*Label\s*$", re.I))
        if label_node:
            # сначала попробуем <a> рядом
            a = label_node.find_parent().find_next("a")
            if a and a.get_text(strip=True):
                label = a.get_text(strip=True)
            else:
                sib = label_node.find_parent().find_next_sibling()
                if sib:
                    label = sib.get_text(" ", strip=True)
        if label:
            # часто рядом «Label Liquid Luve Discs» → оставим только имя
            label = re.sub(r"^\s*Label\s*", "", label, flags=re.I).strip()

        # --- Catalogue No. ---
        cat_node = soup.find(string=re.compile(r"Catalogue\s*No\.?", re.I))
        if cat_node:
            # ближайший следующий текст, из него — короткий токен (LQLDISC01 и т.п.)
            txt = ""
            # сначала прямой сосед
            sib = cat_node.find_parent().find_next_sibling()
            if sib:
                txt = sib.get_text(" ", strip=True)
            if not txt:
                txt = cat_node.parent.get_text(" ", strip=True)
            m = self.CAT_RE.search(txt)
            if m:
                catno = m.group(1).upper()

        return catno, label

    def _extract_price_and_availability(self, soup: BeautifulSoup) -> Tuple[Optional[Decimal], Optional[str]]:
        text = soup.get_text(" ", strip=True)
        prices = []
        for m in self.GBP_PRICE_RE.finditer(text):
            try:
                prices.append(Decimal(m.group(1)))
            except Exception:
                pass
        price = max(prices) if prices else None

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
        for img in soup.select("img"):
            src = img.get("src")
            if not src:
                continue
            if src.startswith("http"):
                return src
            if src.startswith("//"):
                return "https:" + src
            if src.startswith("/"):
                return RedeyeService.BASE + src
        return None

    @staticmethod
    def _extract_tracks_from_subtitle(soup: BeautifulSoup) -> List[Dict]:
        # под H1 есть блок с треками (A1…/A2…/B1…/B2…). Разрежем по строкам/слешам
        area_texts: List[str] = []
        h1 = soup.find("h1")
        if h1:
            nxt = h1.find_next(["h2", "div"])
            if nxt:
                area_texts.append(nxt.get_text("\n", strip=True))
        # fallback: правая колонка
        right = soup.find(string=re.compile(r"Redeye\s*No\.", re.I))
        if right:
            area_texts.append(right.find_parent().get_text("\n", strip=True))

        joined = "\n".join(area_texts)
        raw_parts = [p.strip(" .") for p in re.split(r"[\/\n]+", joined) if p.strip()]
        # фильтруем очевидный мусор, оставляем «Xx TrackName 07:31» как есть
        result: List[Dict] = []
        for p in raw_parts:
            if len(p) > 80:
                continue
            if re.search(r"\b(A|B|C|D)\d", p, re.I) or " " in p:
                result.append({"title": p})
        # не считаем ошибкой, если пусто
        return result

    @staticmethod
    def _guess_formats_from_page(soup: BeautifulSoup) -> List[str]:
        txt = soup.get_text(" ", strip=True).upper()
        formats: List[str] = []
        # простая эвристика
        if "12\"" in txt or " 12IN" in txt or " LP" in txt:
            formats.append("LP")
        if "10\"" in txt:
            formats.append('10"')
        if "7\"" in txt:
            formats.append('7"')
        return formats
