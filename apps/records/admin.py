import logging

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from .forms import RecordForm
from .models import Record, Track
from .services.discogs_service import DiscogsService

logger = logging.getLogger(__name__)


class TrackInline(admin.TabularInline):
    """
    Inline-администратор для отображения треков в интерфейсе записи (Record).
    """

    model = Track
    extra = 0
    readonly_fields = ("position", "title", "duration", "youtube_url")
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        """Запрещаем добавление треков через админку."""
        return False


class RecordAdmin(admin.ModelAdmin):
    """
    Административный интерфейс для модели Record.
    """

    form = RecordForm
    inlines = [TrackInline]

    # Поля для создания новой записи
    add_fieldsets = (
        (
            None,
            {
                "fields": ("barcode", "catalog_number"),
                "description": "Введите штрих-код или каталожный номер для импорта из Discogs",
            },
        ),
    )

    # Поля для редактирования существующей записи
    fieldsets = (
        (
            "Основная информация",
            {"fields": ("title", "artists", "label", "release_year")},
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
            {"fields": ("cover_image", "notes"), "classes": ("collapse",)},
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
    )
    list_filter = ("condition", "genres", "styles", "created", "modified")
    search_fields = (
        "title",
        "barcode",
        "catalog_number",
        "discogs_id",
        "artists__name",
        "label__name",
    )
    ordering = ("-created",)
    date_hierarchy = "created"

    # Оптимизация запросов
    list_select_related = ("label",)
    list_prefetch_related = ("artists", "genres", "styles")

    # Действия
    actions = ["update_from_discogs"]

    def get_artists_display(self, obj):
        """Отображение артистов в списке."""
        artists = obj.artists.all()[:3]  # Первые 3 артиста
        names = [a.name for a in artists]
        if obj.artists.count() > 3:
            names.append("...")
        return ", ".join(names) or "-"

    get_artists_display.short_description = "Артисты"

    def get_fieldsets(self, request, obj=None):
        """Возвращает fieldsets в зависимости от создания/редактирования."""
        if not obj:
            return self.add_fieldsets
        return self.fieldsets

    def get_inline_instances(self, request, obj=None):
        """Показываем треки только для существующих записей."""
        if obj and obj.pk:
            return super().get_inline_instances(request, obj)
        return []

    def save_model(self, request, obj, form, change):
        """Сохранение модели с обработкой импорта из Discogs."""
        # Проверяем, был ли обнаружен дубликат при импорте
        if hasattr(form, "duplicate_record"):
            duplicate = form.duplicate_record

            messages.info(
                request,
                format_html(
                    'Запись "{}" (Discogs ID: {}) уже существует в базе данных. '
                    "Недостающие данные были обновлены.",
                    duplicate.title,
                    duplicate.discogs_id,
                ),
            )

            # Устанавливаем флаг для перенаправления
            self._redirect_to_existing = duplicate
            return

        # Стандартное сохранение
        super().save_model(request, obj, form, change)

        # Добавляем сообщения об импорте
        if not change:  # Новая запись
            if obj.discogs_id:
                messages.success(
                    request,
                    f'Запись "{obj.title}" успешно импортирована из Discogs (ID: {obj.discogs_id})',
                )
                logger.info(f"Successfully imported record {obj.pk} from Discogs")
            else:
                messages.warning(
                    request,
                    "Запись создана, но данные из Discogs не найдены. "
                    "Заполните информацию вручную или попробуйте обновить из Discogs позже.",
                )
                logger.warning(f"Created record {obj.pk} without Discogs data")

    def response_add(self, request, obj, post_url_continue=None):
        """Обработка ответа после создания записи."""
        # Проверяем флаг перенаправления на дубликат
        if hasattr(self, "_redirect_to_existing"):
            existing_obj = self._redirect_to_existing
            delattr(self, "_redirect_to_existing")

            # Перенаправляем на существующую запись
            return redirect(
                reverse(
                    f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                    args=[existing_obj.pk],
                )
            )

        # Если импорт успешен, перенаправляем на редактирование
        if obj.discogs_id:
            messages.info(
                request,
                "Проверьте импортированные данные и при необходимости отредактируйте их.",
            )
            # Перенаправляем на страницу редактирования
            redirect_url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
            return redirect(redirect_url)

        # Стандартное поведение
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        """Обработка ответа после изменения записи."""
        # Проверяем флаг перенаправления (если вдруг при редактировании нашелся дубликат)
        if hasattr(self, "_redirect_to_existing"):
            existing_obj = self._redirect_to_existing
            delattr(self, "_redirect_to_existing")

            return redirect(
                reverse(
                    f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                    args=[existing_obj.pk],
                )
            )

        return super().response_change(request, obj)

    def update_from_discogs(self, request, queryset):
        """Действие для обновления записей из Discogs."""
        updated = 0
        errors = 0
        service = DiscogsService()  # Создаем экземпляр сервиса

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
                # Получаем релиз из Discogs по ID
                release = service.api_client._make_request(
                    service.api_client.client.release, record.discogs_id
                )

                if release:
                    # Обновляем данные записи
                    service.importer._update_record(release, record, save_image=True)
                    updated += 1
                    self.message_user(
                        request,
                        f'Запись "{record}" успешно обновлена',
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        f'Не удалось получить данные для записи "{record}" из Discogs',
                        level=messages.WARNING,
                    )
                    errors += 1
            except Exception as e:
                logger.error(f"Failed to update record {record.pk}: {str(e)}")
                self.message_user(
                    request,
                    f'Ошибка при обновлении записи "{record}": {str(e)}',
                    level=messages.ERROR,
                )
                errors += 1

        # Итоговое сообщение
        self.message_user(
            request,
            f"Обновлено записей: {updated}, ошибок: {errors}",
            level=messages.INFO,
        )

    def get_readonly_fields(self, request, obj=None):
        """Делаем discogs_id только для чтения."""
        if obj:  # Редактирование
            return ("discogs_id", "created", "modified")
        return ()

    def has_delete_permission(self, request, obj=None):
        """Ограничиваем удаление записей с заказами."""
        if obj and obj.order_items.exists():
            return False
        return super().has_delete_permission(request, obj)


# Регистрация
admin.site.register(Record, RecordAdmin)
