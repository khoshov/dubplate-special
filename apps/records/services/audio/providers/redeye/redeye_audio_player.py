from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from django.db.models import Q

from records.constants import (
    REDEYE_PLAYER_DEFAULT_CLICK_TIMEOUT_SEC,
    REDEYE_PLAYER_PRUNE_UNTITLED,
)
from records.models import Record, Track, RecordSource
from records.services.audio.common.downloader import download_audio_to_track
from .redeye_audio_scraper import collect_redeye_audio_urls

logger = logging.getLogger(__name__)


def _resolve_product_page_url(
    record: Record, explicit_url: Optional[str] = None
) -> Optional[str]:
    """Метод определяет URL карточки Redeye для записи.

    Приоритет источников:
        1) Явно переданный `explicit_url`.
        2) `RecordSource(provider=REDEYE, role=PRODUCT_PAGE, can_fetch_audio=True)`.
        3) (Legacy) `record.source_url`, если поле присутствует.

    Args:
        record: Запись, для которой требуется URL карточки.
        explicit_url: Явно заданный URL карточки.

    Returns:
        Валидный URL карточки либо None, если источник не найден.
    """
    if explicit_url:
        logger.debug("[redeye_player] явный URL карточки: %s", explicit_url)
        return explicit_url

    try:
        src = (
            record.sources.filter(
                provider=RecordSource.Provider.REDEYE,
                role=RecordSource.Role.PRODUCT_PAGE,
                can_fetch_audio=True,
            )
            .values_list("url", flat=True)
            .first()
        )
        if src:
            logger.debug("[redeye_player] URL из RecordSource: %s", src)
            return src
    except Exception as exc:
        logger.debug("[redeye_player] не удалось получить URL из RecordSource: %s", exc)

    legacy_url = getattr(record, "source_url", None)
    if legacy_url:
        logger.debug("[redeye_player] legacy record.source_url: %s", legacy_url)
    return legacy_url


def _ordered_tracks(record: Record) -> List[Track]:
    """Метод возвращает треки записи в порядке привязки к аудио.

    Сортировка:
        1) Все треки с заполненным `position_index` по возрастанию; при равенстве — по id.
        2) Затем треки без `position_index` — по id.

    Args:
        record: Запись, чьи треки нужно упорядочить.

    Returns:
        Список треков в порядке привязки к полученным URL.
    """
    tracks_all: List[Track] = list(record.tracks.all())
    with_idx: List[Tuple[int, Track]] = []
    without_idx: List[Track] = []
    for track in tracks_all:
        idx = getattr(track, "position_index", None)
        if isinstance(idx, int):
            with_idx.append((idx, track))
        else:
            without_idx.append(track)

    if without_idx:
        logger.warning(
            "[redeye_player] у %d трек(ов) записи %s нет position_index — они будут привязаны в конце по порядку.",
            len(without_idx),
            record.pk,
        )

    with_idx.sort(key=lambda it: (it[0], it[1].id))
    without_idx.sort(key=lambda t: t.id)
    return [t for _, t in with_idx] + without_idx


def _prune_empty_untitled_placeholders(record: Record) -> None:
    """Метод удаляет плейсхолдеры «Untitled...» без аудио, если все треки — 'Untitled…'."""
    qs = record.tracks.order_by("position_index", "id")
    titles = list(qs.values_list("title", flat=True))
    all_untitled = bool(titles) and all(
        (t or "").lower().startswith("untitled") for t in titles
    )
    if not all_untitled:
        return
    deleted = qs.filter(
        Q(audio_preview__isnull=True) | Q(audio_preview__exact="")
    ).delete()
    logger.info("[audio] удалено плейсхолдеров без аудио: %s.", deleted[0])


def attach_audio_from_redeye_player(
    record: Record,
    *,
    page_url: Optional[str] = None,
    force: bool = False,
    per_click_timeout_sec: int = REDEYE_PLAYER_DEFAULT_CLICK_TIMEOUT_SEC,
) -> int:
    """Метод прикрепляет аудио к трекам записи, используя плеер Redeye.

    Метод реализует:
        - разрешение URL карточки (если не передан явно),
        - опциональную зачистку плейсхолдеров «Untitled...» без аудио,
        - сбор аудио-URL с карточки (клики по кнопкам плеера),
        - сопоставление ссылок с треками по их порядку,
        - скачивание и сохранение аудио-файлов в `Track.audio_preview`.

    Args:
        record: Запись, чьи треки требуется заполнить аудио.
        page_url: Валидный URL карточки Redeye; если не указан — берётся из `RecordSource`.
        force: Перезаписывать ли уже существующие файлы у треков.
        per_click_timeout_sec: Таймаут ожидания после клика по кнопке плеера.

    Returns:
        Количество треков, у которых появилось или обновилось аудио.
    """
    logger.info("[redeye_player] запуск для записи %s.", record.pk)

    if REDEYE_PLAYER_PRUNE_UNTITLED:
        _prune_empty_untitled_placeholders(record)

    # URL карточки
    page_url = _resolve_product_page_url(record, page_url)
    if not page_url:
        logger.info(
            "[redeye_player] у записи %s отсутствует URL карточки — пропуск.", record.pk
        )
        return 0

    # Треки в порядке привязки
    tracks: List[Track] = _ordered_tracks(record)
    if not tracks:
        logger.info("[redeye_player] у записи %s нет треков.", record.pk)
        return 0

    # Сбор ссылок плеера
    urls = collect_redeye_audio_urls(
        page_url, per_click_timeout_sec=per_click_timeout_sec, debug=False
    )
    logger.debug("[redeye_player] ссылок получено=%d: %s", len(urls), urls)
    if not urls:
        logger.info(
            "[redeye_player] не удалось получить медиа-ссылки для %s.", page_url
        )
        return 0

    # Сопоставление и скачивание
    updated = 0
    for track, url in zip(tracks, urls):
        if not force and getattr(track.audio_preview, "name", ""):
            continue
        saved = download_audio_to_track(track, url, overwrite=force, referer=page_url)
        if saved:
            updated += 1

    logger.info(
        "[redeye_player] запись=%s, обновлено аудио: %d (urls=%d, tracks=%d).",
        record.pk,
        updated,
        len(urls),
        len(tracks),
    )
    return updated
