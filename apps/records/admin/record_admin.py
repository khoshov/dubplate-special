import logging
from typing import Optional

from django.contrib import admin
from django.http import HttpRequest

from records.forms import RecordForm
from records.models import Record, Artist
from records.services.audio.audio_service import AudioService
from records.services.image.image_service import ImageService
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.providers.redeye.redeye_service import RedeyeService
from records.services.record_service import RecordService
from .actions import update_from_discogs, update_from_redeye
from .inlines import TrackInline
from .mixins import RedeyeAudioRefreshMixin

logger = logging.getLogger(__name__)


@admin.register(Record)
class RecordAdmin(RedeyeAudioRefreshMixin, admin.ModelAdmin):
    """Административный интерфейс для записей.

    Интегрирован с Discogs для импорта и обновления данных.
    При создании новой записи автоматически импортирует данные
    из Discogs по штрих-коду или каталожному номеру.
    """

    form = RecordForm
    autocomplete_fields = ("artists",)
    inlines = [TrackInline]
    actions = [update_from_discogs, update_from_redeye]

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

    list_select_related = ("label",)

    def __init__(self, *args, **kwargs):
        """Инициализация админки."""
        super().__init__(*args, **kwargs)
        self.record_service = RecordService(
            discogs_service=DiscogsService(),
            redeye_service=RedeyeService(),
            image_service=ImageService(),
            audio_service=AudioService(),
        )

    def get_artists_display(self, obj: Record) -> str:
        """Показывает первых трёх артистов, если больше — добавляет '...'."""
        artists = obj.artists.all()[:3]
        names = [a.name for a in artists]
        if obj.artists.count() > 3:
            names.append("...")
        return ", ".join(names) or "-"

    get_artists_display.short_description = "Артисты"

    def get_fieldsets(self, request: HttpRequest, obj: Optional[Record] = None):
        """
        Возвращает набор полей для формы.

        На странице добавления (obj is None) показываем разные поля
        в зависимости от выбранного источника (Discogs / Redeye).
        На странице редактирования возвращаем стандартный набор.
        """
        if obj:
            logger.debug(
                "RecordAdmin.get_fieldsets(change): pk=%s → используется стандартный набор полей.",
                getattr(obj, "pk", None),
            )
            return self.fieldsets

        source = (
            request.POST.get("source") or request.GET.get("source") or "discogs"
        ).lower()
        if source == "redeye":
            logger.debug(
                "RecordAdmin.get_fieldsets(add): источник=Redeye → поля: (source, catalog_number)."
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
            return add_fieldsets_redeye

        logger.debug(
            "RecordAdmin.get_fieldsets(add): источник=Discogs → поля: (source, barcode, catalog_number)."
        )
        add_fieldsets_discogs = (
            (
                None,
                {
                    "fields": ("source", "barcode", "catalog_number"),
                    "description": (
                        "Выберите источник данных (Discogs или Redeye), затем введите штрих-код или каталожный номер. "
                        "Для Redeye используется только каталожный номер."
                    ),
                },
            ),
        )
        return add_fieldsets_discogs

    def get_inline_instances(self, request: HttpRequest, obj: Optional[Record] = None):
        """
        Показываем треки только для существующих записей.
        На странице добавления треки скрыты.
        """
        if obj and obj.pk:
            logger.debug(
                "RecordAdmin.get_inline_instances: pk=%s → отображение треков подключено.",
                obj.pk,
            )
            return super().get_inline_instances(request, obj)

        logger.debug(
            "RecordAdmin.get_inline_instances: add-view → отображение треков отключено."
        )
        return []

    def get_readonly_fields(self, request: HttpRequest, obj: Optional[Record] = None):
        """
        Базовые поля только для чтения дополняем служебными.
        """
        base = super().get_readonly_fields(request, obj)
        extra = ("discogs_id", "created", "modified")
        readonly = tuple(base) + extra
        logger.debug(
            "RecordAdmin.get_readonly_fields: base=%s, добавлено=%s → итог=%s",
            base,
            extra,
            readonly,
        )
        return readonly

    def has_delete_permission(
        self, request: HttpRequest, obj: Optional[Record] = None
    ) -> bool:
        """Запрещает удаление записей, которые есть в заказах."""
        if obj and hasattr(obj, "order_items") and obj.order_items.exists():
            return False
        return super().has_delete_permission(request, obj)

    def formfield_for_manytomany(self, db_field, request: HttpRequest, **kwargs):
        """
        Делает M2M-поля необязательными в админке (чтобы не мешали созданию записи).
        """
        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)
        if formfield:
            formfield.required = False
            logger.debug(
                "RecordAdmin.formfield_for_manytomany: поле '%s' помечено как необязательное к заполнению.",
                db_field.name,
            )
        return formfield

    class Media:
        css = {"all": ("records/admin/record_submit_row.css",)}


@admin.register(Artist)
class ArtistAdmin(admin.ModelAdmin):
    """
    Регистрация модели необходима для работы автодополнения исполнителей
    и для возможности ручного добавления при редактировании записи.
    """

    search_fields = ("name",)

    def get_model_perms(self, request: HttpRequest):
        """
        Скрывает модель в боковом меню и индексе админки,
        но остаётся доступной для функции автозаполнения.
        """
        return {}
