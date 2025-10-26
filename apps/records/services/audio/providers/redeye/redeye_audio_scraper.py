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
    Browser,
    ViewportSize,
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
    REDEYE_PLAYER_DEFAULT_CLICK_TIMEOUT_SEC,
    PLAYWRIGHT_CLICK_ACTION_TIMEOUT_MS,
    PLAYWRIGHT_NAVIGATION_TIMEOUT_MS,
    PLAYWRIGHT_ACTION_TIMEOUT_MS,
    PLAYWRIGHT_WAIT_TICK_MS,
)
from records.services.providers.redeye.helpers import normalize_abs_url

logger = logging.getLogger(__name__)

# --- добавлено: блокировки «тяжёлых» и посторонних ресурсов ---
_THIRDPARTY_BLOCKLIST = (
    "googletagmanager",
    "google-analytics",
    "doubleclick",
    "facebook",
    "twitter",
    "hotjar",
    "segment",
    "sentry",
    "cloudflareinsights",
)
_ALLOW_DOMAIN_HINTS = ("redeyerecords.co.uk", "sounds.redeyerecords.co.uk")


def _install_network_blocker(context) -> None:
    """
    Подключает роутер Playwright и блокирует «тяжёлые» и посторонние ресурсы.

    Блокируются:
      - типы image/stylesheet/font;
      - явные домены аналитики/пикселей.
    Оставляем document/script/xhr/fetch/media — это нужно плееру.
    """

    def _should_abort(req) -> bool:
        rtype = (req.resource_type or "").lower()
        if rtype in {"image", "stylesheet", "font"}:
            return True
        url = (req.url or "").lower()
        if not any(h in url for h in _ALLOW_DOMAIN_HINTS):
            if any(b in url for b in _THIRDPARTY_BLOCKLIST):
                return True
        return False

    def _route(route, request) -> None:
        try:
            if _should_abort(request):
                route.abort()
                return
        except Exception as err:  # noqa: BLE001 — роутер не должен ронять сценарий
            logger.debug("Ошибка в роутере сети: %s", err, exc_info=True)
        route.continue_()

    context.route("**/*", _route)


def _dismiss_cookie_banners(page: Page) -> None:
    """Метод закрывает cookie-баннеры по набору селекторов (если присутствуют)."""
    for selector in REDEYE_COOKIE_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                logger.debug("Закрытие cookie-баннера: %s", selector)
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
                    logger.debug("Обнаружен аудио-ответ: %s", url)  # <-- с заглавной
        except Exception as err:  # noqa: BLE001
            logger.debug("Ошибка сниффера ответа: %s", err)

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
    logger.debug("буквы плеера (отсортированы): %s", letters)
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
                    "Добавлена недостающая ссылка: %s", candidate
                )
        except requests.RequestException as req_err:
            logger.debug(
                "Ошибка проверки %s: %s", candidate, req_err
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

from typing import Literal

WaitUntil = Literal["load", "domcontentloaded", "networkidle", "commit"]


def _safe_goto(
    page: Page,
    url: str,
    *,
    wait_until: WaitUntil = "domcontentloaded",
    retries: int = 1,
) -> bool:
    """
    Пытается открыть страницу с заданным числом повторных попыток при таймауте навигации.

    Поведение:
      • Выполняет до (retries + 1) попыток page.goto(...).
      • На каждой неуспешной попытке (Timeout) пишет DEBUG и пробует ещё раз.
      • Если исчерпаны попытки — пишет WARNING и возвращает False.
      • Любая другая ошибка Playwright логируется кратко и приводит к False.

    Args:
        page: Экземпляр Playwright Page.
        url: Адрес страницы.
        wait_until: Условие ожидания загрузки.
        retries: Количество дополнительных попыток при таймауте (по умолчанию 1).

    Returns:
        True, если навигация успешна; False при таймауте/ошибке.
    """
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until=wait_until)
            return True
        except PWTimeout:
            if attempt < retries:
                logger.debug(
                    "Навигация превысила таймаут, повторная попытка %d/%d: %s",
                    attempt + 1,
                    retries,
                    url,
                )
                continue
            logger.warning(
                "Навигация не уложилась в %d мс: %s — пропуск страницы.",
                PLAYWRIGHT_NAVIGATION_TIMEOUT_MS,
                url,
            )
            return False
        except PWError as err:
            logger.error("Ошибка навигации Playwright к %s: %s", url, err)
            return False
    return False  # недостижимо, но для спокойствия mypy




