"""Константы для интеграции с Discogs API."""


class DiscogsConstants:
    """Константы для работы с Discogs."""

    # Таймауты и задержки
    RATE_LIMIT_WAIT_TIME = 60  # секунд - время ожидания при превышении лимита запросов
    IMAGE_DOWNLOAD_TIMEOUT = 20  # секунд - таймаут для загрузки изображений

    # API настройки
    DEFAULT_USER_AGENT = "VinylCollector/1.0"

    # Типы поиска
    SEARCH_TYPE_BARCODE = "barcode"
    SEARCH_TYPE_CATALOG = "catno"

    # Типы идентификаторов
    IDENTIFIER_BARCODE = "barcode"
    IDENTIFIER_CATALOG = "catalog_number"

    # Форматы файлов
    IMAGE_FORMAT = "jpeg"
    IMAGE_FILENAME_TEMPLATE = "cover_{discogs_id}.{format}"

    # HTTP статус коды
    HTTP_UNAUTHORIZED = 401
    HTTP_RATE_LIMIT_EXCEEDED = 429
