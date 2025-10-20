from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from typing import Dict, List, Optional, OrderedDict as OrderedDictType

import requests
from playwright.sync_api import (
    Page,
    TimeoutError as PWTimeout,
    Error as PWError,
    sync_playwright,
)

from records.constants import (
    REDEYE_PLAYER_BUTTON_SELECTOR,
    REDEYE_COOKIE_SELECTORS,
    MEDIA_ACCEPTABLE_HTTP_STATUSES,
    MEDIA_CONTENT_TYPE_HINTS,
    MEDIA_URL_HINTS,
    CAPTURE_FALLBACK_HTTP_TIMEOUT,
    CAPTURE_WAIT_TICK_MS,
    CAPTURE_GLOBAL_TIMEOUT_SEC,
    CAPTURE_POST_CLICK_SETTLE_SEC,
    REDEYE_SOUNDS_BASE_URL,
)
from records.services.providers.redeye.helpers import normalize_abs_url

logger = logging.getLogger(__name__)


def _dismiss_cookie_banners(page: Page) -> None:
    """Метод закрывает cookie-баннеры по набору селекторов (если присутствуют)."""
    for selector in REDEYE_COOKIE_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                logger.debug("[capture] закрытие cookie-баннера: %s", selector)
                locator.click(timeout=1_000)
                break
        except PWError:
            continue


def _is_audio_like_response(resp: object) -> bool:
    """Метод проверяет, похож ли сетевой ответ Playwright на аудио-ресурс.

    Критерии:
      - Код ответа в MEDIA_ACCEPTABLE_HTTP_STATUSES.
      - Content-Type содержит подсказки MEDIA_CONTENT_TYPE_HINTS ИЛИ
        URL содержит фрагменты из MEDIA_URL_HINTS.
    """
    status = getattr(resp, "status", None)
    url = (getattr(resp, "url", "") or "").lower()
    headers = getattr(resp, "headers", {}) or {}
    content_type = str(headers.get("content-type", "")).lower()

    if status not in MEDIA_ACCEPTABLE_HTTP_STATUSES:
        return False
    return any(hint in content_type for hint in MEDIA_CONTENT_TYPE_HINTS) or any(
        hint in url for hint in MEDIA_URL_HINTS
    )


def _attach_response_sniffer(page: Page) -> OrderedDictType[str, None]:
    """Метод подписывает перехватчик на `page.on('response')` и собирает аудио-URL.

    Возвращает:
        OrderedDict[str, None]: упорядоченная коллекция без дублей (сохраняет порядок находок).
    """
    collected: OrderedDictType[str, None] = OrderedDict()

    def _on_response(resp: object) -> None:
        try:
            if _is_audio_like_response(resp):
                raw_url = getattr(resp, "url", "")
                url = normalize_abs_url(re.sub(r"\s+", "", raw_url))
                if url and url not in collected:
                    collected[url] = None
                    logger.debug("[capture] обнаружен аудио-ответ: %s", url)
        except Exception as err:
            logger.debug("[capture] ошибка сниффера ответа: %s", err)

    page.on("response", _on_response)
    return collected


def _collect_button_letters(page: Page) -> List[str]:
    """Метод извлекает буквы (data-sample) с кнопок плеера и сортирует их по алфавиту.

    Это гарантирует стабильное сопоставление «буква → трек» вне зависимости от порядка в разметке.
    """
    unique_letters: set[str] = set()
    try:
        buttons = page.locator(REDEYE_PLAYER_BUTTON_SELECTOR)
        count = buttons.count()
        for i in range(count):
            data_sample = (
                (buttons.nth(i).get_attribute("data-sample") or "").strip().lower()
            )
            if data_sample:
                unique_letters.add(data_sample)
    except PWError:
        pass

    letters = sorted(unique_letters)
    logger.debug("[capture] буквы плеера (отсортированы): %s", letters)
    return letters


