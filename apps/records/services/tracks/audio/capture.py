# from __future__ import annotations
#
# import logging
# import re
# import time
# from collections import OrderedDict
# from typing import List, Optional
#
# import requests
# from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout, sync_playwright
#
# logger = logging.getLogger(__name__)
#
# MEDIA_OK_STATUSES = {200, 206}
# MEDIA_CT_HINTS = ("audio/", "mpegurl", "application/vnd.apple.mpegurl")
# MEDIA_URL_HINTS = (".mp3", ".aac", ".m3u8", "/stream/")
# BTN_QUERY = ".play a.btn-play[data-sample]"
# COOKIE_SELECTORS = (
#     'button:has-text("Accept")',
#     'button:has-text("I Agree")',
#     'text=Accept all',
#     'button:has-text("OK")',
#     'button[aria-label="Accept"]',
# )
# FALLBACK_HTTP_TIMEOUT = 5  # сек
# PER_CLICK_WAIT_TICK_MS = 120  # тик в _wait_new_urls
#
#
# def _dismiss_cookie_banners(page) -> None:
#     for sel in COOKIE_SELECTORS:
#         try:
#             loc = page.locator(sel).first
#             if loc.count() and loc.is_visible():
#                 loc.click()
#                 break
#         except PWError:
#             pass
#
#
# def _is_audio_like(resp) -> bool:
#     status = getattr(resp, "status", None)
#     url = (getattr(resp, "url", "") or "").lower()
#     headers = (getattr(resp, "headers", {}) or {})
#     if not isinstance(headers, dict):
#         headers = {}
#     ct = ""
#     for k, v in headers.items():
#         if isinstance(k, str) and k.lower() == "content-type":
#             ct = (v or "").lower()
#             break
#     if status not in MEDIA_OK_STATUSES:
#         return False
#     return any(h in ct for h in MEDIA_CT_HINTS) or any(h in url for h in MEDIA_URL_HINTS)
#
#
# def _btn_count(page) -> int:
#     try:
#         return page.locator(BTN_QUERY).count()
#     except PWError:
#         return 0
#
#
# def _btn_letters(page) -> List[str]:
#     letters: List[str] = []
#     try:
#         btns = page.locator(BTN_QUERY)
#         n = btns.count()
#         for i in range(n):
#             ds = (btns.nth(i).get_attribute("data-sample") or "").strip().lower()
#             if ds:
#                 letters.append(ds)
#     except PWError:
#         pass
#     return letters
#
#
# def _wire_sniffer(page):
#     bag: "OrderedDict[str, None]" = OrderedDict()
#
#     def _on_response(resp):
#         try:
#             if _is_audio_like(resp):
#                 url = re.sub(r"\s+", "", resp.url)
#                 if url and url not in bag:
#                     bag[url] = None
#                     logger.info("[capture] +audio: %s", url)
#         except Exception:
#             pass
#
#     page.on("response", _on_response)
#     return bag
#
#
# def _wait_new_urls(bag: "OrderedDict[str, None]", before: int, timeout_sec: float, page) -> None:
#     deadline = time.monotonic() + timeout_sec
#     while time.monotonic() < deadline:
#         if len(bag) > before:
#             return
#         try:
#             page.wait_for_timeout(PER_CLICK_WAIT_TICK_MS)
#         except PWError:
#             time.sleep(PER_CLICK_WAIT_TICK_MS / 1000.0)
#
#
# def _extract_redeye_no(html: str) -> Optional[str]:
#     m = re.search(r"Redeye\s+No\.\s*([0-9]{3,})", html, flags=re.I)
#     return m.group(1) if m else None
#
#
# def _fallback_fill_missing(html: str, urls: List[str], letters: List[str]) -> List[str]:
#     """
#     Достраиваем недостающие ссылки по DOM-списку букв data-sample: ['a','b','c','d', ...]
#     Суффикс для 'a' — '', для остальных — сама буква.
#     """
#     if not letters or len(urls) >= len(letters):
#         return urls
#
#     no = _extract_redeye_no(html)
#     if not no:
#         return urls
#
#     prefix = None
#     for u in urls:
#         m = re.search(r"^(https?://.*/)" + re.escape(no) + r"[a-z]?\.mp3(?:\?.*)?$", u, flags=re.I)
#         if m:
#             prefix = m.group(1)
#             break
#     if not prefix:
#         prefix = "https://sounds.redeyerecords.co.uk/"
#
#     present = set(urls)
#     out = list(urls)
#
#     for idx, letter in enumerate(letters):
#         suffix = "" if letter == "a" else letter
#         cand = f"{prefix}{no}{suffix}.mp3"
#         if cand in present:
#             continue
#         try:
#             r = requests.get(cand, stream=True, timeout=FALLBACK_HTTP_TIMEOUT)
#             if r.status_code in (200, 206):
#                 out.append(cand)
#                 present.add(cand)
#                 logger.info("[capture:fallback] +%s", cand)
#             r.close()
#         except Exception:
#             pass
#
#     return out
#
#
# def collect_redeye_media_urls(page_url: str, *, per_click_timeout_sec: int = 12) -> List[str]:
#     urls: List[str] = []
#     browser = None
#
#     try:
#         with sync_playwright() as pw:
#             try:
#                 browser = pw.chromium.launch(
#                     channel="chrome",
#                     headless=True,
#                     args=["--autoplay-policy=no-user-gesture-required"],
#                 )
#             except PWError:
#                 browser = pw.chromium.launch(
#                     headless=True,
#                     args=["--autoplay-policy=no-user-gesture-required"],
#                 )
#
#             ctx = browser.new_context()
#             page = ctx.new_page()
#             logger.info("[capture] goto %s", page_url)
#             page.goto(page_url, wait_until="domcontentloaded")
#             _dismiss_cookie_banners(page)
#
#             letters = _btn_letters(page)  # ['a','b','c','d', ...]
#             total = len(letters)
#             if total <= 0:
#                 logger.info("[capture] no .btn-play[data-sample] on page")
#                 return []
#
#             bag = _wire_sniffer(page)
#             btns = page.locator(BTN_QUERY)
#
#             for idx in range(total):
#                 before = len(bag)
#                 btn = btns.nth(idx)
#                 try:
#                     btn.scroll_into_view_if_needed()
#                     btn.click()
#                     _wait_new_urls(bag, before, per_click_timeout_sec, page)
#                 except PWTimeout:
#                     logger.warning("[capture] timeout after click idx=%s", idx)
#                 except PWError as e:
#                     logger.warning("[capture] click idx=%s failed: %s", idx, e)
#
#             urls = list(bag.keys())
#
#             # если собрали меньше, чем кнопок — попробуем достроить по буквам
#             if len(urls) < total:
#                 html = page.content()
#                 urls = _fallback_fill_missing(html, urls, letters)
#
#     finally:
#         try:
#             if browser:
#                 browser.close()
#         except Exception:
#             pass
#
#     return urls
#
#
# if __name__ == "__main__":
#     page_url = input("Вставьте URL страницы Redeye: ").strip()
#     if not page_url:
#         raise SystemExit("URL не указан.")
#     out = collect_redeye_media_urls(page_url, per_click_timeout_sec=12)
#     if not out:
#         print("[]")
#         raise SystemExit(4)
#     print("\n".join(out))
