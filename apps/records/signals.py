"""
Сигналы для обслуживания файловых полей моделей:
- удаление старой обложки при замене;
- уборка файлов и пустых директорий при удалении Record/Track.

Важно: перенос файлов из временных путей НЕ выполняется. Вместо этого
сохранение обложки организовано двухфазно на уровне админки (см. RecordAdmin).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import Record, Track

logger = logging.getLogger(__name__)


def _safe_storage_delete(storage: Any, rel_path: str) -> None:
    """Удаляет файл из storage по относительному пути; ошибки не прерывают поток."""
    if not rel_path:
        return
    try:
        storage.delete(rel_path)
    except Exception as exc:
        logger.debug("Не удалось удалить файл из storage (%s): %s", rel_path, exc)


def _safe_rmdir(storage: Any, rel_dir: str) -> None:
    """
    Пытается удалить пустую директорию (актуально для FileSystemStorage).
    Для стораджей без .path() — игнорирует.
    """
    try:
        abs_dir = storage.path(rel_dir)  # у FileSystemStorage есть .path()
    except Exception:
        return

    try:
        if os.path.isdir(abs_dir) and not os.listdir(abs_dir):
            os.rmdir(abs_dir)
    except Exception as exc:
        logger.debug("Не удалось удалить пустую директорию (%s): %s", abs_dir, exc)


@receiver(pre_save, sender=Record)
def cleanup_old_cover_on_change(sender: type[Record], instance: Record, **kwargs: Any) -> None:
    """
    Удаляет старую обложку при её замене (сравнение по относительному пути .name).
    Новые записи (без pk) не затрагиваются.
    """
    if not instance.pk:
        return

    try:
        old: Optional[Record] = Record.objects.get(pk=instance.pk)
    except Record.DoesNotExist:
        return
    except Exception as exc:
        logger.debug("cleanup_old_cover_on_change: не удалось загрузить старую версию: %s", exc)
        return

    old_file = getattr(old, "cover_image", None)
    new_file = getattr(instance, "cover_image", None)

    old_name = (getattr(old_file, "name", "") or "").strip()
    new_name = (getattr(new_file, "name", "") or "").strip()

    if old_name and old_name != new_name:
        _safe_storage_delete(getattr(old_file, "storage", None), old_name)


@receiver(post_delete, sender=Record)
def cleanup_cover_on_delete(sender: type[Record], instance: Record, **kwargs: Any) -> None:
    """Удаляет файл обложки и подчищает пустые директории при удалении записи."""
    f = getattr(instance, "cover_image", None)
    if not f or not getattr(f, "name", ""):
        return

    storage = f.storage
    rel_path = str(f.name).replace("\\", "/")

    _safe_storage_delete(storage, rel_path)

    rel_dir = os.path.dirname(rel_path)
    stop_at = "/".join([instance._meta.app_label, instance._meta.model_name, "cover_image"])

    if rel_dir and rel_dir != stop_at:
        _safe_rmdir(storage, rel_dir)
    _safe_rmdir(storage, stop_at)


@receiver(post_delete, sender=Track)
def cleanup_audio_on_delete(sender: type[Track], instance: Track, **kwargs: Any) -> None:
    """Удаляет файл превью-аудио и подчищает пустые директории при удалении трека."""
    f = getattr(instance, "audio_preview", None)
    if not f or not getattr(f, "name", ""):
        return

    storage = f.storage
    rel_path = str(f.name).replace("\\", "/")

    _safe_storage_delete(storage, rel_path)

    rel_dir = os.path.dirname(rel_path)
    stop_at = "/".join([instance._meta.app_label, instance._meta.model_name, "audio_preview"])

    if rel_dir and rel_dir != stop_at:
        _safe_rmdir(storage, rel_dir)
    _safe_rmdir(storage, stop_at)