def _collect_with_browser(
        *,
        active_browser: Browser,
        page_url: str,
        per_click_timeout_sec: int,
        debug: bool,
        global_max_sec: float,
) -> List[str]:
    """
    Выполняет сбор аудио-ссылок внутри уже запущенного браузера (создаёт только context/page).

    Args:
        active_browser: Экземпляр Browser (внешний или временный).
        page_url: URL карточки Redeye.
        per_click_timeout_sec: Таймаут ожидания ссылок после клика по кнопке плеера, сек.
        debug: Включать ли подробные логи.
        global_max_sec: Общий мягкий предел работы, сек.

    Returns:
        Список аудио-URL в порядке кнопок (A, B, C, ...).
    """
    start_ts = time.monotonic()

    viewport: ViewportSize = {"width": 960, "height": 800}  # --- типизировано ---
    context = active_browser.new_context(viewport=viewport, java_script_enabled=True)
    _install_network_blocker(context)

    try:
        page = context.new_page()
        page.set_default_timeout(PLAYWRIGHT_ACTION_TIMEOUT_MS)
        page.set_default_navigation_timeout(PLAYWRIGHT_NAVIGATION_TIMEOUT_MS)  # --- добавлено ---

        # --- добавлено: одна повторная попытка навигации при редком флапе сети ---
        if not _safe_goto(page, page_url, wait_until="domcontentloaded", retries=1):
            return []

        _dismiss_cookie_banners(page)

        letters = _collect_button_letters(page)
        total = len(letters)
        if total == 0:
            logger.warning("На странице нет кнопок плеера: %s", REDEYE_PLAYER_BUTTON_SELECTOR)
            return []

        bag = _attach_response_sniffer(page)
        buttons = page.locator(REDEYE_PLAYER_BUTTON_SELECTOR)

        for idx in range(total):
            before_count = len(bag)
            try:
                btn = buttons.nth(idx)
                logger.debug("Клик по кнопке #%s", idx + 1)
                btn.scroll_into_view_if_needed()
                btn.click(timeout=PLAYWRIGHT_CLICK_ACTION_TIMEOUT_MS)
                _wait_new_urls(bag, before_count, per_click_timeout_sec, page)
            except PWTimeout:
                logger.debug("Таймаут ожидания после клика idx=%s", idx)
            except PWError as pw_err:
                logger.debug("Ошибка клика idx=%s: %s", idx, pw_err)

            if len(bag) >= total:
                break
            if time.monotonic() - start_ts > global_max_sec:
                logger.debug("Достигнут общий мягкий таймаут сбора")
                break

        end_deadline = time.monotonic() + CAPTURE_POST_CLICK_SETTLE_SEC
        while len(bag) < total and time.monotonic() < end_deadline:
            page.wait_for_timeout(PLAYWRIGHT_WAIT_TICK_MS)

        urls_raw = list(bag.keys())
        if debug:
            logger.debug("Сырые ссылки: %s", urls_raw)

        urls_ordered: List[str] = _map_urls_by_letters(urls_raw, letters)

        if len(urls_ordered) < total:
            html = page.content()
            urls_ordered = _fallback_fill_missing(html, urls_ordered, letters)
            if debug and html:
                logger.debug("HTML-сниппет (первые 1800 симв.): %s", html[:1800])

        return urls_ordered

    finally:
        try:
            context.close()
        except PWError as close_exc:
            logger.debug("Ошибка закрытия контекста: %s", close_exc)


def collect_redeye_audio_urls(
        page_url: str,
        *,
        per_click_timeout_sec: Optional[int] = None,
        debug: bool = False,
        global_max_sec: float = CAPTURE_GLOBAL_TIMEOUT_SEC,
        browser: Optional[Browser] = None,
) -> List[str]:
    """
    Собирает прямые ссылки на аудио с плеера Redeye.

    Сценарии:
      • Потоковый: если передан внешний `browser`, новый Chromium не запускается — создаётся только context/page.
      • Разовый: если `browser` не передан, браузер поднимается локально на время вызова и закрывается автоматически.

    Args:
        page_url: URL карточки Redeye.
        per_click_timeout_sec: Таймаут ожидания ссылок после клика по кнопке плеера, сек.
        debug: Включать ли подробные логи.
        global_max_sec: Общий мягкий предел работы, сек.
        browser: Внешний Browser для пакетной обработки.

    Returns:
        Список аудио-URL в порядке кнопок (A, B, C, ...).
    """
    logger.info("Открываем страницу: %s", page_url)
    if per_click_timeout_sec is None:
        per_click_timeout_sec = REDEYE_PLAYER_DEFAULT_CLICK_TIMEOUT_SEC
    try:
        # --- если передан внешний браузер, но он «мертвый», безопасно отфолбэкаем на локальный запуск ---
        if browser is not None:
            try:
                if hasattr(browser, "is_connected") and not browser.is_connected():
                    logger.warning("Получен неактивный браузер (is_connected=False) — используем локальный экземпляр")
                    browser = None
            except PWError as probe_err:
                logger.warning("Ошибка проверки состояния браузера: %s — используем локальный экземпляр", probe_err)
                browser = None

        if browser is not None:
            urls = _collect_with_browser(
                active_browser=browser,
                page_url=page_url,
                per_click_timeout_sec=per_click_timeout_sec,
                debug=debug,
                global_max_sec=global_max_sec,
            )
            logger.info("Собрано ссылок: %d", len(urls))
            return urls

        # Разовый случай — поднимаем собственный браузер
        with sync_playwright() as pw:
            tmp_browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--autoplay-policy=no-user-gesture-required",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                ],
            )
            urls = _collect_with_browser(
                active_browser=tmp_browser,
                page_url=page_url,
                per_click_timeout_sec=per_click_timeout_sec,
                debug=debug,
                global_max_sec=global_max_sec,
            )
            logger.info("Собрано ссылок: %d", len(urls))
            return urls

    except PWTimeout:
        logger.warning("Таймаут Playwright при обработке %s — страница пропущена.", page_url)
        if debug:
            logger.exception("Детали таймаута:")
        return []
    except PWError as pw_err:
        logger.error("Ошибка Playwright при обработке %s: %s", page_url, pw_err)
        if debug:
            logger.exception("Детали ошибки Playwright:")
        return []
    except Exception as exc:  # noqa: BLE001 — предохранитель
        logger.error("Неожиданная ошибка при обработке %s: %s", page_url, exc)
        if debug:
            logger.exception("Детали непредвиденной ошибки:")
        return []


if __name__ == "__main__":
    _url = input("Вставьте URL страницы Redeye: ").strip()
    if not _url:
        raise SystemExit("URL не указан.")
    _out = collect_redeye_audio_urls(_url, per_click_timeout_sec=12, debug=True)
    if not _out:
        print("[]")
        raise SystemExit(4)
    print("\n".join(_out))
