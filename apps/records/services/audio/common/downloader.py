from __future__ import annotations

import logging
import os
import random
import tempfile
from typing import Dict, Optional, Protocol
from urllib.parse import urlsplit

import requests
from django.core.files import File
from django.utils.text import slugify
from requests.adapters import HTTPAdapter, Retry

from records.constants import (
    REDEYE_USER_AGENTS,
    AUDIO_STREAM_CHUNK_SIZE,
    AUDIO_DEFAULT_TIMEOUT,
    AUDIO_DEFAULT_MAX_BYTES,
    ALLOWED_AUDIO_CONTENT_TYPES,
    RETRY_TOTAL,
    RETRY_BACKOFF_FACTOR,
    RETRY_STATUS_FORCELIST,
    RETRY_ALLOWED_METHODS,
    HTTP_ACCEPT,
    HTTP_ACCEPT_LANGUAGE,
)

logger = logging.getLogger(__name__)


class FileFieldLike(Protocol):
    """Протокол описывает поведение FileField, необходимое для сохранения файла."""

    def save(self, name: str, file: File, save: bool = ...) -> None: ...


class TrackLike(Protocol):
    """Протокол описывает минимальный контракт трека для сохранения аудио-файла.

    Атрибуты:
        audio_preview: Поле файла, совместимое с `FileFieldLike`.
        title: Название трека (может отсутствовать).
        pk: Идентификатор (любого типа) — используется в логах.
    """

    audio_preview: FileFieldLike
    title: Optional[str]
    pk: object


