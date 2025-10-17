# apps/records/services/tracks/audio/capture.py


"""
Захват mp3-превью со страницы Redeye через Playwright (headless Chromium).

Ключевые моменты:
- Кнопки плеера считываем по селектору .btn-play[data-sample], извлекаем буквы ('a','b','c','d', ...)
- ПОРЯДОК ССЫЛОК: жёстко формируем в алфавитном порядке букв (a<b<c<d...), а не по DOM и не по времени ответа
- Если каких-то ссылок не хватает — достраиваем быстро через HEAD-запросы по шаблону
  https://sounds.redeyerecords.co.uk/<number><letter>.mp3, где prefix/number берём из уже пойманных URL
- Логи на русском; подробности — на DEBUG
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from typing import Dict, List, Optional

import requests
from playwright.sync_api import (
    Page,
    TimeoutError as PWTimeout,
    Error as PWError,
    sync_playwright,
)
from ...providers.redeye.utils import normalize_redeye_url

logger = logging.getLogger(__name__)

# --- эвристики для отсева и распознавания аудио ---
MEDIA_OK_STATUSES = {200, 206}
MEDIA_CT_HINTS = ("audio/", "mpegurl", "application/vnd.apple.mpegurl")
MEDIA_URL_HINTS = (".mp3", ".aac", ".m3u8", "/stream/")

# --- селекторы на Redeye ---
BTN_QUERY = ".play a.btn-play[data-sample]"
COOKIE_SELECTORS = (
    'button:has-text("Accept")',
    'button:has-text("I Agree")',
    "text=Accept all",
    'button:has-text("OK")',
    'button[aria-label="Accept"]',
)

# --- тайминги ---
FALLBACK_HTTP_TIMEOUT = 5.0
PER_CLICK_WAIT_TICK_MS = 120.0


# ---------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ---------------------------------------------------------------------------


def _dismiss_cookie_banners(page: Page) -> None:
    """Закрыть cookie-баннер (если есть). Ошибки игнорируем."""
    for sel in COOKIE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                logger.debug("[capture] закрываем cookie-баннер селектором: %s", sel)
                loc.click(timeout=1000)
                break
        except PWError:
            continue


def _is_audio_like(resp) -> bool:
    """Проверяем, похож ли сетевой ответ на аудио-ресурс (по статусу и content-type/URL)."""
    status = getattr(resp, "status", None)
    url = (getattr(resp, "url", "") or "").lower()
    headers = getattr(resp, "headers", {}) or {}
    ct = str(headers.get("content-type", "")).lower()
    if status not in MEDIA_OK_STATUSES:
        return False
    return any(h in ct for h in MEDIA_CT_HINTS) or any(
        h in url for h in MEDIA_URL_HINTS
    )


def _wire_sniffer(page: Page) -> "OrderedDict[str, None]":
    """
    Подписываемся на page.on('response') и собираем все подходящие аудио-URL
    (в упорядоченную коллекцию без дублей).
    """
    bag: "OrderedDict[str, None]" = OrderedDict()

    def _on_response(resp):
        try:
            if _is_audio_like(resp):
                url = re.sub(r"\s+", "", resp.url)
                if url and url not in bag:
                    bag[url] = None
                    logger.debug("[capture] обнаружен аудио-ответ: %s", url)
        except Exception as e:
            logger.debug("[capture] ошибка сниффера: %s", e)

    page.on("response", _on_response)
    return bag


def _btn_letters(page: Page) -> List[str]:
    """
    Возвращает ОТСОРТИРОВАННЫЙ список букв из data-sample всех кнопок плеера.
    Сортируем алфавитно (a<b<c<d...), чтобы исключить DOM-аномалии ('d','c' в конце и т.п.).
    """
    seen: set[str] = set()
    try:
        btns = page.locator(BTN_QUERY)
        n = btns.count()
        for i in range(n):
            ds = (btns.nth(i).get_attribute("data-sample") or "").strip().lower()
            if ds:
                seen.add(ds)
    except PWError:
        pass
    letters = sorted(seen)
    logger.debug("[capture] буквы плеера (отсортированы): %s", letters)
    return letters


def _suffix_letter(u: str) -> str:
    """
    Извлечь букву суффикса из имени файла URL.
    Примеры: /123456a.mp3 -> 'a'; /123456.mp3 -> 'a'; /123456c.m3u8 -> 'c'.
    """
    m = re.search(r"/(\d+)([a-z]?)\.mp3(?:\?.*)?$", u, flags=re.I)
    if m:
        return m.group(2) or "a"
    m2 = re.search(r"/(\d+)([a-z]?)\.(m3u8|aac)(?:\?.*)?$", u, flags=re.I)
    if m2:
        return m2.group(2) or "a"
    return ""


def _map_urls_by_letters(urls: List[str], letters: List[str]) -> List[str]:
    """
    Строим массив URL строго в порядке букв `letters`.
    Для каждой буквы берём лучший вариант: .mp3 предпочтительнее .m3u8/.aac.
    """
    best: Dict[str, str] = {}
    for u in urls:
        lt = _suffix_letter(u)
        if not lt:
            continue
        cur = best.get(lt)
        if not cur:
            best[lt] = u
            continue
        # предпочтение mp3
        if cur.lower().endswith(".mp3") and not u.lower().endswith(".mp3"):
            continue
        if not cur.lower().endswith(".mp3") and u.lower().endswith(".mp3"):
            best[lt] = u

    ordered: List[str] = []
    for lt in letters:
        u = best.get(lt)
        if u:
            ordered.append(u)
    return ordered


def _extract_redeye_no(html: str) -> Optional[str]:
    """Найти номер релиза из HTML по фразе 'Redeye No. <digits>'."""
    m = re.search(r"Redeye\s+No\.\s*([0-9]{3,})", html, flags=re.I)
    return m.group(1) if m else None


def _fallback_fill_missing(
    html: str, urls_ordered: List[str], letters: List[str]
) -> List[str]:
    """
    Достроить недостающие mp3 по буквам (если их меньше, чем кнопок).
    Префикс и номер релиза берём сначала из уже пойманных URL, затем — из HTML, иначе — стандартный префикс.
    """
    if not letters or len(urls_ordered) >= len(letters):
        return urls_ordered

    # 1) пробуем вытащить prefix/number из уже имеющихся ссылок
    prefix: Optional[str] = None
    number: Optional[str] = None
    for u in urls_ordered:
        m = re.search(r"^(https?://.*/)(\d+)[a-z]?\.mp3(?:\?.*)?$", u, flags=re.I)
        if m:
            prefix, number = m.group(1), m.group(2)
            break

    # 2) если номера нет — попробуем из HTML
    if not number:
        number = _extract_redeye_no(html)

    # 3) дефолтный prefix
    if not prefix:
        prefix = "https://sounds.redeyerecords.co.uk/"

    if not number:
        return urls_ordered

    present = set(urls_ordered)
    out = list(urls_ordered)

    for lt in letters:
        suffix = "" if lt == "a" else lt
        cand = f"{prefix}{number}{suffix}.mp3"
        if cand in present:
            continue
        try:
            r = requests.head(cand, timeout=FALLBACK_HTTP_TIMEOUT)
            if r.status_code in MEDIA_OK_STATUSES:
                out.append(cand)
                present.add(cand)
                logger.info("[capture:fallback] добавлена недостающая ссылка: %s", cand)
        except Exception as e:
            logger.debug("[capture:fallback] не удалось проверить %s (%s)", cand, e)

    # вернём в нужном порядке букв
    return _map_urls_by_letters(out, letters)


def _wait_new_urls(
    bag: Dict[str, None], before: int, timeout_sec: float, page: Page
) -> None:
    """Ожидаем появления новых URL в сниффере после клика по кнопке."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if len(bag) > before:
            return
        page.wait_for_timeout(PER_CLICK_WAIT_TICK_MS)


