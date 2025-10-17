import logging
from typing import Optional
from django.urls import path
from django.contrib import admin, messages
from django.shortcuts import get_object_or_404, redirect

from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.urls import reverse

from .forms import RecordForm
from .models import Record, Track, Artist
from .services.discogs_service import DiscogsService
from .services.image_service import ImageService
from .services.providers.redeye.redeye_service import RedeyeService
from .services.record_service import RecordService

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
    list_filter = (
        "condition",
        "genres",
        "styles",
        "created",
        "modified",
        "is_expected",
    )
    search_fields = (
        "title",
        "barcode",
        "catalog_number",
        "discogs_id",
        "artists__name",
        "label__name",
    )
    ordering = (
        "is_expected",
        "release_year",
        "release_month",
        "release_day",
        "-created",
    )
    date_hierarchy = "created"

    # Оптимизация запросов
    list_select_related = ("label",)

    # Действия
    actions = ["update_from_discogs"]

    def __init__(self, *args, **kwargs):
        """Инициализация админки."""
        super().__init__(*args, **kwargs)
        self.record_service = RecordService(
            discogs_service=DiscogsService(),
            redeye_service=RedeyeService(),
            image_service=ImageService(),
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
        # Поля для создания новой записи (ДВА варианта: Discogs/Redeye)
        source = (
            request.POST.get("source") or request.GET.get("source") or "discogs"
        ).lower()
        if source == "redeye":
            add_fieldsets_redeye = (
                (
                    None,
                    {
                        "fields": ("source", "catalog_number"),
                        "description": "Импорт из Redeye использует только каталожный номер.",
                    },
                ),
            )

            return add_fieldsets_redeye
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
        return add_fieldsets_discogs

    # добавляем URL для кнопки "Обновить"
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/refresh/",
                self.admin_site.admin_view(self.process_refresh),
                name="records_record_refresh",
            ),
        ]
        return custom + urls

    @transaction.atomic
    def process_refresh(self, request: HttpRequest, object_id: str) -> HttpResponse:
        # Разрешаем только POST с формы submit_row
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        obj = get_object_or_404(self.model, pk=object_id)
        if not self.has_change_permission(request, obj):
            messages.error(request, "Недостаточно прав для обновления этой записи.")
            return redirect("admin:records_record_change", object_id=obj.pk)

        if not obj.catalog_number:
            messages.error(
                request, "Невозможно обновить: у записи не указан каталожный номер."
            )
            return redirect("admin:records_record_change", object_id=obj.pk)

        # Читаем чекбокс «Перекачать аудио» из submit_row
        force = str(request.POST.get("_refresh_force", "0")).strip().lower() in {
            "1",
            "true",
            "on",
            "yes",
        }

        try:
            # 1) обновляем данные релиза (обложку подтянет сам импорт)
            record, imported = self.record_service.import_from_redeye(  # type: ignore[attr-defined]
                catalog_number=obj.catalog_number,
                download_audio=False,
            )
            # 2) докачиваем/перекачиваем превью
            updated_audio = self.record_service._maybe_attach_redeye_previews(  # type: ignore[attr-defined]
                record,
                force=force,
            )

            parts = []
            parts.append(
                "данные по релизу обновлены"
                if imported
                else "данные по релизу проверены (актуальны)"
            )
            parts.append(
                f"mp3-превью {'перекачано' if force else 'добавлено'}: {updated_audio}"
                if updated_audio
                else (
                    "перекачка не потребовалась"
                    if force
                    else "новых mp3-превью не найдено"
                )
            )
            messages.success(request, ", ".join(parts) + ".")
        except Exception as e:
            messages.error(request, f"Не удалось выполнить обновление: {e}")

        # Остаёмся на карточке записи
        return redirect("admin:records_record_change", object_id=obj.pk)

    def get_inline_instances(self, request: HttpRequest, obj: Optional[Record] = None):
        """Показываем треки только для существующих записей."""
        if obj and obj.pk:
            return super().get_inline_instances(request, obj)
        return []

    # --- ИЗМЕНЕНО: поведение при дубликате каталожного номера ---
    # внутри class RecordAdmin(admin.ModelAdmin):

    def save_model(
        self, request: HttpRequest, obj: Record, form: RecordForm, change: bool
    ) -> None:
        """
        Сохранение модели с обработкой:
        - дубликата (обновляем существующую запись и подтягиваем превью),
        - докачки превью при создании/редактировании.
        """
        # --- кейс дубликата при создании ---
        duplicate = getattr(form, "duplicate_record", None)
        if duplicate is not None:
            try:
                # попытка докачать превью для уже существующей записи
                updated = self.record_service._maybe_attach_redeye_previews(
                    duplicate, force=False
                )  # type: ignore[attr-defined]
                if updated:
                    messages.success(
                        request,
                        f"Для существующей записи «{duplicate}» добавлены превью: {updated}.",
                    )
                else:
                    messages.info(
                        request,
                        f"Для существующей записи «{duplicate}» новые превью не найдены.",
                    )
            except Exception as e:
                logger.exception(
                    "Auto audio-fetch on duplicate failed for %s", duplicate.pk
                )
                messages.error(
                    request, f"Не удалось подтянуть превью для «{duplicate}»: {e}"
                )

            # настроим редирект в response_add
            self._redirect_to_existing = duplicate
            return

        # --- обычное сохранение ---
        super().save_model(request, obj, form, change)

        # --- докачка превью после сохранения ---
        try:
            # условие: есть треки без превью — имеет смысл пытаться
            need_audio = obj.tracks.filter(audio_preview__isnull=True).exists()
        except Exception:
            need_audio = False

        if need_audio:
            try:
                updated = self.record_service._maybe_attach_redeye_previews(
                    obj, force=False
                )  # type: ignore[attr-defined]
                if updated:
                    messages.success(
                        request, f"Добавлены mp3-превью треков: {updated}."
                    )
            except Exception as e:
                logger.exception("Post-save audio-fetch failed for %s", obj.pk)
                messages.warning(request, f"Не удалось подтянуть mp3-превью: {e}")

    # --- ИЗМЕНЕНО: поддержка редиректа на существующую запись после авто-обновления ---
    def response_add(
        self, request: HttpRequest, obj: Record, post_url_continue: Optional[str] = None
    ):
        """
        После нажатия «Save» в add-форме:
        - если обнаружен дубликат, уходим на страницу существующей записи (после авто-обновления);
        - иначе — прежнее поведение: на форму редактирования только что созданной записи.
        """
        # Перенаправление на уже существующую запись (кейс дубликата)
        if self._redirect_to_existing is not None:
            existing_obj = self._redirect_to_existing
            self._redirect_to_existing = None
            return redirect(
                reverse(
                    f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                    args=[existing_obj.pk],
                )
            )

        # Если импорт успешен — ведём на редактирование (как раньше)
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

        # Для Redeye discogs_id обычно нет — всё равно ведём на редактирование
        source = (request.POST.get("source") or "").lower()
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

    def has_delete_permission(
        self, request: HttpRequest, obj: Optional[Record] = None
    ) -> bool:
        """Запрещает удаление записей, которые есть в заказах."""
        if obj and hasattr(obj, "order_items") and obj.order_items.exists():
            return False
        return super().has_delete_permission(request, obj)

    # M2M опциональны (у Redeye иногда отсутствуют) — не требуем на форме
    def formfield_for_manytomany(self, db_field, request, **kwargs):
        field = super().formfield_for_manytomany(db_field, request, **kwargs)
        field.required = False
        return field

    class Media:
        css = {"all": ("records/admin/record_submit_row.css",)}


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