def _extract_suffix_letter(url: str) -> str:
    """Метод извлекает буквенный суффикс трека из имени файла URL (a/b/c/...).

    Примеры:
        /123456a.mp3  → 'a'
        /123456.mp3   → 'a' (по умолчанию)
        /123456c.m3u8 → 'c'
    """
    m = re.search(r"/(\d+)([a-z]?)\.mp3(?:\?.*)?$", url, flags=re.I)
    if m:
        return m.group(2) or "a"
    m2 = re.search(r"/(\d+)([a-z]?)\.(m3u8|aac)(?:\?.*)?$", url, flags=re.I)
    if m2:
        return m2.group(2) or "a"
    return ""


def _map_urls_by_letters(urls: List[str], letters: List[str]) -> List[str]:
    """Метод упорядочивает ссылки в соответствии с буквами `letters`.

    Для каждой буквы выбирается «лучший» URL:
      - приоритет .mp3 над .m3u8/.aac.
    """
    best: Dict[str, str] = {}
    for url in urls:
        letter = _extract_suffix_letter(url)
        if not letter:
            continue
        current = best.get(letter)
        if not current:
            best[letter] = url
            continue
        cur_mp3 = current.lower().endswith(".mp3")
        url_mp3 = url.lower().endswith(".mp3")
        if not cur_mp3 and url_mp3:
            best[letter] = url

    ordered: List[str] = []
    for letter in letters:
        url = best.get(letter)
        if url:
            ordered.append(url)
    return ordered


def _extract_redeye_number_from_html(html: str) -> Optional[str]:
    """Метод извлекает «Redeye No.» (число) из HTML карточки, либо None."""
    m = re.search(r"Redeye\s+No\.\s*([0-9]{3,})", html, flags=re.I)
    return m.group(1) if m else None


def _fallback_fill_missing(
    html: str, urls_ordered: List[str], letters: List[str]
) -> List[str]:
    """Метод достраивает недостающие mp3-ссылки по буквам при нехватке.

    Правила:
      - Префикс и номер релиза берутся из уже пойманных ссылок;
      - Если номер не найден — ищется в HTML по «Redeye No.»;
      - Префикс по умолчанию — `REDEYE_SOUNDS_BASE_URL`;
      - Каждая кандидатная ссылка проверяется HEAD-запросом; при 200/206 — добавляется.
    """
    if not letters or len(urls_ordered) >= len(letters):
        return urls_ordered

    prefix: Optional[str] = None
    number: Optional[str] = None
    for url in urls_ordered:
        m = re.search(r"^(https?://.*/)(\d+)[a-z]?\.mp3(?:\?.*)?$", url, flags=re.I)
        if m:
            prefix, number = m.group(1), m.group(2)
            break

    if not number:
        number = _extract_redeye_number_from_html(html)
    if not prefix:
        prefix = REDEYE_SOUNDS_BASE_URL
    if not number:
        return urls_ordered

    present = set(urls_ordered)
    out = list(urls_ordered)

    for letter in letters:
        suffix = "" if letter == "a" else letter
        candidate = f"{prefix}{number}{suffix}.mp3"
        if candidate in present:
            continue
        try:
            resp = requests.head(candidate, timeout=CAPTURE_FALLBACK_HTTP_TIMEOUT)
            if resp.status_code in MEDIA_ACCEPTABLE_HTTP_STATUSES:
                out.append(candidate)
                present.add(candidate)
                logger.info(
                    "[capture:fallback] добавлена недостающая ссылка: %s", candidate
                )
        except requests.RequestException as req_err:
            logger.debug(
                "[capture:fallback] ошибка проверки %s: %s", candidate, req_err
            )

    return _map_urls_by_letters(out, letters)


