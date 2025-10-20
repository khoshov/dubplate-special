"""
Сигналы для обслуживания файловых полей моделей:
- корректное удаление старой обложки при замене;
- перенос обложки из временной папки '_new' в папку с pk после первого сохранения;
- уборка файлов и пустых директорий при удалении Record/Track.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver

from .models import Record, Track

logger = logging.getLogger(__name__)


def _safe_storage_delete(storage: Any, rel_path: str) -> None:
    """
    Удаляет файл из storage по относительному пути, не прерывая основной поток.

    Args:
        storage: backend хранилища (FileSystemStorage, S3 и т.п.).
        rel_path: относительный путь внутри стораджа.

    """
    if not rel_path:
        return
    try:
        storage.delete(rel_path)
    except Exception as exc:  # --- изменено: логируем на DEBUG, не валим сигнал ---
        logger.debug("Не удалось удалить файл из storage (%s): %s", rel_path, exc)


def _safe_rmdir(storage: Any, rel_dir: str) -> None:
    """
    Пытается удалить пустую директорию (актуально для FileSystemStorage).
    Для стораджей без .path() — молча игнорирует.

    Args:
        storage: backend хранилища.
        rel_dir: относительная директория.

    """
    try:
        abs_dir = storage.path(rel_dir)
    except Exception:
        return

    try:
        if os.path.isdir(abs_dir) and not os.listdir(abs_dir):
            os.rmdir(abs_dir)
    except Exception as exc:
        logger.debug("Не удалось удалить пустую директорию (%s): %s", abs_dir, exc)


def _move_file_to_id_folder(instance: Record, field_name: str) -> None:
    """
    Переносит файл из временной папки '_new' в папку с реальным pk:
    <app>/<model>/<field>/_new/filename → <app>/<model>/<field>/<pk>/filename

    Args:
        instance: экземпляр модели Record (уже сохранён).
        field_name: имя File/Image-поля модели.

    """
    if not instance.pk:
        return

    f = getattr(instance, field_name, None)
    if not f or not getattr(f, "name", ""):
        return

    old_rel = str(f.name).replace("\\", "/")
    parts = old_rel.split("/")
    if "_new" not in parts:
        return

    storage = f.storage
    filename = parts[-1]
    app_label = instance._meta.app_label
    model_name = instance._meta.model_name
    new_rel = "/".join([app_label, model_name, field_name, str(instance.pk), filename])

    if new_rel == old_rel:
        return

    try:
        with storage.open(old_rel, "rb") as src:
            storage.save(new_rel, src)

        type(instance).objects.filter(pk=instance.pk).update(**{field_name: new_rel})
        getattr(instance, field_name).name = new_rel

        _safe_storage_delete(storage, old_rel)
    except FileNotFoundError as exc:  # --- добавлено: узкий перехват ---
        logger.debug("Исходный файл для переноса не найден (%s): %s", old_rel, exc)
    except Exception as exc:
        logger.debug("Не удалось перенести файл %s → %s: %s", old_rel, new_rel, exc)


@receiver(pre_save, sender=Record)
def cleanup_old_cover_on_change(sender: type[Record], instance: Record, **kwargs: Any) -> None:
    """
    Метод удаляет старую обложку при её замене.

    Сценарии:
      - при редактировании записи, если относительный путь cover_image изменился;
      - работает и при фактическом перемещении файла (сравнение по .name).

    Args:
        sender: класс модели (Record).
        instance: редактируемый объект.
        **kwargs: дополнительные аргументы от сигналов Django.

    """
    if not instance.pk:
        return

    try:
        old: Optional[Record] = Record.objects.get(pk=instance.pk)
    except Record.DoesNotExist:
        return
    except Exception as exc:
        logger.debug("cleanup_old_cover_on_change: ошибка получения старой версии: %s", exc)
        return

    old_file = getattr(old, "cover_image", None)
    new_file = getattr(instance, "cover_image", None)

    old_name = (getattr(old_file, "name", "") or "").strip()
    new_name = (getattr(new_file, "name", "") or "").strip()

    if old_name and old_name != new_name:
        _safe_storage_delete(getattr(old_file, "storage", None), old_name)


@receiver(post_save, sender=Record)
def move_cover_after_save(sender: type[Record], instance: Record, created: bool, **kwargs: Any) -> None:
    """
    Метод переносит обложку из '_new' в папку с pk после сохранения.

    Args:
        sender: класс модели (Record).
        instance: сохранённый объект.
        created: флаг «создан новый» от Django.
        **kwargs: дополнительные аргументы.

    """
    _move_file_to_id_folder(instance, "cover_image")


@receiver(post_delete, sender=Record)
def cleanup_cover_on_delete(sender: type[Record], instance: Record, **kwargs: Any) -> None:
    """
    Метод удаляет обложку и чистит пустые директории при удалении записи.

    Структура путей:
      records/record/cover_image/<id>/filename → чистим <id>/ и (при необходимости) cover_image/

    """
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
    """
    Метод удаляет превью-аудио и чистит пустые директории при удалении трека.

    Структура путей:
      records/track/audio_preview/<id>/clip.mp3 → чистим <id>/ и (при необходимости) audio_preview/

    """
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
