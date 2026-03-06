from __future__ import annotations

import os
from typing import Any

from django.utils.deconstruct import deconstructible
from django.utils.text import slugify


@deconstructible
class PathByInstance:
    """
    Генератор upload_to-путей вида:
      <app>/<model>/<field>/<pk>/<filename>

    По умолчанию требует наличие pk (лучше всего работает в «двухфазном» сохранении):
      1) сначала сохраняем объект → появляется pk,
      2) затем присваиваем файл и сохраняем только поле.

    Args:
        field_name: имя File/Image поля, используемое в пути.
        require_pk: если True — падает, если pk нет (по умолчанию True).
                    Если False — положит файл во временную папку '_new'.
    """

    def __init__(self, field_name: str, *, require_pk: bool = True) -> None:
        self.field_name = field_name
        self.require_pk = require_pk

    def __call__(self, instance: Any, filename: str) -> str:
        app_label = instance._meta.app_label
        model_name = instance._meta.model_name

        base_name, ext = os.path.splitext(filename or "")
        ext = (ext or ".bin").lower()

        title = (
            getattr(instance, "title", "") or getattr(instance, "name", "") or "file"
        )
        safe_title = slugify(title) or "file"

        new_filename = f"{safe_title}{ext}"

        if not getattr(instance, "pk", None):
            if self.require_pk:
                raise ValueError(
                    f"{self.__class__.__name__}: у instance нет pk — "
                    f"используйте двухфазное сохранение (сначала save(), затем запись файла)."
                )
            return os.path.join(
                app_label, model_name, self.field_name, "_new", new_filename
            )

        return os.path.join(
            app_label, model_name, self.field_name, str(instance.pk), new_filename
        )
