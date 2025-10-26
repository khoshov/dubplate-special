from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from django.db.models import Q
from playwright.sync_api import Browser
from records.constants import REDEYE_PLAYER_PRUNE_UNTITLED
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
        logger.debug("явный URL карточки: %s", explicit_url)
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
            logger.debug("URL из RecordSource: %s", src)
            return src
    except Exception as exc:
        logger.debug("не удалось получить URL из RecordSource: %s", exc)

    legacy_url = getattr(record, "source_url", None)
    if legacy_url:
        logger.debug("legacy record.source_url: %s", legacy_url)
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
            "у %d трек(ов) записи %s нет position_index — они будут привязаны в конце по порядку.",
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
    logger.info("удалено плейсхолдеров без аудио: %s.", deleted[0])


def attach_audio_from_redeye_player(
    record: Record,
    *,
    page_url: Optional[str] = None,
    force: bool = False,
    per_click_timeout_sec: Optional[int] = None,
    browser: Optional[Browser] = None
) -> int:
    """
    Метод прикрепляет аудио-превью к трекам записи из источника Redeye.

    Архитектура:
      • Сервис не подменяет значения и не считает дефолты — параметры пробрасываются вниз.
      • Значения по умолчанию применяются в скраперe (нижний уровень).

    Args:
        record: Запись, треки которой нужно заполнить аудио.
        force: Принудительно перезаписывать уже существующие файлы у треков.
        per_click_timeout_sec: Таймаут ожидания появления URL после клика (сек).
            Если None — дефолт будет применён скрапером.
        page_url: Явный URL карточки Redeye (если не указан — будет определён ниже).
        browser: Внешний экземпляр Playwright Browser для массовой обработки.
            Если передан — используется как есть (без запуска/остановки нового браузера).

    Returns:
        Количество треков, у которых аудио появилось или обновилось.
    """
    logger.info("запуск для записи %s.", record.pk)

    if REDEYE_PLAYER_PRUNE_UNTITLED:
        _prune_empty_untitled_placeholders(record)

    # URL карточки
    page_url = _resolve_product_page_url(record, page_url)
    if not page_url:
        logger.info(
            "у записи %s отсутствует URL карточки — пропуск.", record.pk
        )
        return 0

    # Треки в порядке привязки
    tracks: List[Track] = _ordered_tracks(record)
    if not tracks:
        logger.info("у записи %s нет треков.", record.pk)
        return 0

    # Сбор ссылок плеера
    urls = collect_redeye_audio_urls(
        page_url, per_click_timeout_sec=per_click_timeout_sec, debug=False, browser=browser,
    )
    logger.debug("ссылок получено=%d: %s", len(urls), urls)
    if not urls:
        logger.info(
            "не удалось получить медиа-ссылки для %s.", page_url
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
        "запись=%s, обновлено аудио: %d (urls=%d, tracks=%d).",
        record.pk,
        updated,
        len(urls),
        len(tracks),
    )
    return updated
