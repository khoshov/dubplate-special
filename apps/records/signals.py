# apps/records/signals.py
import os
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from .models import Record, Track


# ------- helpers -------


def _safe_storage_delete(storage, rel_path: str) -> None:
    """Безопасно удаляет файл из storage по относительному пути."""
    if not rel_path:
        return
    try:
        storage.delete(rel_path)
    except Exception:
        # не валим сохранение/удаление модели из-за файловой ошибки
        pass


def _safe_rmdir(storage, rel_dir: str) -> None:
    """
    Пытается удалить ПУСТУЮ директорию. Работает с FileSystemStorage.
    Для других стораджей просто молча игнорируем.
    """
    try:
        abs_dir = storage.path(rel_dir)  # у FileSystemStorage есть .path()
    except Exception:
        return

    try:
        if os.path.isdir(abs_dir) and not os.listdir(abs_dir):
            os.rmdir(abs_dir)
    except Exception:
        pass


def _move_file_to_id_folder(instance: Record, field_name: str) -> None:
    """
    Если файл лежит в папке '_new', переносим его в папку с реальным id:
    <app>/<model>/<field>/_new/filename -> <app>/<model>/<field>/<id>/filename
    """
    f = getattr(instance, field_name, None)
    if not f or not getattr(f, "name", ""):
        return

    old_rel = f.name.replace("\\", "/")
    parts = old_rel.split("/")
    if "_new" not in parts:
        return  # уже в целевой структуре

    storage = f.storage
    filename = parts[-1]
    app_label = instance._meta.app_label
    model_name = instance._meta.model_name
    new_rel = "/".join([app_label, model_name, field_name, str(instance.pk), filename])

    if new_rel == old_rel:
        return

    try:
        # читаем → сохраняем → апдейтим поле в БД (без повторного save модели) → удаляем старый файл
        with storage.open(old_rel, "rb") as src:
            storage.save(new_rel, src)

        type(instance).objects.filter(pk=instance.pk).update(**{field_name: new_rel})
        # локально обновим name — чтобы в админке сразу отобразился новый путь
        getattr(instance, field_name).name = new_rel

        _safe_storage_delete(storage, old_rel)
    except Exception:
        # не прерываем post_save
        pass


# ------- signals -------


@receiver(pre_save, sender=Record)
def cleanup_old_cover_on_change(sender, instance: Record, **kwargs):
    """
    Если у записи уже была обложка и её заменили — удалить старый файл.
    Работает и при переименовании/перемещении (сравниваем относительные пути).
    """
    if not instance.pk:
        return

    try:
        old = Record.objects.get(pk=instance.pk)
    except Record.DoesNotExist:
        return

    old_file = getattr(old, "cover_image", None)
    new_file = getattr(instance, "cover_image", None)

    old_name = getattr(old_file, "name", "") or ""
    new_name = getattr(new_file, "name", "") or ""

    if old_name and old_name != new_name:
        _safe_storage_delete(old_file.storage, old_name)


@receiver(post_save, sender=Record)
def move_cover_after_save(sender, instance: Record, created, **kwargs):
    """
    После первого сохранения переносим файл из '_new' в '<id>/'.
    Если будут другие File/Image-поля — вызови _move_file_to_id_folder для них тоже.
    """
    _move_file_to_id_folder(instance, "cover_image")


@receiver(post_delete, sender=Record)
def cleanup_cover_on_delete(sender, instance: Record, **kwargs):
    """
    Удаляем файл обложки и очищаем пустые директории вверх до каталога поля:
    <app>/<model>/<field>/<id>/filename → чистим <id>/ и, если опустело, <field>/.
    """
    f = getattr(instance, "cover_image", None)
    if not f or not getattr(f, "name", ""):
        return

    storage = f.storage
    rel_path = f.name.replace(
        "\\", "/"
    )  # e.g. "records/record/cover_image/42/title.jpg"

    # 1) удалить сам файл
    _safe_storage_delete(storage, rel_path)

    # 2) подниматься вверх и удалять пустые каталоги до уровня <app>/<model>/<field>
    #    (не поднимаемся выше директории поля)
    rel_dir = os.path.dirname(rel_path)
    stop_at = "/".join(
        [instance._meta.app_label, instance._meta.model_name, "cover_image"]
    )

    # сначала удалим папку с id, затем — при необходимости — сам каталог поля
    if rel_dir and rel_dir != stop_at:
        _safe_rmdir(storage, rel_dir)

    _safe_rmdir(storage, stop_at)


# --- добавлено: очистка mp3 при удалении треков ---


@receiver(post_delete, sender=Track)
def cleanup_audio_on_delete(sender, instance: Track, **kwargs):
    """
    При удалении трека удаляем файл превью (audio_preview)
    и чистим пустые директории, если используется FileSystemStorage.
    """
    f = getattr(instance, "audio_preview", None)
    if not f or not getattr(f, "name", ""):
        return

    storage = f.storage
    rel_path = f.name.replace(
        "\\", "/"
    )  # например: "records/track/audio_preview/123/clip.mp3"

    # удалить сам mp3
    _safe_storage_delete(storage, rel_path)

    # удалить пустые директории вверх до уровня поля audio_preview
    rel_dir = os.path.dirname(rel_path)
    stop_at = "/".join(
        [instance._meta.app_label, instance._meta.model_name, "audio_preview"]
    )
    if rel_dir and rel_dir != stop_at:
        _safe_rmdir(storage, rel_dir)
    _safe_rmdir(storage, stop_at)
