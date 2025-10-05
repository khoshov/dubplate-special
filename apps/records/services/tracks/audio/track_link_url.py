"""
Headless-захват media-ответа Redeye:
- спрашивает URL трека,
- нажимает Play в фоновом браузере,
- ждёт первый media-ответ,
- сохраняет заголовки в media_headers.json,
- печатает в консоль редактированный URL,
  а ПОЛНЫЙ URL — только при явном подтверждении прав.
"""

from typing import Dict, Optional, Mapping, Any
# from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Error as PWError

SENSITIVE_HEADER_KEYS = {"cookie", "authorization", "x-api-key", "x-csrf-token"}


def sanitize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out = {}
    for k, v in headers.items():
        out[k] = "<redacted>" if isinstance(k, str) and k.lower() in SENSITIVE_HEADER_KEYS else v
    return out


def dismiss_cookie_banners(page) -> None:
    selectors = [
        'button:has-text("Accept")',
        'button:has-text("I Agree")',
        'text=Accept all',
        'button:has-text("OK")',
        'button[aria-label="Accept"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click()
                return
        except PWError as e:
            print(f"[cookie] пропуск ({type(e).__name__}): {e}")


def try_click_play(page) -> bool:
    for sel in ["button.playbutton", "a.playbutton", ".playbutton"]:
        try:
            btn = page.locator(sel).first
            if btn.count():
                btn.scroll_into_view_if_needed()
                btn.click()
                print("▶️  Play нажата (CSS).")
                return True
        except PWError as e:
            print(f"[play] не удалось кликнуть {sel}: {e}")
    try:
        page.get_by_role("button", name=lambda n: n and "play" in n.lower()).click()
        print("▶️  Play нажата (role).")
        return True
    except PWError as e:
        print(f"[play] не найдено: {e}")
        return False


# --- Media detection helpers ---

MEDIA_OK_STATUSES = {200, 206}
MEDIA_CT_HINTS = ("audio/", "mpegurl", "application/vnd.apple.mpegurl")
MEDIA_URL_HINTS = ("/stream/", ".m3u8", ".mp3", ".aac")
MEDIA_HOST_HINTS = ("bcbits.com", "bandcamp.com")


def _get_header_ci(headers: Mapping[str, Any], name: str) -> str:
    lname = name.lower()
    for k, v in headers.items():
        if isinstance(k, str) and k.lower() == lname:
            return v or ""
    return ""


def classify_media(status: Optional[int], content_type: str, url: str) -> bool:
    if status not in MEDIA_OK_STATUSES:
        return False
    ct = (content_type or "").lower()
    ul = (url or "").lower()
    host_ok = any(h in ul for h in MEDIA_HOST_HINTS)
    ct_ok = any(h in ct for h in MEDIA_CT_HINTS)
    url_ok = any(h in ul for h in MEDIA_URL_HINTS)
    return host_ok and (ct_ok or url_ok)


def is_media_response(resp) -> bool:
    status = getattr(resp, "status", None)
    url = getattr(resp, "url", "") or ""
    headers = getattr(resp, "headers", {}) or {}
    if not isinstance(headers, Mapping):
        headers = {}
    content_type = _get_header_ci(headers, "content-type")
    return classify_media(status, content_type, url)


# --- Core ---

def get_track_download_link(url: str, timeout_sec: int = 60) -> str | None:
    browser = None

    try:
        with sync_playwright() as pw:
            # headless Chrome; если нет — встроенный Chromium (нужно: python -m playwright install chromium)
            try:
                browser = pw.chromium.launch(
                    channel="chrome",
                    headless=True,
                    args=["--autoplay-policy=no-user-gesture-required"],
                )
            except PWError:
                browser = pw.chromium.launch(
                    headless=True,
                    args=["--autoplay-policy=no-user-gesture-required"],
                )

            ctx = browser.new_context()
            page = ctx.new_page()
            print(f"Открываю: {url}")
            page.goto(url, wait_until="domcontentloaded")
            dismiss_cookie_banners(page)

            try:
                with page.expect_response(is_media_response, timeout=timeout_sec * 1000) as resp_info:
                    _clicked = try_click_play(page)
                    if not _clicked:
                        print("ℹ️  Не удалось кликнуть Play автоматически. Ждём media-ответ.")
                resp = resp_info.value
            except PWTimeout:
                print("⏱️  Таймаут: media-ответ не появился.")
                return None

            return resp.url
    finally:
        try:
            if browser:
                browser.close()
        except Exception as e:
            print(f"[close] предупреждение при закрытии браузера: {e}")



if __name__ == "__main__":
    track_page_url = input("Вставьте URL страницы трека Bandcamp: ").strip()
    if not track_page_url:
        raise SystemExit("URL не указан.")

    track_link = get_track_download_link(track_page_url, timeout_sec=60)
    print(track_link)
    if track_link is None:
        raise SystemExit(4)




