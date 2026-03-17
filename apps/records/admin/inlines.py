from __future__ import annotations

from typing import Optional

from django import forms
from django.contrib import admin
from django.http import HttpRequest
from django.forms.models import BaseInlineFormSet

from ..models import Record, StructuredFormat, Track
from ..services.record_assembly import get_structured_format_incomplete_error


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


class StructuredFormatInlineForm(forms.ModelForm):
    """Форма inline-редактирования structured format с мягкой обработкой пустых строк."""

    quantity = forms.IntegerField(min_value=1, required=False, label="Количество")

    class Meta:
        model = StructuredFormat
        fields = ("carrier", "quantity", "format_name", "details")

    def clean(self):
        cleaned = super().clean()
        carrier = str(cleaned.get("carrier") or "").strip()
        format_name = str(cleaned.get("format_name") or "").strip()
        details = str(cleaned.get("details") or "").strip()
        quantity = cleaned.get("quantity")

        # Полностью пустая строка считается очищенной и не создаёт новый вариант.
        is_effectively_empty = not any((carrier, format_name, details)) and quantity in (
            None,
            1,
        )
        setattr(self, "is_effectively_empty", is_effectively_empty)

        cleaned["carrier"] = carrier
        cleaned["format_name"] = format_name
        cleaned["details"] = details
        cleaned["quantity"] = 1 if is_effectively_empty else quantity
        return cleaned


class StructuredFormatInlineFormSet(BaseInlineFormSet):
    """FormSet inline-строк формата с автопроставлением номера варианта."""

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return

        active_variant = self._selected_variant_of_format()
        if active_variant is None:
            return

        for index, form in enumerate(self.forms, start=1):
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue

            if self.can_delete and self._should_delete_form(form):
                continue

            variant_of_format = getattr(form.instance, "variant_of_format", None) or index
            if variant_of_format != active_variant:
                continue

            incomplete_error = get_structured_format_incomplete_error(
                carrier=form.cleaned_data.get("carrier"),
                quantity=form.cleaned_data.get("quantity"),
                format_name=form.cleaned_data.get("format_name"),
                details=form.cleaned_data.get("details"),
            )
            if incomplete_error is not None:
                raise forms.ValidationError(incomplete_error)
            return

    def _selected_variant_of_format(self) -> int | None:
        raw_variant = str(self.data.get("active_structured_format_variant") or "").strip()
        if raw_variant.isdigit():
            selected_variant = int(raw_variant)
            if selected_variant > 0:
                return selected_variant

        if not self.forms:
            return None

        first_variant = getattr(self.forms[0].instance, "variant_of_format", None)
        if isinstance(first_variant, int) and first_variant > 0:
            return first_variant

        return 1

    def _next_variant_of_format(self) -> int:
        existing_variants = [
            form.instance.variant_of_format
            for form in self.initial_forms
            if getattr(form.instance, "variant_of_format", None) is not None
        ]
        new_variants = [
            form.instance.variant_of_format
            for form in self.extra_forms
            if getattr(form.instance, "variant_of_format", None) is not None
        ]
        current_max = max(existing_variants + new_variants, default=0)
        return current_max + 1

    def save_new_objects(self, commit: bool = True):
        self.new_objects = []

        for form in self.extra_forms:
            if not form.has_changed():
                continue

            if getattr(form, "is_effectively_empty", False):
                continue

            if self.can_delete and self._should_delete_form(form):
                continue

            if getattr(form.instance, "variant_of_format", None) is None:
                form.instance.variant_of_format = self._next_variant_of_format()

            self.new_objects.append(self.save_new(form, commit=commit))
            if not commit:
                self.saved_forms.append(form)

        return self.new_objects


class StructuredFormatInline(admin.TabularInline):
    """Inline-редактор структурированных строк формата Discogs."""

    model = StructuredFormat
    form = StructuredFormatInlineForm
    formset = StructuredFormatInlineFormSet
    verbose_name = "Структурированный формат релиза"
    verbose_name_plural = "Структурированный формат релиза"
    extra = 0
    can_delete = False
    show_change_link = False
    classes = ("structured-format-inline",)
    template = "admin/edit_inline/record_format_tabular.html"

    fields = ("carrier", "quantity", "format_name", "details")

    class Media:
        css = {"all": ("records/admin/record_format_inline.css",)}
        js = ("records/admin/structured_format_variant_selector.js",)

    def get_extra(
        self, request: HttpRequest, obj: Optional[Record] = None, **kwargs
    ) -> int:
        """Показывает одну пустую строку, если structured rows ещё отсутствуют."""
        if obj is None or not getattr(obj, "pk", None):
            return 0

        return 0 if obj.structured_formats.exists() else 1

    def has_add_permission(
        self, request: HttpRequest, obj: Optional[Record] = None
    ) -> bool:
        """
        Разрешает только первичное заполнение structured-блока.

        Если строки уже существуют, блок не должен вести себя как управляемый
        список с добавлением новых позиций через админку.
        """
        if obj is None or not getattr(obj, "pk", None):
            return False

        return not obj.structured_formats.exists()

    def has_delete_permission(
        self, request: HttpRequest, obj: Optional[Record] = None
    ) -> bool:
        """Удаление строк вручную в этом переходном UI недоступно."""
        return False

    def get_queryset(self, request: HttpRequest):
        """Показывает строки в порядке, пришедшем из Discogs."""
        return super().get_queryset(request).order_by("variant_of_format", "id")
