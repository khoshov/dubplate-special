# apps/records/signals.py
import os, shutil
from django.conf import settings
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver
from django.utils.text import slugify
from .models import Record

def _record_dir(instance: Record) -> str:
    slug = slugify(instance.title or "record")
    return os.path.join(settings.MEDIA_ROOT, "records", slug)

@receiver(post_delete, sender=Record)
def cleanup_record_dir_on_delete(sender, instance: Record, **kwargs):
    # удаляем всю папку записи (обложка + будущие аудио)
    path = _record_dir(instance)
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass

@receiver(pre_save, sender=Record)
def cleanup_old_cover_on_change(sender, instance: Record, **kwargs):
    if not instance.pk:
        return
    try:
        old = Record.objects.get(pk=instance.pk)
    except Record.DoesNotExist:
        return
    # если обложку заменили — удалить старый файл
    if old.cover_image and old.cover_image.name != (instance.cover_image.name or ""):
        try:
            old.cover_image.delete(save=False)
        except Exception:
            pass
