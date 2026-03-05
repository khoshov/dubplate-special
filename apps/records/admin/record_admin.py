import logging
from datetime import timedelta, timezone as dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import admin
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html
from vk_api.exceptions import ApiError

from core.middleware import ADMIN_TOO_MANY_FIELDS_SESSION_KEY
from records.constants import SOURCE_DISCOGS
from records.forms import RecordForm
from records.models import Artist, Format, Genre, Record, Style, VKPublicationLog
from records.services.audio.audio_service import AudioService
from records.services.image.image_service import ImageService
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.providers.redeye.redeye_service import RedeyeService
from records.services.record_service import RecordService
from records.services.social.publication_log import register_vk_publication_event
from records.services.social.vk_service import VKService
from records.services.social.schedule import build_even_schedule
from .actions import update_from_discogs, update_from_redeye, post_to_vk, schedule_to_vk
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
    actions = [update_from_discogs, update_from_redeye, post_to_vk, schedule_to_vk]
    vk_service: VKService
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
        ("Детали", {"fields": ("genres", "styles", "formats", "condition")}),
        ("Склад и цены", {"fields": ("stock", "availability_status", "price")}),
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
        "availability_status",
        "vk_published_at_display",
    )
    list_filter = (
        "condition",
        "genres",
        "styles",
        "created",
        "modified",
        "is_expected",
        "availability_status",
    )
    search_fields = (
        "title",
        "barcode",
        "catalog_number",
        "discogs_id",
        "artists__name",
        "label__name",
        "availability_status",
    )
    ordering = (
        "is_expected",
        "release_year",
        "release_month",
        "release_day",
        "-created",
    )
    date_hierarchy = "created"
    list_per_page = 20

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
        self.vk_service = VKService.from_settings()

    def changelist_view(
        self, request: HttpRequest, extra_context: Optional[dict] = None
    ) -> HttpResponse:
        """Отображает отложенное сообщение об ошибке массового POST из middleware."""
        pending_error = request.session.pop(ADMIN_TOO_MANY_FIELDS_SESSION_KEY, None)
        if pending_error:
            messages.error(request, pending_error)
        return super().changelist_view(request, extra_context=extra_context)

    def get_artists_display(self, obj: Record) -> str:
        """Показывает первых трёх артистов, если больше — добавляет '...'."""
        artists = obj.artists.all()[:3]
        names = [a.name for a in artists]
        if obj.artists.count() > 3:
            names.append("...")
        return ", ".join(names) or "-"

    get_artists_display.short_description = "Артисты"

    @admin.display(description="Опубликовано", ordering="vk_published_at")
    def vk_published_at_display(self, obj: Record) -> str:
        published_at = getattr(obj, "vk_published_at", None)
        if published_at is None:
            return "-"

        published_at_utc = published_at.astimezone(dt_timezone.utc)
        iso = published_at_utc.isoformat()
        fallback_text = published_at_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
        return format_html(
            '<time class="js-vk-published-at" data-utc="{}">{}</time>',
            iso,
            fallback_text,
        )

    def get_urls(self):
        base_urls = super().get_urls()
        custom = [
            path(
                "vk-schedule/",
                self.admin_site.admin_view(self.vk_schedule_view),
                name="records_record_vk_schedule",
            ),
        ]
        return custom + base_urls

    def vk_schedule_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_change_permission(request):
            messages.error(request, "Недостаточно прав для публикации записей.")
            return HttpResponseRedirect(reverse("admin:records_record_changelist"))

        selected_ids = self._extract_ids(request)
        if not selected_ids:
            messages.warning(request, "Не выбраны записи для публикации.")
            return HttpResponseRedirect(reverse("admin:records_record_changelist"))

        queryset = Record.objects.filter(pk__in=selected_ids)
        ordering = self.get_ordering(request) or ("pk",)
        queryset = queryset.order_by(*ordering)
        total = queryset.count()
        if total == 0:
            messages.warning(request, "Не выбраны записи для публикации.")
            return HttpResponseRedirect(reverse("admin:records_record_changelist"))

        tz_label = timezone.get_current_timezone_name()
        tz_value = tz_label
        tz_fallback = False

        if request.method == "POST":
            tz_name = (request.POST.get("timezone") or "").strip()
            user_tz = self._get_timezone_from_name(tz_name)
            tz_fallback = not tz_name
            previous_tz = timezone.get_current_timezone()
            timezone.activate(user_tz)
            tz_label = timezone.get_current_timezone_name()
            tz_value = tz_label
            try:
                publish_at_raw = request.POST.get("publish_at", "")
                publish_from_raw = request.POST.get("publish_from", "")
                publish_to_raw = request.POST.get("publish_to", "")
                publish_at = self._parse_datetime_local(publish_at_raw, user_tz)
                publish_from = self._parse_datetime_local(publish_from_raw, user_tz)
                publish_to = self._parse_datetime_local(publish_to_raw, user_tz)

                if total == 1:
                    if publish_at is None:
                        messages.error(
                            request, "Укажите корректную дату и время публикации."
                        )
                    else:
                        publish_from = publish_at
                        publish_to = publish_at
                else:
                    if publish_from is None or publish_to is None:
                        messages.error(
                            request,
                            "Заполните корректные даты начала и окончания публикации.",
                        )
                    elif publish_to < publish_from:
                        messages.error(
                            request,
                            "Дата окончания должна быть не раньше даты начала.",
                        )

                if publish_from and publish_to:
                    vk_service = getattr(self, "vk_service", None)
                    if vk_service is None:
                        messages.error(
                            request,
                            "Сервис VK не сконфигурирован. Обратитесь к администратору.",
                        )
                        return HttpResponseRedirect(
                            reverse("admin:records_record_changelist")
                        )

                    if total == 1:
                        times = [publish_from]
                        step = None
                    else:
                        times = build_even_schedule(publish_from, publish_to, total)
                        step = (publish_to - publish_from) / (total - 1)

                    delta = self._get_retry_delta(step)
                    ok = fail = 0
                    failed: list[str] = []

                    for record, publish_at in zip(queryset, times, strict=False):
                        try:
                            success, final_at, shifted = self._post_with_retry(
                                vk_service=vk_service,
                                record=record,
                                publish_at=publish_at,
                                delta=delta,
                                max_retries=10,
                            )
                            if not success:
                                raise ApiError(
                                    None,
                                    "wall.post",
                                    {},
                                    {},
                                    {
                                        "error_code": 214,
                                        "error_msg": (
                                            "Access to adding post denied: "
                                            "a post is already scheduled for this time."
                                        ),
                                    },
                                )
                            ok += 1
                            if shifted:
                                messages.info(
                                    request,
                                    self._format_shift_message(
                                        record=record,
                                        original_at=publish_at,
                                        new_at=final_at,
                                    ),
                                )
                            register_vk_publication_event(
                                record=record,
                                mode=VKPublicationLog.Mode.SCHEDULED,
                                status=VKPublicationLog.Status.SUCCESS,
                                planned_publish_at=publish_at,
                                effective_publish_at=final_at,
                            )
                        except Exception as exc:
                            fail += 1
                            failed.append(f"#{record.pk} «{record}»: {exc!s}")
                            register_vk_publication_event(
                                record=record,
                                mode=VKPublicationLog.Mode.SCHEDULED,
                                status=VKPublicationLog.Status.FAILED,
                                planned_publish_at=publish_at,
                                error_message=str(exc),
                            )

                    if ok:
                        messages.success(
                            request,
                            f"Запланировано публикаций: {ok} из {total}.",
                        )
                    if fail:
                        messages.error(request, f"Ошибки при планировании: {fail}.")
                        messages.error(request, "Ошибки:\n• " + "\n• ".join(failed))

                    return HttpResponseRedirect(
                        reverse("admin:records_record_changelist")
                    )
            finally:
                timezone.activate(previous_tz)

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "records": list(queryset),
            "selected_ids": [str(pk) for pk in selected_ids],
            "show_single": total == 1,
            "publish_at_value": request.POST.get("publish_at", ""),
            "publish_from_value": request.POST.get("publish_from", ""),
            "publish_to_value": request.POST.get("publish_to", ""),
            "timezone_label": tz_label,
            "timezone_value": tz_value,
            "timezone_fallback": tz_fallback,
        }
        return TemplateResponse(
            request, "admin/records/record/vk_schedule.html", context
        )

    @staticmethod
    def _get_retry_delta(step):
        if step is None:
            return timedelta(minutes=5)
        if step.total_seconds() <= 0:
            return timedelta(minutes=5)
        return step / 2

    @staticmethod
    def _format_shift_message(*, record: Record, original_at, new_at) -> str:
        def _fmt(value):
            if timezone.is_aware(value):
                value = timezone.localtime(value)
            return value.strftime("%Y-%m-%d %H:%M")

        return (
            f"Запись #{record.pk} «{record}» была запланирована на {_fmt(original_at)}, "
            f"но это время занято во ВК; время смещено на {_fmt(new_at)}."
        )

    @staticmethod
    def _post_with_retry(
        *,
        vk_service: VKService,
        record: Record,
        publish_at,
        delta,
        max_retries: int,
    ) -> tuple[bool, object, bool]:
        attempts = 0
        current_at = publish_at
        shifted = False

        while True:
            try:
                vk_service.post_record_with_audio(record=record, publish_at=current_at)
                return True, current_at, shifted
            except ApiError as exc:
                if exc.code != 214:
                    raise
                attempts += 1
                shifted = True
                if attempts > max_retries:
                    return False, current_at, shifted
                current_at = current_at + delta

    @staticmethod
    def _parse_datetime_local(value: str, tz):
        dt = parse_datetime(value)
        if dt is None:
            return None
        if tz is None:
            tz = timezone.get_current_timezone()
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, tz)
        else:
            dt = dt.astimezone(tz)
        return dt.astimezone(dt_timezone.utc)

    @staticmethod
    def _get_timezone_from_name(name: str):
        if not name:
            return timezone.get_current_timezone()
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            logger.warning(
                "VK schedule: неизвестная timezone '%s', использую текущую.",
                name,
            )
            return timezone.get_current_timezone()

    @staticmethod
    def _extract_ids(request: HttpRequest) -> list[int]:
        if request.method == "POST":
            raw_ids = request.POST.getlist("ids")
            if not raw_ids:
                raw = request.POST.get("ids", "")
                raw_ids = raw.split(",") if raw else []
        else:
            raw = request.GET.get("ids", "")
            raw_ids = raw.split(",") if raw else []

        ids: list[int] = []
        for raw_id in raw_ids:
            raw_id = raw_id.strip()
            if not raw_id:
                continue
            if raw_id.isdigit():
                ids.append(int(raw_id))
        return ids

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
            request.POST.get("source") or request.GET.get("source") or SOURCE_DISCOGS
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

    def save_model(self, request, obj, form, change) -> None:
        """
        Сохраняет запись в два этапа, чтобы upload_to мог опираться на pk и не требовались переносы в сигнале:
          1) если объект новый — сохраняем без файла (появляется pk);
          2) если в форме загружена новая обложка — присваиваем её и делаем update только поля cover_image.
        """
        new_cover_file = form.files.get("cover_image")

        if not obj.pk:
            super().save_model(request, obj, form, change=False)

        if new_cover_file is not None:
            obj.cover_image = new_cover_file
            obj.save(update_fields=["cover_image"])
            return  # всё сохранено

        super().save_model(request, obj, form, change=True)

    def response_add(self, request, obj, post_url_continue=None):
        if getattr(obj, "_duplicate_record", False):
            messages.warning(
                request,
                f"Запись с каталожным номером {obj.catalog_number!s} уже существует. "
                "Создание пропущено, открыта существующая запись.",
            )
        return super().response_add(request, obj, post_url_continue=post_url_continue)

    class Media:
        css = {"all": ("records/admin/record_submit_row.css",)}
        js = ("records/js/admin_local_datetime.js",)


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


