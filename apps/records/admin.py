# apps/records/admin.py
import logging
from typing import Optional

from django.contrib import admin, messages
from django.core.exceptions import SuspiciousFileOperation
from django.http import HttpRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from .forms import RecordForm
from .models import Record, Track, Artist
from .services import DiscogsService, ImageService, RecordService

logger = logging.getLogger(__name__)


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

    # порядок и колонки
    fields = ("position_index", "position", "title", "duration", "youtube_url", "audio_preview_player")
    readonly_fields = ("position_index", "position", "title", "duration", "youtube_url", "audio_preview_player")

    class Media:
        css = {"all": ("records/admin/track_inline.css",)}

    def has_add_permission(self, request: HttpRequest, obj: Optional[Record] = None) -> bool:
        """Запрещает добавление треков через админку (импортируются из внешних источников)."""
        return False

    @admin.display(description="Preview")
    def audio_preview_player(self, obj: Track) -> str:
        """
        Встроенный аудио-плеер для локального MP3 превью (Track.audio_preview).
        Если файла нет или база не отдаёт URL — показываем '-'.
        """
        f = getattr(obj, "audio_preview", None)
        if not f:
            return "-"
        try:
            url = f.url
        except (ValueError, OSError, SuspiciousFileOperation):
            return "-"
        return format_html('<audio controls preload="none" src="{}"></audio>', url)


