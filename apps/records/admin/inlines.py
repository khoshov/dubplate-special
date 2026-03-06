from __future__ import annotations

from typing import Optional

from django.contrib import admin
from django.http import HttpRequest

from ..models import Record, Track


class TrackInline(admin.TabularInline):
    """
    Inline-администратор для треков.

    Отображает треки записи в табличном виде.
    Треки доступны только для чтения и не могут быть добавлены через админку.
    """

    model = Track
    extra = 0
    can_delete = False
    show_change_link = False

    fields = (
        "position_index",
        "position",
        "title",
        "duration",
        "youtube_url",
        "audio_preview",
    )
    readonly_fields = (
        "position_index",
        "position",
        "title",
        "duration",
        "youtube_url",
        "audio_preview",
    )

    class Media:
        css = {"all": ("records/admin/track_inline.css",)}

    def has_add_permission(
        self, request: HttpRequest, obj: Optional[Record] = None
    ) -> bool:
        """Запрещает добавление треков через админку (импортируются из внешних источников)."""
        return False