def create_http_session() -> requests.Session:
    """Метод создаёт HTTP-сессию с повторными попытками и базовыми заголовками.

    Поведение:
        - Выполняет до `RETRY_TOTAL` повторных попыток для кодов из `RETRY_STATUS_FORCELIST`.
        - Использует экспоненциальные паузы (`RETRY_BACKOFF_FACTOR`).
        - Устанавливает безопасные заголовки (Accept/Accept-Language/User-Agent).

    Returns:
        requests.Session: Настроенная сессия.
    """
    session = requests.Session()
    retries = Retry(
        total=RETRY_TOTAL,
        backoff_factor=RETRY_BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUS_FORCELIST,
        allowed_methods=frozenset(RETRY_ALLOWED_METHODS),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update(
        {
            "User-Agent": random.choice(REDEYE_USER_AGENTS),
            "Accept": HTTP_ACCEPT,
            "Accept-Language": HTTP_ACCEPT_LANGUAGE,
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    return session


SESSION: requests.Session = create_http_session()


def http_get(
    url: str,
    params: Optional[Dict[str, str]] = None,
    *,
    timeout: int = AUDIO_DEFAULT_TIMEOUT,
    referer: Optional[str] = None,
    stream: bool = False,
    allow_http: bool = True,  # --- изменено: теперь по умолчанию True для совместимости с источниками, отдающими http ---
) -> requests.Response:
    """Метод выполняет GET-запрос с безопасными заголовками и (опционально) потоковой выдачей.

    Метод реализует:
        - проверку схемы URL; https — предпочтителен; http допускается (по умолчанию), но будет предупреждение,
          можно жёстко запретить через allow_http=False;
        - установку заголовка Referer (если передан);
        - мягкую ротацию User-Agent для снижения «липкости» отпечатка;
        - вызов `raise_for_status()` для отсечения 4xx/5xx.

    Args:
        url: Запрашиваемый URL.
        params: Query-параметры.
        timeout: Таймаут запроса (сек).
        referer: Значение заголовка Referer.
        stream: Признак потоковой передачи тела ответа.
        allow_http: Разрешать явные http-ссылки (по умолчанию True для совместимости с провайдерами превью).

    Returns:
        requests.Response: Ответ с уже вызванным `raise_for_status()`.

    Raises:
        requests.RequestException: При сетевых/HTTP-ошибках.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme == "http" and not allow_http:
        # Раньше выбрасывали ValueError; теперь не рвём поток по умолчанию, а даём возможность жёстко запретить.
        logger.warning("HTTP-ссылка (не https): %s — запрещено (allow_http=False).", url)
        # Эмулируем «жёсткий» запрет явным исключением только если так просили:
        raise ValueError(f"Небезопасная схема URL (http) для {url} при allow_http=False.")

    headers: Dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    if random.random() < 0.25:
        headers["User-Agent"] = random.choice(REDEYE_USER_AGENTS)

    if scheme == "http":
        logger.warning("Используется http-ссылка (не https): %s — продолжаем по совместимости.", url)

    logger.debug("HTTP GET %s params=%s stream=%s", url, params, stream)
    response = SESSION.get(url, params=params, headers=headers, timeout=timeout, stream=stream)
    response.raise_for_status()
    return response



def _guess_extension_from_url_or_ct(url: str, content_type: str) -> str:
    """Метод определяет расширение файла по URL или Content-Type.

    Args:
        url: Источник файла.
        content_type: Заголовок Content-Type (может быть пустым).

    Returns:
        str: Расширение файла с точкой (например, ".mp3").
    """
    pure = url.split("?", 1)[0]
    ext = os.path.splitext(pure)[1].lower()
    if not ext or ext not in {".mp3", ".aac", ".mpeg"}:
        if content_type in {"audio/aac", "audio/aacp"}:
            return ".aac"
        return ".mp3"
    return ext


def make_audio_filename(track_title: Optional[str], url: str, content_type: str) -> str:
    """Метод формирует безопасное имя файла на основе названия трека и типа контента.

    Args:
        track_title: Название трека (используется как основа имени).
        url: Источник файла (используется для определения расширения).
        content_type: Заголовок Content-Type ответа (уточняет расширение).

    Returns:
        str: Имя файла с расширением.
    """
    base = slugify(track_title or "audio") or "audio"
    ext = _guess_extension_from_url_or_ct(url, content_type)
    return f"{base}{ext}"


def _validate_content_type(url: str, content_type: str) -> None:
    """Пишет предупреждение, если тип контента не из разрешённых и URL без ожидаемого расширения."""
    if content_type and (content_type not in ALLOWED_AUDIO_CONTENT_TYPES) and not url.lower().endswith((".mp3", ".aac")):
        logger.warning(
            "Неожиданный Content-Type '%s' для URL %s — продолжаем осторожно.",
            content_type,
            url,
        )


def _content_length_ok(response: requests.Response, *, max_bytes: int) -> bool:
    """Возвращает True, если Content-Length отсутствует или не превышает лимит; иначе False."""
    try:
        header = response.headers.get("Content-Length", "0")
        value = int(header) if header else 0
        if value and value > max_bytes:
            logger.warning(
                "Размер файла %s байт превышает лимит %s байт — скачивание отменено.",
                value,
                max_bytes,
            )
            return False
    except (ValueError, TypeError):
        # Некорректный заголовок — проверим ограничение во время записи потока.
        pass
    return True


def _write_stream_to_temp(response: requests.Response, *, max_bytes: int) -> Optional[str]:
    """Пишет поток ответа в временный файл с ограничением размера. Возвращает путь или None при превышении лимита."""
    written = 0
    with tempfile.NamedTemporaryFile("wb", delete=False) as tmp_file:
        tmp_path = tmp_file.name
        for chunk in response.iter_content(AUDIO_STREAM_CHUNK_SIZE):
            if not chunk:
                continue
            written += len(chunk)
            if written > max_bytes:
                logger.warning(
                    "Лимит размера %s байт превышен (получено %s байт) — удаляем временный файл.",
                    max_bytes,
                    written,
                )
                tmp_file.close()
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return None
            tmp_file.write(chunk)
    return tmp_path


def _save_temp_to_filefield(track: TrackLike, url: str, tmp_path: str, content_type: str) -> str:
    """Сохраняет временный файл в track.audio_preview и возвращает FieldFile.name."""
    filename = make_audio_filename(getattr(track, "title", None), url, content_type)
    with open(tmp_path, "rb") as fh:
        track.audio_preview.save(filename, File(fh), save=True)
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
    return getattr(track.audio_preview, "name", "")


def download_audio_to_track(
    track: TrackLike,
    url: str,
    *,
    timeout: int = AUDIO_DEFAULT_TIMEOUT,
    max_bytes: int = AUDIO_DEFAULT_MAX_BYTES,
    overwrite: bool = False,
    referer: Optional[str] = None,
    allow_http: bool = False,
) -> Optional[str]:
    """Метод скачивает аудио по URL и сохраняет его в `track.audio_preview`.

    Метод реализует:
        - проверку входных данных (пустой URL — ранний выход);
        - пропуск скачивания при `overwrite=False` и уже существующем файле;
        - строгую проверку схемы (https по умолчанию, http — только через `allow_http=True`);
        - предварительную проверку заголовка Content-Length (если есть) на превышение `max_bytes`;
        - потоковую загрузку с ограничением размера;
        - формирование безопасного имени файла (`make_audio_filename`);
        - сохранение через FileField и возврат `FieldFile.name`.

    Args:
        track: Объект трека, совместимый с `TrackLike`.
        url: Прямой URL на аудио-файл (mp3/aac/...).
        timeout: Таймаут HTTP-запроса (сек).
        max_bytes: Лимит размера файла (байт).
        overwrite: Признак перезаписи существующего файла.
        referer: Заголовок Referer (например, URL карточки товара).
        allow_http: Разрешает использовать http-ссылки (по умолчанию — False).

    Returns:
        Optional[str]: Относительное имя сохранённого файла (`FieldFile.name`) либо `None` при неуспехе.

    Raises:
        ValueError: При явном запрете http-ссылок и попытке скачать по http.
    """
    if not url:
        logger.debug("Скачивание пропущено: пустой URL.")
        return None

    existing_name = getattr(getattr(track, "audio_preview", None), "name", "")
    if existing_name and not overwrite:
        logger.info(
            "Скачивание пропущено: у трека уже есть аудио (track=%s, file=%s).",
            getattr(track, "pk", None),
            existing_name,
        )
        return existing_name

    tmp_path: Optional[str] = None
    try:
        response = http_get(url, timeout=timeout, referer=referer, stream=True, allow_http=allow_http)
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()

        _validate_content_type(url, content_type)
        if not _content_length_ok(response, max_bytes=max_bytes):
            return None

        tmp_path = _write_stream_to_temp(response, max_bytes=max_bytes)
        if not tmp_path:
            return None

        saved_name = _save_temp_to_filefield(track, url, tmp_path, content_type)
        logger.info(
            "Аудио сохранено: track=%s, file=%s (источник: %s).",
            getattr(track, "pk", None),
            saved_name,
            url,
        )
        return saved_name

    except requests.HTTPError as http_error:
        status = getattr(getattr(http_error, "response", None), "status_code", "?")
        logger.warning("HTTP-ошибка при скачивании %s (status=%s): %s.", url, status, http_error)
        return None
    except (requests.RequestException, OSError, ValueError) as error:
        logger.error("Ошибка скачивания/сохранения %s: %s.", url, error, exc_info=True)
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


if __name__ == "__main__":
    test_url = input("Вставьте прямой URL аудио (желательно https, mp3/aac): ").strip()
    if not test_url:
        raise SystemExit("URL не указан.")

    scheme = urlsplit(test_url).scheme.lower()
    allow_http_flag = False
    if scheme == "http":
        confirm = input("Обнаружен http. Продолжить? [y/N]: ").strip().lower()
        allow_http_flag = confirm == "y"

    destination = (input("Имя файла для сохранения (по умолчанию track.mp3): ").strip() or "track.mp3")

    try:
        resp = http_get(test_url, timeout=30, stream=True, allow_http=allow_http_flag)
        saved_bytes = 0
        with open(destination, "wb") as out:
            for test_chunk in resp.iter_content(AUDIO_STREAM_CHUNK_SIZE):
                if not test_chunk:
                    continue
                saved_bytes += len(test_chunk)
                out.write(test_chunk)
        print(f"OK: сохранено {saved_bytes} байт в {destination}")
    except (requests.RequestException, OSError, ValueError) as error:
        print(f"Ошибка загрузки: {error}")
        raise SystemExit(4)
