from typing import Tuple

# для FORMS SETTINGS
SOURCE_DISCOGS = "discogs"
SOURCE_REDEYE = "redeye"
SOURCE_CHOICES: Tuple[Tuple[str, str], ...] = (
    (SOURCE_DISCOGS, "Discogs"),
    (SOURCE_REDEYE, "Redeye Records"),
)


# REDEYE SETTINGS
REDEYE_HOST: str = "www.redeyerecords.co.uk"
REDEYE_URLS = [
    {
        "code": "bass-preorders",
        "url": "https://www.redeyerecords.co.uk/bass-music/pre-orders",
        "style": "Bass Music",
        "genre": "Electronic",
    },
    {
        "code": "dnb-preorders",
        "url": "https://www.redeyerecords.co.uk/drum-and-bass/pre-orders",
        "style": "Drum n Bass",
        "genre": "Electronic",
    },
]

# DOWNLOADER SETTINGS
REDEYE_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]


AUDIO_STREAM_CHUNK_SIZE = 64 * 1024  # 64 KiB
AUDIO_DEFAULT_TIMEOUT = 20  # сек
AUDIO_DEFAULT_MAX_BYTES = 15 * 1024 * 1024  # 15 MiB

ALLOWED_AUDIO_CONTENT_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/mpeg3",
    "audio/x-mpeg-3",
    "audio/aac",
    "audio/aacp",
}

# --- HTTP/повторные попытки ---
RETRY_TOTAL = 3
RETRY_BACKOFF_FACTOR = 0.6
RETRY_STATUS_FORCELIST = [429, 500, 502, 503, 504]
RETRY_ALLOWED_METHODS = {"GET"}

# --- базовые заголовки ---
HTTP_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
HTTP_ACCEPT_LANGUAGE = "ru,en;q=0.9"


# Настройки для модуля захвата mp3-превью со страницы Redeye

# Redeye / захват аудио (Playwright)
REDEYE_PLAYER_BUTTON_SELECTOR = ".play a.btn-play[data-sample]"
REDEYE_COOKIE_SELECTORS = (
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
CAPTURE_FALLBACK_HTTP_TIMEOUT = 5.0  # сек, HEAD-проверка недостающих mp3
CAPTURE_WAIT_TICK_MS = 120.0  # мс, «тик» ожидания после клика
CAPTURE_GLOBAL_TIMEOUT_SEC = 25.0  # сек, общий мягкий предел работы
CAPTURE_POST_CLICK_SETTLE_SEC = 2.0  # сек, «доохота» после серии кликов

# Хост CDN аудио Redeye (для fallback-конструкции ссылок)
REDEYE_SOUNDS_BASE_URL = "https://sounds.redeyerecords.co.uk/"


# Redeye / player
REDEYE_PLAYER_DEFAULT_CLICK_TIMEOUT_SEC = 20
REDEYE_PLAYER_PRUNE_UNTITLED = True  # удалять плейсхолдеры "Untitled..." без аудио


# Redeye: базовый URL и сетевые параметры
REDEYE_BASE_URL = "https://www.redeyerecords.co.uk"
REDEYE_HTTP_TIMEOUT = 20
REDEYE_HTTP_DELAY_SEC = 0.6
REDEYE_HTTP_JITTER_SEC = 0.5
REDEYE_HTTP_MAX_RETRIES = 4
REDEYE_HTTP_COOLDOWN_SEC = 90
REDEYE_HTTP_STOP_ON_BLOCK = False

# Базовые заголовки для Redeye (поисковая/карточка)

REDEYE_BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}


# Карты месяцев (fallback, если нет babel)
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