@admin.register(Format)
class FormatAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)

    def get_model_perms(self, request: HttpRequest):
        """
        Скрывает модель в боковом меню и индексе админки,
        но остаётся доступной для функции автозаполнения.
        """
        return {}


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("display_name",)
    search_fields = ("name",)
    ordering = ("name",)

    @admin.display(description="Название", ordering="name")
    def display_name(self, obj: Genre) -> str:
        return str(obj)

    def get_model_perms(self, request: HttpRequest):
        """
        Скрывает модель в боковом меню и индексе админки,
        но остаётся доступной для функции автозаполнения.
        """
        return {}


@admin.register(Style)
class StyleAdmin(admin.ModelAdmin):
    list_display = ("display_name",)
    search_fields = ("name",)
    ordering = ("name",)

    @admin.display(description="Название", ordering="name")
    def display_name(self, obj: Style) -> str:
        return str(obj)

    def get_model_perms(self, request: HttpRequest):
        """
        Скрывает модель в боковом меню и индексе админки,
        но остаётся доступной для функции автозаполнения.
        """
        return {}


@admin.register(VKPublicationLog)
class VKPublicationLogAdmin(admin.ModelAdmin):
    list_display = (
        "record",
        "mode",
        "status",
        "planned_publish_at",
        "effective_publish_at",
        "vk_post_id",
        "created",
    )
    list_filter = ("mode", "status", "created")
    search_fields = (
        "record__title",
        "record__catalog_number",
        "error_message",
    )
    autocomplete_fields = ("record",)
    ordering = ("-created",)
    readonly_fields = (
        "record",
        "mode",
        "status",
        "planned_publish_at",
        "effective_publish_at",
        "vk_post_id",
        "error_message",
        "created",
        "modified",
    )
