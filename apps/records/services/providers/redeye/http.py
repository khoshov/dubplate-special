from __future__ import annotations

import html
import logging
import random
import time
from typing import Optional

import requests

from records.constants import (
    REDEYE_HTTP_TIMEOUT,
    REDEYE_HTTP_DELAY_SEC,
    REDEYE_HTTP_JITTER_SEC,
    REDEYE_HTTP_MAX_RETRIES,
    REDEYE_HTTP_COOLDOWN_SEC,
    REDEYE_HTTP_STOP_ON_BLOCK,
    REDEYE_BASE_HEADERS,
    REDEYE_USER_AGENTS,
)

logger = logging.getLogger(__name__)


class RedeyeHTTPClient:
    """
    Класс инкапсулирует сетевую политику для Redeye
    (задержки, повторные запросы, заголовки).

    Методы:
        get_text(url, referer=None, slow=False) -> str
            Возвращает HTML как text (html.unescape).
    """

    def __init__(
        self,
        *,
        timeout: int = REDEYE_HTTP_TIMEOUT,
        delay_sec: float = REDEYE_HTTP_DELAY_SEC,
        jitter_sec: float = REDEYE_HTTP_JITTER_SEC,
        max_retries: int = REDEYE_HTTP_MAX_RETRIES,
        cooldown_sec: int = REDEYE_HTTP_COOLDOWN_SEC,
        stop_on_block: bool = REDEYE_HTTP_STOP_ON_BLOCK,
    ) -> None:
        self.session = requests.Session()
        self.timeout = int(timeout)
        self.delay_sec = float(delay_sec)
        self.jitter_sec = float(jitter_sec)
        self.max_retries = int(max_retries)
        self.cooldown_sec = int(cooldown_sec)
        self.stop_on_block = bool(stop_on_block)

    def _polite_sleep(self) -> None:
        time.sleep(self.delay_sec + random.uniform(0.0, self.jitter_sec))

    def get_text(self, url: str, *, referer: Optional[str] = None, slow: bool = False) -> str:
        """
        Метод выполняет GET и возвращает html.unescape(resp.text), применяя задержки и ретраи.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                if slow:
                    self._polite_sleep()

                headers = dict(REDEYE_BASE_HEADERS)
                headers["User-Agent"] = random.choice(REDEYE_USER_AGENTS)
                if referer:
                    headers["Referer"] = referer

                resp = self.session.get(url, headers=headers, timeout=self.timeout)
                status = resp.status_code

                if status == 200:
                    return html.unescape(resp.text)

                if 500 <= status < 600:
                    backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.8)
                    logger.warning(
                        "[redeye:http] %s for %s (attempt %s/%s) backoff=%.1fs",
                        status, url, attempt, self.max_retries, backoff
                    )
                    time.sleep(backoff)
                    continue

                if status in (403, 429):
                    logger.warning(
                        "[redeye:http] possible block %s for %s → cooldown %ss (attempt %s/%s)",
                        status, url, self.cooldown_sec, attempt, self.max_retries
                    )
                    time.sleep(self.cooldown_sec)
                    if attempt == self.max_retries:
                        if self.stop_on_block:
                            raise requests.HTTPError(f"blocked: {status} {url}")
                        logger.warning("[redeye:http] skip blocked page: %s", url)
                        break
                    continue

                resp.raise_for_status()

            except requests.RequestException as e:
                last_exc = e
                backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.8)
                logger.warning(
                    "[redeye:http] request error for %s: %s (attempt %s/%s) backoff=%.1fs",
                    url, e, attempt, self.max_retries, backoff
                )
                time.sleep(backoff)

        if last_exc:
            raise last_exc
        return ""  # неустранимый блок
