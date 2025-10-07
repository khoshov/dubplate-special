"""
Загрузка превью по плееру Redeye с сопоставлением «по порядку».

1-й url → 1-й трек (position_index=1), 2-й → 2-й и т.д.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from django.db.models import Q

from ....models import Record, Track, RecordSource
from .redeye_track_downloader import download_to_filefield
from .capture import collect_redeye_media_urls

logger = logging.getLogger(__name__)


def _resolve_product_page_url(record: Record, explicit_url: Optional[str] = None) -> Optional[str]:
    """
    Возвращает URL карточки товара Redeye для записи.

    Приоритет:
      1) explicit_url (если передан сверху),
      2) RecordSource(provider=REDEYE, role=PRODUCT_PAGE, can_fetch_audio=True),
      3) бэкап для старых инсталляций: record.source_url (если существует).
    """
    if explicit_url:
        logger.debug("[redeye_player] explicit product_page url: %s", explicit_url)
        return explicit_url

    try:
        src = (
            record.sources
            .filter(
                provider=RecordSource.Provider.REDEYE,
                role=RecordSource.Role.PRODUCT_PAGE,
                can_fetch_audio=True,
            )
            .values_list("url", flat=True)
            .first()
        )
        if src:
            logger.debug("[redeye_player] url from RecordSource: %s", src)
            return src
    except Exception as e:
        logger.debug("[redeye_player] failed to lookup RecordSource url: %s", e)

    # бэкап — атрибут мог существовать в старой схеме
    legacy_url = getattr(record, "source_url", None)
    if legacy_url:
        logger.debug("[redeye_player] legacy record.source_url: %s", legacy_url)
    return legacy_url


def ensure_previews_from_redeye_player(
        record: Record,
        *,
        page_url: Optional[str] = None,
        force: bool = False,
        per_click_timeout_sec: int = 20,
) -> int:
    """
    Ставит mp3-превью в Track.audio_preview.

    ВАЖНО: сопоставление выполняется строго по `position_index`:
      urls[0] → трек с минимальным position_index,
      urls[1] → следующий и т.д.
    Поле `position` (например 'A1', 'B1') НЕ участвует в упорядочивании.

    :param record: запись
    :param page_url: явная ссылка на карточку Redeye (если не задана, берём из RecordSource)
    :param force: если True — перезаписываем уже имеющиеся превью
    :param per_click_timeout_sec: таймаут между кликами при сборе ссылок
    :return: количество треков, у которых появилось/обновилось превью
    """
    logger.info("[redeye_player] вызов модуля аудио для записи %s", record.pk)

    # --- PRUNE: если это полностью синтезированные "Untitled"-треки и часть осталась без превью — удалим пустые ---
    qs = record.tracks.order_by("position_index", "id")
    titles = list(qs.values_list("title", flat=True))
    all_untitled = titles and all((t or "").lower().startswith("untitled") for t in titles)

    if all_untitled:
        pruned = qs.filter(Q(audio_preview__isnull=True) | Q(audio_preview__exact="")).delete()
        logger.info("[audio] prune: удалено плейсхолдеров без превью: %s", pruned[0])

    # нормализуем адрес карточки товара
    page_url = _resolve_product_page_url(record, page_url)
    if not page_url:
        logger.info("[redeye_player] record %s has no product_page URL — пропуск", record.pk)
        return 0

    # 1) берём треки и УПОРЯДОЧИВАЕМ ТОЛЬКО ПО position_index
    #    - None идут в конец, внутри групп сортируем по id для стабильности
    tracks_all: List[Track] = list(record.tracks.all())
    tracks_with_idx: List[Tuple[int, Track]] = []
    tracks_without_idx: List[Track] = []
    for t in tracks_all:
        idx = getattr(t, "position_index", None)
        if isinstance(idx, int):
            tracks_with_idx.append((idx, t))
        else:
            tracks_without_idx.append(t)

    if tracks_without_idx:
        logger.warning(
            "[redeye_player] у %d трек(ов) записи %s нет position_index — они будут привязаны в конце по порядку.",
            len(tracks_without_idx), record.pk
        )

    tracks_with_idx.sort(key=lambda it: (it[0], it[1].id))  # по индексу затем по id
    tracks_without_idx.sort(key=lambda t: t.id)  # хвост — по id
    tracks: List[Track] = [t for _, t in tracks_with_idx] + tracks_without_idx

    if not tracks:
        logger.info("[redeye_player] no tracks for record %s", record.pk)
        return 0

    # 2) получаем УЖЕ ОТСОРТИРОВАННЫЕ по кнопкам (a,b,c,...) URL'ы
    urls = collect_redeye_media_urls(page_url, per_click_timeout_sec=per_click_timeout_sec, debug=False)
    if not urls:
        logger.info("[redeye_player] no media urls captured for %s", page_url)
        return 0

    # 3) сопоставляем: ТОЛЬКО по порядку position_index
    updated = 0
    for track, url in zip(tracks, urls):
        # если не force — не перезаписываем уже существующие превью
        if not force and getattr(track.audio_preview, "name", ""):
            continue
        saved = download_to_filefield(track, url, overwrite=force, referer=page_url)
        if saved:
            updated += 1

    logger.info(
        "[redeye_player] record=%s previews updated: %d (urls=%d, tracks=%d)",
        record.pk, updated, len(urls), len(tracks)
    )
    return updated
