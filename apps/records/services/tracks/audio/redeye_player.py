# from __future__ import annotations
#
# """
# Загрузка превью по плееру Redeye с сопоставлением «по порядку»:
#
# 1-й url → 1-й трек (position_index=1), 2-й url → 2-й трек, и т.д.
# Если url меньше, чем треков — заполняем первые K, остальные пропускаем.
# Если url больше — лишние игнорируем.
# """
#
# import logging
# from typing import List
#
# from ....models import Track, Record
# from .redeye_track_downloader import download_to_filefield
# from .capture import collect_redeye_media_urls
#
# logger = logging.getLogger(__name__)
#
#
# def ensure_previews_from_redeye_player(
#     record: Record,
#     *,
#     force: bool = False,
#     per_click_timeout_sec: int = 20,
# ) -> int:
#     page_url = record.vendor_source_url or ""
#     if not page_url:
#         logger.info("[redeye_player] record %s has no vendor_source_url", record.pk)
#         return 0
#
#     tracks: List[Track] = list(record.tracks.order_by("position_index", "id"))
#     if not tracks:
#         logger.info("[redeye_player] no tracks for record %s", record.pk)
#         return 0
#
#     urls = collect_redeye_media_urls(page_url, per_click_timeout_sec=per_click_timeout_sec)
#     if not urls:
#         logger.info("[redeye_player] no media urls captured for %s", page_url)
#         return 0
#
#     updated = 0
#     for track, url in zip(tracks, urls):
#         if not force and track.audio_preview and track.audio_preview.name:
#             continue
#         saved = download_to_filefield(track, url, overwrite=force, referer=page_url)
#         if saved:
#             updated += 1
#
#     logger.info("[redeye_player] record=%s previews updated: %d (urls=%d, tracks=%d)",
#                 record.pk, updated, len(urls), len(tracks))
#     return updated