# ---------------------------------------------------------------------------
# ОСНОВНАЯ ФУНКЦИЯ СБОРА
# ---------------------------------------------------------------------------


def collect_redeye_media_urls(
    page_url: str,
    *,
    per_click_timeout_sec: int = 5,
    debug: bool = False,
    global_max_sec: int = 25,
) -> List[str]:
    """
    Собрать список mp3-ссылок со страницы Redeye.

    Ускорения и стабильность:
    - Быстрый проход по всем кнопкам (маленькие ожидания);
    - Ранний выход, как только набрали нужное количество ссылок;
    - Небольшая «доохота» ответов (2 секунды);
    - Формирование порядка ссылок строго по буквам (a<b<c<d...);
    - Быстрый fallback через HEAD, если не хватило.
    """
    urls_ordered: List[str] = []
    urls_raw: List[str] = []
    browser = None
    t0 = time.monotonic()

    logger.info("[capture] открываем страницу: %s", page_url)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--autoplay-policy=no-user-gesture-required"],
            )
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_default_timeout(3000)
            page_url = normalize_redeye_url(page_url)  # --- добавлено ---
            logger.info("[capture] открываем страницу: %s", page_url)
            page.goto(page_url, wait_until="domcontentloaded")

            _dismiss_cookie_banners(page)

            letters = _btn_letters(page)  # например ['a','b','c','d']
            total = len(letters)
            if total == 0:
                logger.warning(
                    "[capture] на странице нет кнопок плеера (%s)", BTN_QUERY
                )
                return []

            bag = _wire_sniffer(page)
            btns = page.locator(BTN_QUERY)

            # Фаза 1: быстрый проход кликов
            for idx in range(total):
                before = len(bag)
                try:
                    btn = btns.nth(idx)
                    logger.debug("[capture] клик по кнопке #%s", idx + 1)
                    btn.scroll_into_view_if_needed()
                    btn.click(timeout=1500)
                    _wait_new_urls(bag, before, per_click_timeout_sec, page)
                except PWTimeout:
                    logger.debug("[capture] таймаут ожидания после клика idx=%s", idx)
                except PWError as e:
                    logger.debug("[capture] ошибка клика idx=%s: %s", idx, e)

                # Ранний выход: собрали достаточно
                if len(bag) >= total:
                    break

                # Ограничение общего времени
                if time.monotonic() - t0 > global_max_sec:
                    logger.debug("[capture] достигнут общий мягкий таймаут сбора")
                    break

            # Фаза 2: короткая «доохота» (до 2 сек суммарно)
            end_deadline = time.monotonic() + 2.0
            while len(bag) < total and time.monotonic() < end_deadline:
                page.wait_for_timeout(100)

            urls_raw = list(bag.keys())
            if debug:
                logger.debug("[capture] сырые ссылки: %s", urls_raw)

            # Упорядочиваем по буквам
            urls_ordered = _map_urls_by_letters(urls_raw, letters)

            # Если не хватило — достроим и вернём в нужном порядке
            if len(urls_ordered) < total:
                html = page.content()
                urls_ordered = _fallback_fill_missing(html, urls_ordered, letters)
                if debug:
                    logger.debug(
                        "[capture] HTML-сниппет (первые 1800 символов): %s", html[:1800]
                    )

    except Exception as e:
        logger.exception("[capture] непредвиденная ошибка сбора: %s", e)
        return []
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass

    elapsed = time.monotonic() - t0
    logger.info(
        "[capture] завершено: %d/%d ссылок; время=%.1fs",
        len(urls_ordered),
        total,
        elapsed,
    )
    return urls_ordered


# ---------------------------------------------------------------------------
# РУЧНОЙ ТЕСТ
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    page_url = input("Вставьте URL страницы Redeye: ").strip()
    if not page_url:
        raise SystemExit("URL не указан.")
    out = collect_redeye_media_urls(page_url, per_click_timeout_sec=12)
    if not out:
        print("[]")
        raise SystemExit(4)
    print("\n".join(out))
