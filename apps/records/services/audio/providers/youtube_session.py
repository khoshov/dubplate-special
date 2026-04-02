from __future__ import annotations

import contextlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from playwright.sync_api import (
    BrowserContext,
    Error as PWError,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

from config.logging import NOTICE_LEVEL, log_event
from records.constants import (
    YOUTUBE_BROWSER_KEYRING,
    YOUTUBE_BROWSER_NAME,
    YOUTUBE_SESSION_LOGIN_POLL_MS,
    YOUTUBE_SESSION_LOGIN_SUCCESS_WAIT_MS,
    YOUTUBE_SESSION_LOGIN_TIMEOUT_MS,
    YOUTUBE_SESSION_LOGIN_URL,
    YOUTUBE_SESSION_LOCK_WAIT_MS,
    YOUTUBE_SESSION_REFRESH_URL,
    YOUTUBE_SESSION_REFRESH_WAIT_MS,
)

logger = logging.getLogger(__name__)
_YOUTUBE_SESSION_COMPONENT = "youtube_session"

_THIRDPARTY_BLOCKLIST = (
    "doubleclick",
    "facebook",
    "googletagmanager",
    "google-analytics",
    "hotjar",
    "segment",
    "sentry",
    "twitter",
)
_ALLOW_DOMAIN_HINTS = (
    "accounts.google.com",
    "google.com",
    "googlevideo.com",
    "gstatic.com",
    "youtube.com",
    "ytimg.com",
)
_REFRESH_LOCK_STALE_SEC = 60 * 10
_AUTHENTICATED_COOKIE_NAMES = frozenset(
    {
        "SAPISID",
        "APISID",
        "SSID",
        "SID",
        "HSID",
        "__Secure-1PSID",
        "__Secure-3PSID",
        "LOGIN_INFO",
    }
)
_AUTHENTICATED_COOKIE_DOMAIN_HINTS = (
    "youtube.com",
    "google.com",
)
_GOOGLE_LOGIN_EMAIL_SELECTOR = 'input[type="email"]'
_GOOGLE_LOGIN_PASSWORD_SELECTOR = 'input[type="password"]'
_LOCAL_UI_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_INTERACTIVE_LOGIN_INTERNAL_PAGE_PREFIXES = (
    "chrome://",
    "edge://",
)


def _log_youtube_session_event(
    level: int,
    event: str,
    message: str,
    **context: Any,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_YOUTUBE_SESSION_COMPONENT,
        event=event,
        **context,
    )


@dataclass(frozen=True)
class YouTubeSessionRefreshResult:
    """Итог операции поддержания YouTube-сессии."""

    refreshed: bool
    profile_ready: bool
    waited_for_existing_refresh: bool = False
    message: str = ""


@dataclass(frozen=True)
class YouTubeSessionLoginResult:
    """Итог интерактивного логина в persistent YouTube profile."""

    logged_in: bool
    profile_ready: bool
    waited_for_existing_refresh: bool = False
    timed_out: bool = False
    message: str = ""


class YouTubeSessionService:
    """Поддерживает долгоживущий профиль браузера для YouTube."""

    @classmethod
    def profile_dir(cls) -> Path:
        path = Path(str(getattr(settings, "YOUTUBE_BROWSER_PROFILE_DIR", "") or ""))
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def lock_file(cls) -> Path:
        lock_path = Path(
            str(getattr(settings, "YOUTUBE_SESSION_LOCK_FILE", "") or "")
        ).resolve()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        return lock_path

    @classmethod
    def browser_name(cls) -> str:
        return YOUTUBE_BROWSER_NAME

    @classmethod
    def browser_keyring(cls) -> str | None:
        configured_value = str(YOUTUBE_BROWSER_KEYRING or "").strip()
        return configured_value or None

    @classmethod
    def login_email(cls) -> str:
        return str(getattr(settings, "YOUTUBE_LOGIN_EMAIL", "") or "").strip()

    @classmethod
    def login_password(cls) -> str:
        return str(getattr(settings, "YOUTUBE_LOGIN_PASSWORD", "") or "").strip()

    @classmethod
    def ui_url(cls) -> str:
        return str(
            getattr(settings, "YOUTUBE_SESSION_UI_URL", "")
            or "http://localhost:6080/vnc.html?autoconnect=1&resize=scale"
        ).strip()

    @classmethod
    def resolved_ui_url(cls, request: Any | None = None) -> str:
        configured_url = cls.ui_url()
        if not configured_url or request is None:
            return configured_url

        try:
            parsed_url = urlsplit(configured_url)
        except ValueError:
            return configured_url

        if (parsed_url.hostname or "").lower() not in _LOCAL_UI_HOSTS:
            return configured_url

        get_host = getattr(request, "get_host", None)
        if not callable(get_host):
            return configured_url

        try:
            request_host = str(get_host() or "").strip()
        except Exception:  # noqa: BLE001
            return configured_url
        if not request_host:
            return configured_url

        request_host_parts = urlsplit(f"//{request_host}")
        target_host = str(request_host_parts.hostname or "").strip()
        if not target_host:
            return configured_url

        if ":" in target_host and not target_host.startswith("["):
            target_host = f"[{target_host}]"

        target_port = parsed_url.port
        rewritten_netloc = (
            f"{target_host}:{target_port}" if target_port is not None else target_host
        )
        return urlunsplit(
            (
                parsed_url.scheme,
                rewritten_netloc,
                parsed_url.path,
                parsed_url.query,
                parsed_url.fragment,
            )
        )

    @classmethod
    def login_url(cls) -> str:
        return YOUTUBE_SESSION_LOGIN_URL

    @classmethod
    def profile_has_cookie_store(cls) -> bool:
        return any(cls.profile_dir().rglob("Cookies"))

    @classmethod
    def profile_is_ready(cls) -> bool:
        if not cls.profile_has_cookie_store():
            return False
        state = cls._get_state()
        return state.status == state.Status.HEALTHY

    @classmethod
    def profile_allows_cookie_reuse(cls) -> bool:
        if not cls.profile_has_cookie_store():
            return False
        state = cls._get_state()
        return state.status in {
            state.Status.HEALTHY,
            state.Status.UNKNOWN,
        }

    @classmethod
    def resolve_cookies_from_browser(cls) -> tuple[str, str, str | None, None] | None:
        if not cls.profile_allows_cookie_reuse():
            return None
        return (
            cls.browser_name(),
            str(cls.profile_dir()),
            cls.browser_keyring(),
            None,
        )

    @classmethod
    def interactive_login(
        cls,
        *,
        timeout_ms: int | None = None,
    ) -> YouTubeSessionLoginResult:
        """Открывает headful Chromium и ждёт ручной авторизации в YouTube."""
        if not os.environ.get("DISPLAY"):
            return YouTubeSessionLoginResult(
                logged_in=False,
                profile_ready=cls.profile_is_ready(),
                message=(
                    "Переменная DISPLAY не задана. "
                    "Запустите команду внутри youtube_session_login."
                ),
            )

        if cls.profile_is_ready():
            return YouTubeSessionLoginResult(
                logged_in=True,
                profile_ready=True,
                message=(
                    "YouTube-сессия уже подтверждена. Повторная интерактивная "
                    "авторизация не требуется."
                ),
            )

        lock_fd = cls._acquire_lock()
        waited_for_existing_refresh = False
        if lock_fd is None:
            waited_for_existing_refresh = cls._wait_for_lock_release()
            return YouTubeSessionLoginResult(
                logged_in=False,
                profile_ready=cls.profile_is_ready(),
                waited_for_existing_refresh=waited_for_existing_refresh,
                message=(
                    "Ожидание завершения уже запущенного обновления профиля."
                    if waited_for_existing_refresh
                    else "Не удалось дождаться освобождения блокировки профиля."
                ),
            )

        login_timeout_ms = timeout_ms or YOUTUBE_SESSION_LOGIN_TIMEOUT_MS
        poll_ms = YOUTUBE_SESSION_LOGIN_POLL_MS
        success_wait_ms = YOUTUBE_SESSION_LOGIN_SUCCESS_WAIT_MS
        logged_in = False
        cls.mark_state_login_in_progress(
            "Запущена интерактивная авторизация YouTube-сессии."
        )
        _log_youtube_session_event(
            logging.INFO,
            "login_start",
            "Запущена интерактивная авторизация YouTube-сессии.",
            profile_ready=cls.profile_is_ready(),
        )
        try:
            cls._clear_profile_singleton_artifacts()
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(cls.profile_dir()),
                    headless=False,
                    no_viewport=True,
                    args=[
                        "--autoplay-policy=no-user-gesture-required",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--no-first-run",
                        "--start-maximized",
                    ],
                )
                try:
                    # Для dedicated profile интерактивный bootstrap всегда должен
                    # требовать живой re-login, а не принимать stale cookies за успех.
                    page = cls._prepare_interactive_login_page(context)
                    page.set_default_timeout(10_000)
                    page.set_default_navigation_timeout(20_000)
                    cls._bring_page_to_front(page)
                    cls._safe_goto(page, cls.login_url())
                    cls._bring_page_to_front(page)
                    cls._prefill_google_login(page)

                    deadline = time.monotonic() + (login_timeout_ms / 1000)
                    while time.monotonic() < deadline:
                        if cls.has_authenticated_session_cookies(context.cookies()):
                            logged_in = True
                            break
                        page.wait_for_timeout(poll_ms)

                    if logged_in:
                        cls._safe_goto(page, "https://www.youtube.com/")
                        page.wait_for_timeout(success_wait_ms)

                    context.cookies()
                finally:
                    with contextlib.suppress(PWError):
                        context.close()
        except Exception as exc:  # noqa: BLE001
            _log_youtube_session_event(
                logging.WARNING,
                "login_failed",
                "Не удалось выполнить интерактивную авторизацию YouTube-сессии.",
                error=str(exc),
            )
            cls.mark_state_auth_required(
                f"Ошибка интерактивной авторизации YouTube: {exc!s}"
            )
            return YouTubeSessionLoginResult(
                logged_in=False,
                profile_ready=cls.profile_is_ready(),
                waited_for_existing_refresh=waited_for_existing_refresh,
                message=str(exc),
            )
        finally:
            cls._release_lock(lock_fd)

        if logged_in:
            cls.mark_state_healthy(
                "YouTube-сессия авторизована и сохранена в persistent profile.",
                login_completed=True,
            )
            _log_youtube_session_event(
                NOTICE_LEVEL,
                "login_success",
                "YouTube-сессия успешно авторизована и сохранена в persistent profile.",
                profile_ready=cls.profile_is_ready(),
            )
        else:
            cls.mark_state_auth_required(
                "Требуется повторная авторизация YouTube-сессии."
            )
            _log_youtube_session_event(
                logging.WARNING,
                "login_timeout",
                "Не удалось подтвердить авторизацию YouTube-сессии в отведённое время.",
                profile_ready=cls.profile_is_ready(),
            )

        return YouTubeSessionLoginResult(
            logged_in=logged_in,
            profile_ready=logged_in,
            waited_for_existing_refresh=waited_for_existing_refresh,
            timed_out=not logged_in,
            message=(
                "YouTube-сессия авторизована и сохранена в persistent profile."
                if logged_in
                else "Не удалось подтвердить авторизацию в отведённое время."
            ),
        )

    @classmethod
    def refresh_profile(cls) -> YouTubeSessionRefreshResult:
        """Открывает persistent profile и обновляет сессию YouTube."""
        lock_fd = cls._acquire_lock()
        waited_for_existing_refresh = False
        if lock_fd is None:
            waited_for_existing_refresh = cls._wait_for_lock_release()
            return YouTubeSessionRefreshResult(
                refreshed=waited_for_existing_refresh,
                profile_ready=cls.profile_is_ready(),
                waited_for_existing_refresh=waited_for_existing_refresh,
                message=(
                    "Ожидание завершения уже запущенного обновления профиля."
                    if waited_for_existing_refresh
                    else "Не удалось дождаться освобождения блокировки профиля."
                ),
            )

        profile_ready = False
        navigated = False
        _log_youtube_session_event(
            logging.DEBUG,
            "refresh_start",
            "Запущено обновление persistent profile YouTube.",
        )
        try:
            cls._clear_profile_singleton_artifacts()
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(cls.profile_dir()),
                    headless=True,
                    args=[
                        "--autoplay-policy=no-user-gesture-required",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-background-timer-throttling",
                        "--disable-renderer-backgrounding",
                        "--mute-audio",
                    ],
                )
                try:
                    cls._install_network_blocker(context)
                    page = cls._resolve_page(context)
                    page.set_default_timeout(5_000)
                    page.set_default_navigation_timeout(8_000)

                    navigated = cls._safe_goto(page, YOUTUBE_SESSION_REFRESH_URL)
                    if navigated:
                        page.wait_for_timeout(YOUTUBE_SESSION_REFRESH_WAIT_MS)

                    # Запрос списка cookies провоцирует запись актуального состояния профиля.
                    profile_ready = cls.has_authenticated_session_cookies(
                        context.cookies()
                    )
                finally:
                    try:
                        context.close()
                    except PWError as exc:
                        _log_youtube_session_event(
                            logging.DEBUG,
                            "refresh_context_close_failed",
                            "Не удалось корректно закрыть persistent context YouTube.",
                            error=str(exc),
                        )
        except Exception as exc:  # noqa: BLE001
            _log_youtube_session_event(
                logging.WARNING,
                "refresh_failed",
                "Не удалось обновить YouTube-сессию.",
                error=str(exc),
            )
            return YouTubeSessionRefreshResult(
                refreshed=False,
                profile_ready=cls.profile_is_ready(),
                waited_for_existing_refresh=waited_for_existing_refresh,
                message=str(exc),
            )
        finally:
            cls._release_lock(lock_fd)

        result = YouTubeSessionRefreshResult(
            refreshed=navigated and profile_ready,
            profile_ready=profile_ready,
            waited_for_existing_refresh=waited_for_existing_refresh,
            message=(
                "Профиль YouTube обновлён."
                if navigated and profile_ready
                else "Профиль YouTube не удалось обновить через браузер."
            ),
        )
        if result.profile_ready:
            cls.mark_state_healthy(
                result.message
                or "YouTube-сессия подтверждена после обновления persistent profile."
            )
        else:
            cls.mark_state_auth_required(
                result.message
                or f"Persistent profile пуст. Выполните интерактивную авторизацию по адресу {cls.ui_url()}."
            )
        _log_youtube_session_event(
            logging.DEBUG,
            "refresh_finish",
            "Обновление persistent profile YouTube завершено.",
            refreshed=result.refreshed,
            profile_ready=result.profile_ready,
            waited=result.waited_for_existing_refresh,
            details=result.message,
        )
        return result

    @classmethod
    def _resolve_page(cls, context: BrowserContext) -> Page:
        if context.pages:
            return context.pages[0]
        return context.new_page()

    @classmethod
    def _prepare_interactive_login_page(cls, context: BrowserContext) -> Page:
        pages = list(context.pages)
        if not pages:
            page = context.new_page()
            cls._bring_page_to_front(page)
            return page

        _log_youtube_session_event(
            logging.DEBUG,
            "login_prepare_pages",
            "Подготовка стартовой страницы интерактивной YouTube-авторизации.",
            page_urls=", ".join(cls._page_url(page) or "—" for page in pages),
            pages_total=len(pages),
        )

        candidate_page: Page | None = None
        for page in pages:
            page_url = cls._page_url(page).lower()
            if any(
                page_url.startswith(prefix)
                for prefix in _INTERACTIVE_LOGIN_INTERNAL_PAGE_PREFIXES
            ):
                with contextlib.suppress(PWError):
                    page.close()
                continue
            candidate_page = page
            break

        page = candidate_page or context.new_page()
        cls._bring_page_to_front(page)
        return page

    @classmethod
    def _page_url(cls, page: Page) -> str:
        with contextlib.suppress(PWError):
            return str(page.url or "").strip()
        return ""

    @classmethod
    def _bring_page_to_front(cls, page: Page) -> None:
        with contextlib.suppress(PWError):
            page.bring_to_front()

    @classmethod
    def has_authenticated_session_cookies(
        cls,
        cookies: list[dict[str, Any]],
    ) -> bool:
        for cookie in cookies:
            name = str(cookie.get("name") or "").strip()
            domain = str(cookie.get("domain") or "").lower()
            if name not in _AUTHENTICATED_COOKIE_NAMES:
                continue
            if any(hint in domain for hint in _AUTHENTICATED_COOKIE_DOMAIN_HINTS):
                return True
        return False

    @classmethod
    def mark_state_healthy(cls, message: str, *, login_completed: bool = False) -> None:
        state = cls._get_state()
        now = cls._now()
        state.status = state.Status.HEALTHY
        state.status_message = message
        state.last_checked_at = now
        state.last_authenticated_at = now

        update_fields = [
            "status",
            "status_message",
            "last_checked_at",
            "last_authenticated_at",
            "modified",
        ]
        if login_completed:
            state.last_login_finished_at = now
            update_fields.append("last_login_finished_at")
        state.save(update_fields=update_fields)

    @classmethod
    def mark_state_auth_required(cls, message: str) -> None:
        state = cls._get_state()
        now = cls._now()
        state.status = state.Status.AUTH_REQUIRED
        state.status_message = message
        state.last_checked_at = now
        state.last_error_at = now
        state.save(
            update_fields=[
                "status",
                "status_message",
                "last_checked_at",
                "last_error_at",
                "modified",
            ]
        )

    @classmethod
    def mark_state_unknown(cls, message: str) -> None:
        state = cls._get_state()
        now = cls._now()
        state.status = state.Status.UNKNOWN
        state.status_message = message
        state.last_checked_at = now
        state.last_error_at = now
        state.save(
            update_fields=[
                "status",
                "status_message",
                "last_checked_at",
                "last_error_at",
                "modified",
            ]
        )

    @classmethod
    def mark_state_login_in_progress(cls, message: str) -> None:
        state = cls._get_state()
        now = cls._now()
        state.status = state.Status.LOGIN_IN_PROGRESS
        state.status_message = message
        state.last_checked_at = now
        state.last_login_started_at = now
        state.save(
            update_fields=[
                "status",
                "status_message",
                "last_checked_at",
                "last_login_started_at",
                "modified",
            ]
        )

    @classmethod
    def _get_state(cls):
        from records.models import YouTubeSessionState

        return YouTubeSessionState.get_solo()

    @staticmethod
    def _now():
        from django.utils import timezone

        return timezone.now()

    @classmethod
    def _prefill_google_login(cls, page: Page) -> None:
        email = cls.login_email()
        password = cls.login_password()
        if not email and not password:
            return

        cls._prefill_email_step(page, email)
        cls._prefill_password_step(page, password)

    @classmethod
    def _prefill_email_step(cls, page: Page, email: str) -> None:
        if not email:
            return
        try:
            email_input = page.locator(_GOOGLE_LOGIN_EMAIL_SELECTOR).first
            email_input.wait_for(state="visible", timeout=5_000)
            email_input.fill(email)
            cls._click_google_step(page, "#identifierNext")
            with contextlib.suppress(PWTimeout, PWError):
                page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except PWTimeout:
            _log_youtube_session_event(
                logging.DEBUG,
                "login_autofill_email_missing",
                "Поле email не найдено для автоподстановки.",
            )
        except PWError as exc:
            _log_youtube_session_event(
                logging.DEBUG,
                "login_autofill_email_failed",
                "Не удалось автоматически заполнить email.",
                error=str(exc),
            )

    @classmethod
    def _prefill_password_step(cls, page: Page, password: str) -> None:
        if not password:
            return
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                password_input = page.locator(_GOOGLE_LOGIN_PASSWORD_SELECTOR).first
                password_input.wait_for(state="visible", timeout=2_000)
                password_input.fill(password)
                return
            except PWTimeout:
                page.wait_for_timeout(500)
            except PWError as exc:
                _log_youtube_session_event(
                    logging.DEBUG,
                    "login_autofill_password_failed",
                    "Не удалось автоматически заполнить пароль.",
                    error=str(exc),
                )
                return
        else:
            _log_youtube_session_event(
                logging.DEBUG,
                "login_autofill_password_missing",
                "Поле пароля не найдено для автоподстановки.",
            )

    @classmethod
    def _click_google_step(cls, page: Page, root_selector: str) -> None:
        selectors = (
            f"{root_selector} button",
            f"{root_selector} div[role='button']",
            root_selector,
        )
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=2_000)
                locator.click()
                return
            except (PWTimeout, PWError):
                continue

    @classmethod
    def _acquire_lock(cls) -> int | None:
        lock_path = cls.lock_file()
        cls._drop_stale_lock(lock_path)
        try:
            file_descriptor = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
            )
        except FileExistsError:
            return None

        os.write(file_descriptor, str(os.getpid()).encode("utf-8"))
        return file_descriptor

    @classmethod
    def _release_lock(cls, file_descriptor: int | None) -> None:
        if file_descriptor is None:
            return
        try:
            os.close(file_descriptor)
        finally:
            with contextlib.suppress(FileNotFoundError):
                cls.lock_file().unlink()

    @classmethod
    def _drop_stale_lock(cls, lock_path: Path) -> None:
        if not lock_path.exists():
            return
        lock_pid = cls._read_lock_pid(lock_path)
        if lock_pid is not None and not cls._pid_is_running(lock_pid):
            with contextlib.suppress(FileNotFoundError):
                lock_path.unlink()
            return
        age_sec = time.time() - lock_path.stat().st_mtime
        if age_sec < _REFRESH_LOCK_STALE_SEC:
            return
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return

    @classmethod
    def _wait_for_lock_release(cls) -> bool:
        deadline = time.monotonic() + (YOUTUBE_SESSION_LOCK_WAIT_MS / 1000)
        lock_path = cls.lock_file()
        while time.monotonic() < deadline:
            cls._drop_stale_lock(lock_path)
            if not lock_path.exists():
                return True
            time.sleep(0.5)
        return False

    @classmethod
    def _read_lock_pid(cls, lock_path: Path) -> int | None:
        try:
            raw_value = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw_value:
            return None
        try:
            return int(raw_value)
        except ValueError:
            return None

    @classmethod
    def _pid_is_running(cls, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _clear_profile_singleton_artifacts(cls) -> None:
        profile_dir = cls.profile_dir()
        for artifact_name in (
            "SingletonCookie",
            "SingletonLock",
            "SingletonSocket",
            "DevToolsActivePort",
        ):
            artifact_path = profile_dir / artifact_name
            if artifact_path.is_dir():
                with contextlib.suppress(OSError):
                    artifact_path.rmdir()
            else:
                with contextlib.suppress(FileNotFoundError):
                    artifact_path.unlink()

    @classmethod
    def _install_network_blocker(cls, context: BrowserContext) -> None:
        def _should_abort(request) -> bool:
            resource_type = (request.resource_type or "").lower()
            if resource_type in {"font", "image", "media", "stylesheet"}:
                return True

            url = (request.url or "").lower()
            if any(domain in url for domain in _ALLOW_DOMAIN_HINTS):
                return False
            return any(domain in url for domain in _THIRDPARTY_BLOCKLIST)

        def _route(route, request) -> None:
            try:
                if _should_abort(request):
                    route.abort()
                    return
            except Exception as exc:  # noqa: BLE001
                _log_youtube_session_event(
                    logging.DEBUG,
                    "network_filter_failed",
                    "Ошибка сетевого фильтра YouTube-сессии.",
                    error=str(exc),
                )
            route.continue_()

        context.route("**/*", _route)

    @classmethod
    def _safe_goto(cls, page: Page, url: str) -> bool:
        try:
            page.goto(url, wait_until="domcontentloaded")
            return True
        except PWTimeout:
            _log_youtube_session_event(
                logging.DEBUG,
                "refresh_navigation_timeout",
                "Навигация к странице обновления YouTube-сессии превысила таймаут.",
                url=url,
            )
            return False
        except PWError as exc:
            _log_youtube_session_event(
                logging.DEBUG,
                "refresh_navigation_failed",
                "Ошибка навигации к странице обновления YouTube-сессии.",
                url=url,
                error=str(exc),
            )
            return False
