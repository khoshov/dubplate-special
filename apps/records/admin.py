# apps/records/admin.py
import logging

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from records.forms import RecordForm
from records.models import Record, Track, Artist
from records.services import DiscogsService, ImageService, RecordService

logger = logging.getLogger(__name__)


class TrackInline(admin.TabularInline):
    """Inline-администратор для треков.

    Отображает треки записи в табличном виде.
    Треки доступны только для чтения и не могут быть
    добавлены через админку.
    """

    model = Track
    extra = 0
    readonly_fields = ("position", "title", "duration", "youtube_url")
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        """Запрещает добавление треков через админку.

        Треки импортируются только из Discogs.

        Args:
            request: HTTP запрос.
            obj: Родительский объект (Record).

        Returns:
            False - добавление запрещено.
        """
        return False


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

    Attributes:
        record_service: Сервис для работы с записями.
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
        """Инициализация админки.

        Args:
            *args: Позиционные аргументы.
            **kwargs: Именованные аргументы.
        """
        super().__init__(*args, **kwargs)
        # Инициализируем сервис
        self.record_service = RecordService(
            discogs_service=DiscogsService(), image_service=ImageService()
        )

    def get_artists_display(self, obj):
        """Отображение артистов в списке.

        Показывает первых трёх артистов, если больше - добавляет "...".

        Args:
            obj: Экземпляр Record.

        Returns:
            Строка с именами артистов.
        """
        artists = obj.artists.all()[:3]
        names = [a.name for a in artists]
        if obj.artists.count() > 3:
            names.append("...")
        return ", ".join(names) or "-"

    get_artists_display.short_description = "Артисты"

    def get_fieldsets(self, request, obj=None):
        """Возвращает fieldsets в зависимости от операции.

        Args:
            request: HTTP запрос.
            obj: Экземпляр Record или None для новой записи.

        Returns:
            Кортеж fieldsets.
        """
        if obj:
            return self.fieldsets

        # add-форма: выбираем набор полей по источнику
        source = (request.POST.get("source") or request.GET.get("source") or "discogs").lower()
        if source == "redeye":
            return add_fieldsets_redeye
        return add_fieldsets_discogs

    def get_inline_instances(self, request, obj=None):
        """Возвращает inline-формы.

        Треки показываются только для существующих записей.

        Args:
            request: HTTP запрос.
            obj: Экземпляр Record или None.

        Returns:
            Список inline-форм.
        """
        if obj and obj.pk:
            return super().get_inline_instances(request, obj)
        return []

    def save_model(self, request, obj, form, change):
        """Сохранение модели с обработкой импорта из Discogs.

        При создании новой записи проверяет наличие дубликатов
        и выводит соответствующие сообщения.

        Args:
            request: HTTP запрос.
            obj: Сохраняемый объект.
            form: Форма с данными.
            change: True если редактирование, False если создание.
        """
        # Проверяем дубликат при импорте
        if hasattr(form, "duplicate_record"):
            duplicate = form.duplicate_record

            messages.info(
                request,
                format_html(
                    'Запись "{}" (Discogs ID: {}) уже существует в базе данных.',
                    duplicate.title,
                    duplicate.discogs_id,
                ),
            )

            # Устанавливаем флаг для перенаправления
            self._redirect_to_existing = duplicate
            return

        # Стандартное сохранение
        super().save_model(request, obj, form, change)

        # Сообщения об импорте для новых записей
        if not change:
            # добавлено: учитываем выбранный источник, чтобы сообщения были релевантны
            source = getattr(form, "cleaned_data", {}).get("source") or request.POST.get("source")

            if obj.discogs_id:
                # старое поведение оставляем — это для импорта из Discogs
                messages.success(
                    request,
                    f'Запись "{obj.title}" успешно импортирована из Discogs (ID: {obj.discogs_id})',
                )
            else:
                # добавлено: если выбрали Redeye — даём нейтральное инфо-сообщение,
                # т.к. у записей с Redeye не будет discogs_id.
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

    def response_add(self, request, obj, post_url_continue=None):
        """Обработка ответа после создания записи.

        Перенаправляет на существующую запись при обнаружении дубликата
        или на страницу редактирования при успешном импорте.

        Args:
            request: HTTP запрос.
            obj: Созданный объект.
            post_url_continue: URL для продолжения.

        Returns:
            HTTP ответ.
        """
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
            redirect_url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
            return redirect(redirect_url)

        # добавлено: для Redeye discogs_id не заполняется; всё равно ведём на редактирование,
        # чтобы пользователь увидел, что подтянулось, и мог сохранить/дополнить.
        source = request.POST.get("source")
        if source == "redeye":
            redirect_url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
            return redirect(redirect_url)

        return super().response_add(request, obj, post_url_continue)

    def update_from_discogs(self, request, queryset):
        """Массовое обновление записей из Discogs.

        Args:
            request: HTTP запрос.
            queryset: QuerySet выбранных записей.
        """
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
            except Exception as e:
                logger.error(f"Failed to update record {record.pk}: {str(e)}")
                self.message_user(
                    request,
                    f'Ошибка при обновлении записи "{record}": {str(e)}',
                    level=messages.ERROR,
                )
                errors += 1

        self.message_user(
            request,
            f"Обновлено записей: {updated}, ошибок: {errors}",
            level=messages.INFO,
        )

    update_from_discogs.short_description = "Обновить из Discogs"

    def get_readonly_fields(self, request, obj=None):
        """Возвращает поля только для чтения.

        Args:
            request: HTTP запрос.
            obj: Экземпляр Record или None.

        Returns:
            Кортеж полей только для чтения.
        """
        # is_expected вычисляется автоматически в save() -> делаем его read-only
        base = ("is_expected",)
        if obj:
            return base + ("discogs_id", "created", "modified")
        return base

    def has_delete_permission(self, request, obj=None):
        """Проверка прав на удаление.

        Запрещает удаление записей, которые есть в заказах.

        Args:
            request: HTTP запрос.
            obj: Экземпляр Record или None.

        Returns:
            True если удаление разрешено.
        """
        if obj and hasattr(obj, "order_items") and obj.order_items.exists():
            return False
        return super().has_delete_permission(request, obj)

    # Добавлено: делаем M2M не обязательными на форме редактирования (без миграций)
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        field = super().formfield_for_manytomany(db_field, request, **kwargs)
        # genres / styles / formats могут отсутствовать у Redeye
        field.required = False  # <-- ключевая строка
        return field

@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    search_fields = ("name",)

    def get_model_perms(self, request):
        """
        Возвращаем пустые права — модель не отображается
        в сайдбаре и индексе админки, но остаётся доступной
        для эндпоинтов автокомплита.
        """
        return {}