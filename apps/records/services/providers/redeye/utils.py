from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

REDEYE_HOST = "www.redeyerecords.co.uk"


def normalize_redeye_url(url: str) -> str:
    """
    Нормализует URL карточки Redeye:
      - чинит кейс с продублированным доменом в path (…/www.redeyerecords.co.uk/…)
      - убирает лишние слэши
      - приводит схему/хост к https://www.redeyerecords.co.uk

    Примеры:
      https://www.redeyerecords.co.uk/www.redeyerecords.co.uk/vinyl/187779-  ->
      https://www.redeyerecords.co.uk/vinyl/187779-
    """
    if not url:
        return url

    scheme, netloc, path, query, fragment = urlsplit(url)

    # 1) схема/хост
    scheme = "https"
    netloc = REDEYE_HOST

    # 2) убрать ведущее "www.redeyerecords.co.uk/" из начала path (если затесалось)
    bad_prefix = f"{REDEYE_HOST}/"
    if path.lstrip("/").startswith(bad_prefix):
        # lstrip('/') чтобы корректно срезать даже если было несколько слэшей
        path = "/" + path.lstrip("/")[len(bad_prefix):]

    # 3) сжать множественные слэши в path
    while "//" in path:
        path = path.replace("//", "/")

    return urlunsplit((scheme, netloc, path, query, fragment))