def _wait_new_urls(
    bag: Dict[str, None], before_count: int, timeout_sec: float, page: Page
) -> None:
    """Метод ожидает появления новых URL в «сниффере» после клика по кнопке."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if len(bag) > before_count:
            return
        page.wait_for_timeout(CAPTURE_WAIT_TICK_MS)


def collect_redeye_audio_urls(
    page_url: str,
    *,
    per_click_timeout_sec: int = 5,
    debug: bool = False,
    global_max_sec: float = CAPTURE_GLOBAL_TIMEOUT_SEC,
) -> List[str]:
    """
    Метод собирает прямые ссылки на аудио с плеера Redeye.

    Алгоритм:
      1) Открывает карточку в headless-браузере.
      2) По очереди кликает по кнопкам (a,b,c,...) и перехватывает медиа-URL.
      3) Упорядочивает ссылки по буквам; при нехватке пытается достроить по HTML.

    Args:
        page_url (str): URL карточки товара Redeye.
        per_click_timeout_sec (int): Таймаут ожидания после каждого клика по кнопке.
        debug (bool): Включает подробный вывод сырых ссылок и сниппета HTML.
        global_max_sec (float): Общий мягкий предел времени для сбора ссылок (сек).

    Returns:
        List[str]: Упорядоченный список URL аудио в порядке букв.
    """
    logger.info("[capture] открываем страницу: %s", page_url)

    urls_ordered: List[str] = []
    browser = None
    start_ts = time.monotonic()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--autoplay-policy=no-user-gesture-required"],
            )
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(3_000)
            page.goto(page_url, wait_until="domcontentloaded")

            _dismiss_cookie_banners(page)

            letters = _collect_button_letters(page)
            total = len(letters)
            if total == 0:
                logger.warning(
                    "[capture] на странице нет кнопок плеера: %s",
                    REDEYE_PLAYER_BUTTON_SELECTOR,
                )
                urls_ordered = []
            else:
                bag = _attach_response_sniffer(page)
                buttons = page.locator(REDEYE_PLAYER_BUTTON_SELECTOR)

                for idx in range(total):
                    before_count = len(bag)
                    try:
                        btn = buttons.nth(idx)
                        logger.debug("[capture] клик по кнопке #%s", idx + 1)
                        btn.scroll_into_view_if_needed()
                        btn.click(timeout=1_500)
                        _wait_new_urls(bag, before_count, per_click_timeout_sec, page)
                    except PWTimeout:
                        logger.debug(
                            "[capture] таймаут ожидания после клика idx=%s", idx
                        )
                    except PWError as pw_err:
                        logger.debug("[capture] ошибка клика idx=%s: %s", idx, pw_err)

                    if len(bag) >= total:
                        break
                    if time.monotonic() - start_ts > global_max_sec:
                        logger.debug("[capture] достигнут общий мягкий таймаут сбора")
                        break

                end_deadline = time.monotonic() + CAPTURE_POST_CLICK_SETTLE_SEC
                while len(bag) < total and time.monotonic() < end_deadline:
                    page.wait_for_timeout(100)

                urls_raw = list(bag.keys())
                if debug:
                    logger.debug("[capture] сырые ссылки: %s", urls_raw)

                urls_ordered = _map_urls_by_letters(urls_raw, letters)

                if len(urls_ordered) < total:
                    html = page.content()
                    urls_ordered = _fallback_fill_missing(html, urls_ordered, letters)
                    if debug:
                        logger.debug(
                            "[capture] HTML-сниппет (первые 1800 символов): %s",
                            html[:1800],
                        )

    except PWError as pw_exc:
        logger.exception("[capture] ошибка Playwright: %s", pw_exc)
        urls_ordered = []
    except Exception as exc:
        logger.exception("[capture] непредвиденная ошибка сбора: %s", exc)
        urls_ordered = []
    finally:
        if browser:
            try:
                browser.close()
            except PWError as close_exc:
                logger.debug("[capture] ошибка закрытия браузера: %s", close_exc)

    logger.info("[capture] собрано ссылок: %d", len(urls_ordered))
    return urls_ordered


if __name__ == "__main__":
    _url = input("Вставьте URL страницы Redeye: ").strip()
    if not _url:
        raise SystemExit("URL не указан.")
    _out = collect_redeye_audio_urls(_url, per_click_timeout_sec=12, debug=True)
    if not _out:
        print("[]")
        raise SystemExit(4)
    print("\n".join(_out))
