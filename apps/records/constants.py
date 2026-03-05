"""
Глобальные константы приложения records.

Блоки:
  • Источники данных (Discogs, Redeye)
  • HTTP/сеть и заголовки
  • Аудио-настройки (загрузка файлов)
  • Настройки Playwright для захвата аудио с Redeye
  • Справочники (месяцы и пр.)
"""

from typing import Tuple


# ============================================================================
#  ИСТОЧНИКИ ДАННЫХ (для форм/моделей)
# ============================================================================

SOURCE_DISCOGS: str = "discogs"
SOURCE_REDEYE: str = "redeye"

SOURCE_CHOICES: Tuple[Tuple[str, str], ...] = (
    (SOURCE_REDEYE, "Redeye Records"),
    (SOURCE_DISCOGS, "Discogs"),
)


# ============================================================================
#  НАСТРОЙКИ REDEYE (основные URL и категории)
# ============================================================================

REDEYE_HOST: str = "www.redeyerecords.co.uk"

REDEYE_URLS = [
    {
        "code": "bass-preorders",
        "url": "https://www.redeyerecords.co.uk/bass-music/new-releases",
        "style": "Not specified",
        "genre": "Bass Music",
    },
    {
        "code": "dnb-preorders",
        "url": "https://www.redeyerecords.co.uk/drum-and-bass/new-releases",
        "style": "Not specified",
        "genre": "Drum and Bass",
    },
]


# ============================================================================
#  HTTP / СЕТЕВЫЕ ПАРАМЕТРЫ
# ============================================================================

REDEYE_BASE_URL: str = "https://www.redeyerecords.co.uk"

REDEYE_HTTP_TIMEOUT: int = 20  # сек
REDEYE_HTTP_DELAY_SEC: float = 0.6
REDEYE_HTTP_JITTER_SEC: float = 0.5
REDEYE_HTTP_MAX_RETRIES: int = 4
REDEYE_HTTP_COOLDOWN_SEC: int = 90
REDEYE_HTTP_STOP_ON_BLOCK: bool = False

# Базовые заголовки для запросов к Redeye (поиск/карточка)
REDEYE_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}

# Полезные заголовки/параметры HTTP
HTTP_ACCEPT: str = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8"
)
HTTP_ACCEPT_LANGUAGE: str = "ru,en;q=0.9"

REDEYE_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]

# Повторные попытки HTTP
RETRY_TOTAL: int = 3
RETRY_BACKOFF_FACTOR: float = 0.6
RETRY_STATUS_FORCELIST = [429, 500, 502, 503, 504]
RETRY_ALLOWED_METHODS = {"GET"}


# ============================================================================
#  АУДИО-ПАРАМЕТРЫ (HTTP-загрузка файлов)
# ============================================================================

AUDIO_STREAM_CHUNK_SIZE: int = 64 * 1024
"""Размер блока при потоковом скачивании аудио (байт)."""

AUDIO_DEFAULT_TIMEOUT: int = 30
"""Таймаут HTTP-загрузки аудио-файла (сек)."""

AUDIO_DEFAULT_MAX_BYTES: int = 15 * 1024 * 1024
"""Максимально допустимый размер аудио-файла (байт)."""

ALLOWED_AUDIO_CONTENT_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/mpeg3",
    "audio/x-mpeg-3",
    "audio/aac",
    "audio/aacp",
}


# ============================================================================
#  НАСТРОЙКИ PLAYWRIGHT / ЗАХВАТА АУДИО С REDEYE
# ============================================================================

REDEYE_PLAYER_BUTTON_SELECTOR: str = ".play a.btn-play[data-sample]"
"""CSS-селектор кнопок плеера Redeye."""

REDEYE_COOKIE_SELECTORS: Tuple[str, ...] = (
    'button:has-text("Accept")',
    'button:has-text("I Agree")',
    "text=Accept all",
    'button:has-text("OK")',
    'button[aria-label="Accept"]',
)

# Эвристики распознавания аудио
MEDIA_ACCEPTABLE_HTTP_STATUSES = {200, 206}
MEDIA_CONTENT_TYPE_HINTS = ("audio/", "mpegurl", "application/vnd.apple.mpegurl")
MEDIA_URL_HINTS = (".mp3", ".aac", ".m3u8", "/stream/")

# Тайминги захвата
CAPTURE_FALLBACK_HTTP_TIMEOUT: float = 5.0
"""Таймаут HEAD-проверки недостающих mp3 (сек)."""

CAPTURE_WAIT_TICK_MS: float = 120.0
"""Интервал «тика» ожидания после клика по кнопке плеера (мс)."""

CAPTURE_GLOBAL_TIMEOUT_SEC: float = 25.0
"""Общий мягкий предел времени работы скрапера (сек)."""

CAPTURE_POST_CLICK_SETTLE_SEC: float = 2.0
"""Дополнительное ожидание («доохота») после серии кликов (сек)."""

# Хост CDN аудио Redeye (для fallback-конструкции ссылок)
REDEYE_SOUNDS_BASE_URL: str = "https://sounds.redeyerecords.co.uk/"

# Параметры плеера/кликов
REDEYE_PLAYER_DEFAULT_CLICK_TIMEOUT_SEC: int = 20
"""Стандартный таймаут ожидания появления URL после клика по кнопке плеера Redeye (сек)."""

REDEYE_PLAYER_PRUNE_UNTITLED: bool = True
"""Удалять ли плейсхолдеры 'Untitled…' без аудио перед привязкой аудио."""

PLAYWRIGHT_CLICK_ACTION_TIMEOUT_MS: int = 1_500
"""Таймаут самого действия клика по кнопке (мс), не путать с ожиданием сетевых ответов."""

PLAYWRIGHT_ACTION_TIMEOUT_MS: int = 3_000
"""Базовый таймаут (мс) для коротких действий Playwright: поиск локатора, клик и т.п."""

PLAYWRIGHT_NAVIGATION_TIMEOUT_MS: int = 12_000
"""Таймаут навигации (page.goto/page.waitForNavigation) в миллисекундах."""

PLAYWRIGHT_WAIT_TICK_MS: int = 100
"""Интервал ожидания (мс) между проверками количества ответов при догрузке аудио."""

# ============================================================================
#  ПРОЧЕЕ / СЛУЖЕБНЫЕ СПРАВОЧНИКИ
# ============================================================================

MONTHS_EN_TO_RU_GENITIVE = {
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

MONTHS_EN_TO_NUM = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
