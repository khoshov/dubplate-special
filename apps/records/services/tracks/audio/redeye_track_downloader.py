# from __future__ import annotations
#
# import logging
# import os
# import random
# import tempfile
# from typing import Dict, Optional
#
# import requests
# from django.core.files import File
# from django.utils.text import slugify
# from requests.adapters import HTTPAdapter, Retry
#
# from apps.records.models import  Track
#
# logger = logging.getLogger(__name__)
#
# # ─────────────────────────────── сетевой стек  ───────────────────────────────
#
# UA_POOL = [
#     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
#     "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
#     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
# ]
#
# def make_session() -> requests.Session:
#     s = requests.Session()
#     retries = Retry(
#         total=3,
#         backoff_factor=0.6,
#         status_forcelist=[429, 500, 502, 503, 504],
#         allowed_methods=frozenset(["GET"]),
#         raise_on_status=False,
#     )
#     s.mount("https://", HTTPAdapter(max_retries=retries))
#     s.mount("http://", HTTPAdapter(max_retries=retries))
#     s.headers.update({
#         "User-Agent": random.choice(UA_POOL),
#         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
#         "Accept-Language": "ru,en;q=0.9",
#         "Connection": "keep-alive",
#         "Upgrade-Insecure-Requests": "1",
#     })
#     return s
#
# SESSION = make_session()
#
# def http_get(
#     url: str,
#     params: Optional[Dict[str, str]] = None,
#     *,
#     timeout: int = 20,
#     referer: Optional[str] = None,
#     stream: bool = False,
# ) -> requests.Response:
#     headers: Dict[str, str] = {}
#     if referer:
#         headers["Referer"] = referer
#     # иногда переставляем UA
#     if random.random() < 0.25:
#         headers["User-Agent"] = random.choice(UA_POOL)
#     resp = SESSION.get(url, params=params, headers=headers, timeout=timeout, stream=stream)
#     # На 403 в твоём коде не бросали, но у нас нет fallback — поэтому поднимем ошибку,
#     # иначе запишем в файл HTML/403.
#     resp.raise_for_status()
#     return resp
#
# # ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
#
# CHUNK = 64 * 1024
# DEFAULT_TIMEOUT = 20
# DEFAULT_MAX_BYTES = 15 * 1024 * 1024  # 15 MB
# ALLOWED_CT = {"audio/mpeg", "audio/mp3", "audio/mpeg3", "audio/x-mpeg-3", "audio/aac", "audio/aacp"}
#
#
# def _guess_basename(track: Track, url: str) -> str:
#     base = slugify(track.title or "preview") or "preview"
#     ext = os.path.splitext(url.split("?", 1)[0])[1].lower()
#     if ext not in [".mp3", ".mpeg", ".mp3a", ".aac"]:
#         ext = ".mp3"
#     return f"{base}{ext}"
#
#
# def download_to_filefield(
#     track: Track,
#     url: str,
#     *,
#     timeout: int = DEFAULT_TIMEOUT,
#     max_bytes: int = DEFAULT_MAX_BYTES,
#     overwrite: bool = False,
#     referer: Optional[str] = None,
# ) -> Optional[str]:
#     """
#     Скачивает URL потоково и сохраняет в Track.audio_preview (FileField).
#
#     Args:
#         track: Track-модель, в которую пишем файл.
#         url: прямой URL на аудио (mp3/aac/…).
#         timeout: таймаут сети (сек).
#         max_bytes: предел размера (байт).
#         overwrite: перезаписывать ли существующий превью-файл.
#         referer: опциональный Referer заголовок (напр. страница карточки).
#
#     Returns:
#         FieldFile.name (относительный путь в storage) или None при неуспехе.
#     """
#     if not url:
#         return None
#     if track.audio_preview and track.audio_preview.name and not overwrite:
#         logger.info("download_to_filefield: already has preview (skip) track=%s", track.pk)
#         return track.audio_preview.name
#
#     try:
#         resp = http_get(url, timeout=timeout, referer=referer, stream=True)
#         ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
#         if ct and ct not in ALLOWED_CT and not url.lower().endswith((".mp3", ".aac")):
#             logger.warning("download_to_filefield: unexpected content-type %s for %s", ct, url)
#
#         total = 0
#         with tempfile.NamedTemporaryFile("wb", delete=False) as tmp:
#             tmp_path = tmp.name
#             for chunk in resp.iter_content(CHUNK):
#                 if not chunk:
#                     continue
#                 total += len(chunk)
#                 if total > max_bytes:
#                     raise ValueError(f"file too large (> {max_bytes} bytes)")
#                 tmp.write(chunk)
#     except Exception as e:
#         logger.warning("download_to_filefield: failed %s: %s", url, e)
#         return None
#
#     base = _guess_basename(track, url)
#     try:
#         with open(tmp_path, "rb") as fh:
#             track.audio_preview.save(base, File(fh), save=True)
#         logger.info("download_to_filefield: saved %s -> %s", url, track.audio_preview.name)
#         return track.audio_preview.name
#     except Exception as e:
#         logger.error("download_to_filefield: save failed for %s: %s", url, e, exc_info=True)
#         return None
#     finally:
#         try:
#             os.unlink(tmp_path)
#         except Exception:
#             pass
#
#
# if __name__ == "__main__":
#     # Ручной тест без Django-моделей: скачиваем по прямому URL в локальный файл.
#     test_url = input("Вставьте прямой URL аудио (mp3/aac): ").strip()
#     if not test_url:
#         raise SystemExit("URL не указан.")
#     dest = input("Имя файла для сохранения (по умолчанию track.mp3): ").strip() or "track.mp3"
#     try:
#         r = http_get(test_url, timeout=30, stream=True)
#         total = 0
#         with open(dest, "wb") as f:
#             for chunk in r.iter_content(CHUNK):
#                 if not chunk:
#                     continue
#                 total += len(chunk)
#                 f.write(chunk)
#         print(f"OK: сохранено {total} байт в {dest}")
#     except Exception as e:
#         print(f"Ошибка загрузки: {e}")
#         raise SystemExit(4)