# Поля для создания новой записи (ДВА варианта: Discogs/Redeye)
add_fieldsets_discogs = (
    (
        None,
        {
            # добавлено: поле source для выбора источника (Discogs | Redeye)
            "fields": ("source", "barcode", "catalog_number"),
            # добавлено: уточнили описание, что теперь доступен Redeye
            "description": (
                "Выберите источник данных (Discogs или Redeye), затем введите штрих-код или каталожный номер. "
                "Для Redeye используется только каталожный номер."
            ),
        },
    ),
)
add_fieldsets_redeye = (
    (
        None,
        {
            "fields": ("source", "catalog_number"),
            "description": "Импорт из Redeye использует только каталожный номер.",
        },
    ),
)


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin):
    """Административный интерфейс для записей.

    Интегрирован с Discogs для импорта и обновления данных.
    При создании новой записи автоматически импортирует данные
    из Discogs по штрих-коду или каталожному номеру.
    """

    form = RecordForm
    autocomplete_fields = ("artists",)
    inlines = [TrackInline]

    # Поля для редактирования существующей записи
    fieldsets = (
        (
            "Основная информация",
            {"fields": ("title", "artists", "label")},
        ),
        (
            "Дата релиза",
            {"fields": ("release_year", "release_month", "release_day", "is_expected")},
        ),
        (
            "Идентификаторы",
            {
                "fields": ("barcode", "catalog_number", "discogs_id"),
                "classes": ("collapse",),
            },
        ),
        ("Детали", {"fields": ("genres", "styles", "formats", "country", "condition")}),
        ("Склад и цены", {"fields": ("stock", "price")}),
        (
            "Дополнительно",
            {
                "fields": ("cover_image", "notes"),
                "classes": ("collapse",),
            },
        ),
    )

    # Настройки списка
    list_display = (
        "title",
        "get_artists_display",
        "label",
        "catalog_number",
        "barcode",
        "stock",
        "price",
        "discogs_id",
        "created",
        "release_year",
        "release_month",
        "release_day",
        "is_expected",
    )
    list_filter = ("condition", "genres", "styles", "created", "modified", "is_expected")
    search_fields = (
        "title",
        "barcode",
        "catalog_number",
        "discogs_id",
        "artists__name",
        "label__name",
    )
    ordering = ("is_expected", "release_year", "release_month", "release_day", "-created")
    date_hierarchy = "created"

    # Оптимизация запросов
    list_select_related = ("label",)

    # Действия
    actions = ["update_from_discogs"]

    def __init__(self, *args, **kwargs):
        """Инициализация админки."""
        super().__init__(*args, **kwargs)
        self.record_service = RecordService(
            discogs_service=DiscogsService(), image_service=ImageService()
        )
        # объявляем атрибут перенаправления заранее (для анализатора типов)
        self._redirect_to_existing: Optional[Record] = None

    def get_artists_display(self, obj: Record) -> str:
        """Показывает первых трёх артистов, если больше — добавляет '...'."""
        artists = obj.artists.all()[:3]
        names = [a.name for a in artists]
        if obj.artists.count() > 3:
            names.append("...")
        return ", ".join(names) or "-"

    get_artists_display.short_description = "Артисты"

    def get_fieldsets(self, request: HttpRequest, obj: Optional[Record] = None):
        """Возвращает fieldsets в зависимости от операции (создание / редактирование)."""
        if obj:
            return self.fieldsets

        # add-форма: выбираем набор полей по источнику
        source = (request.POST.get("source") or request.GET.get("source") or "discogs").lower()
        if source == "redeye":
            return add_fieldsets_redeye
        return add_fieldsets_discogs

    def get_inline_instances(self, request: HttpRequest, obj: Optional[Record] = None):
        """Показываем треки только для существующих записей."""
        if obj and obj.pk:
            return super().get_inline_instances(request, obj)
        return []

    def save_model(self, request: HttpRequest, obj: Record, form: RecordForm, change: bool) -> None:
        """Сохранение модели с обработкой импорта и дубликатов."""
        # Дубликат при импорте
        duplicate = getattr(form, "duplicate_record", None)
        if duplicate is not None:
            messages.info(
                request,
                format_html(
                    'Запись "{}" (Discogs ID: {}) уже существует в базе данных.',
                    duplicate.title,
                    duplicate.discogs_id,
                ),
            )
            # настроим редирект в response_add
            self._redirect_to_existing = duplicate
            return

        # Стандартное сохранение
        super().save_model(request, obj, form, change)

        # Сообщения об импорте для новых записей
        if not change:
            source = getattr(form, "cleaned_data", {}).get("source") or request.POST.get("source")
            if obj.discogs_id:
                messages.success(
                    request,
                    f'Запись "{obj.title}" успешно импортирована из Discogs (ID: {obj.discogs_id})',
                )
            else:
                if source == "redeye":
                    messages.info(
                        request,
                        "Попытка импорта из Redeye завершена. "
                        "Если данные не подтянулись автоматически, заполните карточку вручную "
                        "или повторите импорт позже.",
                    )
                else:
                    messages.warning(
                        request,
                        "Запись создана, но данные из Discogs не найдены. "
                        "Заполните информацию вручную или попробуйте обновить из Discogs позже.",
                    )

    def response_add(self, request: HttpRequest, obj: Record, post_url_continue: Optional[str] = None):
        """Перенаправляет на существующую запись при дубликате или на редактирование после импорта."""
        # Перенаправление на уже существующую запись
        if self._redirect_to_existing is not None:
            existing_obj = self._redirect_to_existing
            self._redirect_to_existing = None
            return redirect(
                reverse(
                    f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                    args=[existing_obj.pk],
                )
            )

        # Если импорт успешен, ведём на редактирование
        if obj.discogs_id:
            messages.info(request, "Проверьте импортированные данные и при необходимости отредактируйте их.")
            redirect_url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
            return redirect(redirect_url)

        # Для Redeye discogs_id обычно нет — всё равно ведём на редактирование
        source = request.POST.get("source")
        if source == "redeye":
            redirect_url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
            return redirect(redirect_url)

        return super().response_add(request, obj, post_url_continue)

    def update_from_discogs(self, request: HttpRequest, queryset):
        """Массовое обновление записей из Discogs."""
        updated = 0
        errors = 0

        for record in queryset:
            if not record.discogs_id:
                self.message_user(
                    request,
                    f'Запись "{record}" не имеет Discogs ID',
                    level=messages.WARNING,
                )
                errors += 1
                continue

            try:
                self.record_service.update_from_discogs(record)
                updated += 1
                self.message_user(
                    request,
                    f'Запись "{record}" успешно обновлена',
                    level=messages.SUCCESS,
                )
            except Exception as e:  # noqa: BLE001 — широкая ловля намеренна: внешний API/сеть/парсинг
                logger.error("Failed to update record %s: %s", record.pk, e)
                self.message_user(
                    request,
                    f'Ошибка при обновлении записи "{record}": {e}',
                    level=messages.ERROR,
                )
                errors += 1

        self.message_user(
            request,
            f"Обновлено записей: {updated}, ошибок: {errors}",
            level=messages.INFO,
        )

    update_from_discogs.short_description = "Обновить из Discogs"

    def get_readonly_fields(self, request: HttpRequest, obj: Optional[Record] = None):
        """Поле is_expected вычисляется в save() → делаем read-only."""
        base = ("is_expected",)
        if obj:
            return base + ("discogs_id", "created", "modified")
        return base

    def has_delete_permission(self, request: HttpRequest, obj: Optional[Record] = None) -> bool:
        """Запрещает удаление записей, которые есть в заказах."""
        if obj and hasattr(obj, "order_items") and obj.order_items.exists():
            return False
        return super().has_delete_permission(request, obj)

    # M2M опциональны (у Redeye иногда отсутствуют) — не требуем на форме
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        field = super().formfield_for_manytomany(db_field, request, **kwargs)
        field.required = False
        return field


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    search_fields = ("name",)

    def get_model_perms(self, request: HttpRequest):
        """
        Возвращаем пустые права — модель не отображается
        в боковом меню и индексе админки, но остаётся доступной
        для функции автозаполнения.
        """
        return {}
