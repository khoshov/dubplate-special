import logging
from datetime import timedelta, timezone as dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html
from vk_api.exceptions import ApiError
from config.logging import log_event

from core.middleware import ADMIN_TOO_MANY_FIELDS_SESSION_KEY
from records.constants import SOURCE_DISCOGS, SOURCE_REDEYE
from records.forms import RecordForm
from records.models import (
    Artist,
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    AudioEnrichmentTrackResult,
    Format,
    Genre,
    Record,
    Style,
    Track,
    VKPublicationLog,
)
from records.services.audio.audio_service import AudioService
from records.services.audio.providers.youtube_audio_enrichment import (
    YouTubeAudioEnrichmentProvider,
)
from records.services.image.image_service import ImageService
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.providers.redeye.redeye_service import RedeyeService
from records.services.record_assembly import (
    ensure_active_structured_format_variant,
    ensure_legacy_formats,
)
from records.services.record_service import RecordService
from records.services.social.publication_log import register_vk_publication_event
from records.services.social.schedule import build_even_schedule
from records.services.social.vk_service import VKService
from .actions import (
    find_audio_on_youtube,
    post_to_vk,
    schedule_to_vk,
    update_audio_from_youtube,
    update_from_discogs,
    update_from_redeye,
)
from .inlines import StructuredFormatInline, TrackInline
from .mixins import RedeyeAudioRefreshMixin, YouTubeAudioRefreshMixin

logger = logging.getLogger(__name__)
_RECORD_ADMIN_COMPONENT = "админка релизов"
_SERVICE_ADMIN_MODELS = {
    "audioenrichmentjob",
    "audioenrichmentjobrecord",
    "audioenrichmenttrackresult",
    "vkpublicationlog",
}


def _log_record_admin_event(
    level: int,
    event: str,
    message: str,
    **context,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_RECORD_ADMIN_COMPONENT,
        event=event,
        **context,
    )


def _group_service_models_in_admin(app_list: list[dict]) -> list[dict]:
    records_app_url: str | None = None
    service_models: list[dict] = []
    filtered_apps: list[dict] = []

    for app in app_list:
        if app.get("app_label") != "records":
            filtered_apps.append(app)
            continue

        records_app_url = app.get("app_url")
        kept_models: list[dict] = []
        for model_info in app.get("models", []):
            object_name = str(model_info.get("object_name") or "").lower()
            if object_name in _SERVICE_ADMIN_MODELS:
                service_models.append(model_info)
            else:
                kept_models.append(model_info)
        if kept_models:
            app["models"] = kept_models
            filtered_apps.append(app)

    if service_models:
        filtered_apps.append(
            {
                "name": "Служебные",
                "app_label": "service",
                "app_url": records_app_url or "#",
                "has_module_perms": True,
                "models": service_models,
            }
        )
    return filtered_apps


_original_get_app_list = admin.site.get_app_list


def _get_app_list_with_service(request: HttpRequest) -> list[dict]:
    return _group_service_models_in_admin(_original_get_app_list(request))


admin.site.get_app_list = _get_app_list_with_service


@admin.register(Record)
class RecordAdmin(YouTubeAudioRefreshMixin, RedeyeAudioRefreshMixin, admin.ModelAdmin):
    """Административный интерфейс для записей.

    Интегрирован с Discogs для импорта и обновления данных.
    При создании новой записи автоматически импортирует данные
    из Discogs по штрих-коду или каталожному номеру.
    """

    form = RecordForm
    autocomplete_fields = ("artists",)
    inlines = [StructuredFormatInline, TrackInline]
    actions = [
        update_from_discogs,
        update_audio_from_youtube,
        find_audio_on_youtube,
        update_from_redeye,
        post_to_vk,
        schedule_to_vk,
    ]
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
        (
            "Склад и цены",
            {"fields": ("stock", "availability_status", "price", "condition")},
        ),
        (
            "Дополнительно",
            {
                "fields": ("cover_image", "notes"),
                "classes": ("collapse",),
            },
        ),
        ("Детали", {"fields": ("genres", "styles", "formats")}),
    )

    list_display = (
        "title_display",
        "artists_display",
        "label_display",
        "catalog_number_display",
        "barcode_display",
        "discogs_id_display",
        "created_display",
        "availability_status_display",
        "is_expected_display",
        "vk_published_at_display",
        "record_id_display",
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

    def add_view(
        self,
        request: HttpRequest,
        form_url: str = "",
        extra_context: Optional[dict] = None,
    ) -> HttpResponse:
        """Показывает всплывающее сообщение при некорректном URL Redeye в add-форме."""
        try:
            response = super().add_view(
                request=request, form_url=form_url, extra_context=extra_context
            )
        except ValidationError as exc:
            error_messages = self._extract_validation_error_messages(exc)
            for error_message in error_messages:
                self.message_user(request, error_message, level=messages.ERROR)

            source = (
                (
                    request.POST.get("source")
                    or request.GET.get("source")
                    or SOURCE_REDEYE
                )
                .strip()
                .lower()
            )
            if source not in {SOURCE_REDEYE, SOURCE_DISCOGS}:
                source = SOURCE_REDEYE

            _log_record_admin_event(
                logging.INFO,
                "add_view_validation_error",
                "При создании записи перехвачена ошибка валидации формы.",
                source=source,
                errors=", ".join(error_messages),
            )
            return HttpResponseRedirect(
                f"{reverse('admin:records_record_add')}?source={source}"
            )

        if request.method != "POST" or not isinstance(response, TemplateResponse):
            return response

        admin_form = (response.context_data or {}).get("adminform")
        form = getattr(admin_form, "form", None)
        if form is None:
            return response

        source_value = (form.data.get("source") or "").strip().lower()
        source_url_value = (form.data.get("source_url") or "").strip()
        source_url_errors = [str(err) for err in form.errors.get("source_url", [])]

        if (
            source_value == SOURCE_REDEYE
            and source_url_value
            and RecordForm.REDEYE_URL_NOT_FOUND_ERROR in source_url_errors
        ):
            self.message_user(
                request,
                RecordForm.REDEYE_URL_NOT_FOUND_ERROR,
                level=messages.ERROR,
            )

        return response

    @staticmethod
    def _extract_validation_error_messages(exc: ValidationError) -> list[str]:
        """Преобразует ValidationError в плоский список сообщений для админки."""
        if hasattr(exc, "message_dict"):
            messages_list: list[str] = []
            for field_errors in exc.message_dict.values():
                for field_error in field_errors:
                    messages_list.append(str(field_error))
            if messages_list:
                return messages_list
        if hasattr(exc, "messages"):
            return [str(msg) for msg in exc.messages]
        return [str(exc)]

    @admin.display(description="АРТИСТЫ")
    def artists_display(self, obj: Record) -> str:
        """Показывает первых трёх артистов, если больше — добавляет '...'."""
        artists = obj.artists.all()[:3]
        names = [a.name for a in artists]
        if obj.artists.count() > 3:
            names.append("...")
        return ", ".join(names) or "-"

    @admin.display(description="НАЗВАНИЕ", ordering="title")
    def title_display(self, obj: Record) -> str:
        return obj.title

    @admin.display(description="LABEL", ordering="label__name")
    def label_display(self, obj: Record) -> str:
        return str(obj.label) if obj.label else "—"

    @admin.display(description="КАТАЛОЖНЫЙ НОМЕР", ordering="catalog_number")
    def catalog_number_display(self, obj: Record) -> str:
        return obj.catalog_number or "—"

    @admin.display(description="BARCODE", ordering="barcode")
    def barcode_display(self, obj: Record) -> str:
        return obj.barcode or "—"

    @admin.display(description="DISCOGS ID", ordering="discogs_id")
    def discogs_id_display(self, obj: Record) -> str:
        return str(obj.discogs_id) if obj.discogs_id else "—"

    @admin.display(description="СОЗДАНО", ordering="created")
    def created_display(self, obj: Record) -> str:
        return timezone.localtime(obj.created).strftime("%Y-%m-%d %H:%M")

    @admin.display(description="НАЛИЧИЕ", ordering="availability_status")
    def availability_status_display(self, obj: Record) -> str:
        return obj.get_availability_status_display() or obj.availability_status

    @admin.display(description="ОЖИДАЕТСЯ", boolean=True, ordering="is_expected")
    def is_expected_display(self, obj: Record) -> bool:
        return bool(obj.is_expected)

    @admin.display(description="RECORD ID", ordering="pk")
    def record_id_display(self, obj: Record) -> int:
        return int(obj.pk)

    @admin.display(description="ПОСЛЕДНЯЯ ПУБЛИКАЦИЯ В ВК", ordering="vk_published_at")
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
                "<path:object_id>/tracks/<path:track_id>/delete-mp3/",
                self.admin_site.admin_view(self.delete_track_mp3_view),
                name="records_record_track_delete_mp3",
            ),
            path(
                "<path:object_id>/tracks/<path:track_id>/enqueue-mp3/",
                self.admin_site.admin_view(self.enqueue_track_mp3_view),
                name="records_record_track_enqueue_mp3",
            ),
            path(
                "vk-schedule/",
                self.admin_site.admin_view(self.vk_schedule_view),
                name="records_record_vk_schedule",
            ),
        ]
        return custom + base_urls

    def delete_track_mp3_view(
        self, request: HttpRequest, object_id: str, track_id: str
    ) -> JsonResponse:
        """Удаляет mp3 трека сразу и очищает поле в БД."""
        if request.method != "POST":
            return JsonResponse(
                {"ok": False, "error": "Разрешён только POST-запрос."},
                status=405,
            )

        record = get_object_or_404(self.model, pk=object_id)
        if not self.has_change_permission(request, record):
            return JsonResponse(
                {"ok": False, "error": "Недостаточно прав для удаления mp3."},
                status=403,
            )

        track = get_object_or_404(Track, pk=track_id, record=record)
        audio_name = str(getattr(track.audio_preview, "name", "") or "").strip()
        if not audio_name:
            return JsonResponse(
                {"ok": True, "deleted": False, "message": "Файл уже отсутствует."}
            )

        try:
            track.audio_preview.delete(save=False)
            track.audio_preview = None
            track.save(update_fields=["audio_preview", "modified"])
        except Exception as exc:
            _log_record_admin_event(
                logging.ERROR,
                "track_audio_delete_failed",
                "Не удалось удалить mp3 трека из админки.",
                record_id=record.pk,
                track_id=track.pk,
                old_audio=audio_name,
                error=str(exc),
            )
            logger.exception("Детали ошибки удаления mp3 трека из админки.")
            return JsonResponse(
                {"ok": False, "error": "Не удалось удалить mp3-файл."},
                status=500,
            )

        return JsonResponse({"ok": True, "deleted": True})

    def enqueue_track_mp3_view(
        self, request: HttpRequest, object_id: str, track_id: str
    ) -> JsonResponse:
        """Ставит один трек в очередь на докачку mp3 из YouTube."""
        if request.method != "POST":
            return JsonResponse(
                {"ok": False, "error": "Разрешён только POST-запрос."},
                status=405,
            )

        record = get_object_or_404(self.model, pk=object_id)
        if not self.has_change_permission(request, record):
            return JsonResponse(
                {"ok": False, "error": "Недостаточно прав для загрузки mp3."},
                status=403,
            )

        track = get_object_or_404(Track, pk=track_id, record=record)
        youtube_url = str(track.youtube_url or "").strip()
        if not youtube_url:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "У трека отсутствует ссылка на YouTube или Bandcamp.",
                },
                status=400,
            )
        if not YouTubeAudioEnrichmentProvider.is_valid_youtube_url(youtube_url):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Ссылка на YouTube или Bandcamp не прошла валидацию.",
                },
                status=400,
            )

        audio_name = str(getattr(track.audio_preview, "name", "") or "").strip()
        if audio_name:
            return JsonResponse(
                {"ok": False, "error": "mp3 уже прикреплён к этому треку."},
                status=400,
            )

        try:
            job = self.record_service.enqueue_track_youtube_audio_enrichment(
                track=track,
                requested_by_user_id=getattr(request.user, "id", None),
            )
        except Exception as exc:  # noqa: BLE001
            _log_record_admin_event(
                logging.ERROR,
                "track_mp3_enqueue_failed",
                "Не удалось поставить трек в очередь на докачку mp3.",
                record_id=record.pk,
                track_id=track.pk,
                error=str(exc),
            )
            return JsonResponse(
                {"ok": False, "error": "Не удалось поставить трек в очередь."},
                status=500,
            )

        _log_record_admin_event(
            logging.INFO,
            "track_mp3_enqueue",
            (
                f"Трек «{track.title}» из релиза «{record}» "
                "поставлен в очередь на докачку MP3."
            ),
            record_id=record.pk,
            track_id=track.pk,
            job_id=str(job.id),
        )
        _log_record_admin_event(
            logging.DEBUG,
            "track_mp3_enqueue",
            "Детали постановки трека в очередь.",
            job_id=str(job.id),
            record_id=record.pk,
            track_id=track.pk,
        )
        return JsonResponse({"ok": True, "job_id": str(job.id)})

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
            _log_record_admin_event(
                logging.WARNING,
                "vk_schedule_timezone_unknown",
                "Указанная timezone не распознана, используется текущая timezone проекта.",
                timezone_name=name,
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
            _log_record_admin_event(
                logging.DEBUG,
                "fieldsets_change",
                "Для change-view используется стандартный набор fieldsets.",
                record_id=getattr(obj, "pk", None),
            )
            return self.fieldsets

        source = (
            request.POST.get("source") or request.GET.get("source") or SOURCE_REDEYE
        ).lower()
        if source == SOURCE_DISCOGS:
            _log_record_admin_event(
                logging.DEBUG,
                "fieldsets_add_discogs",
                "Для add-view выбран источник Discogs: поле URL Redeye скрыто.",
            )
            add_import_fields = ("source", "discogs_id", "catalog_number", "barcode")
            description = "Импорт из Discogs поддерживает discogs_id, barecode или catalog_number."
        else:
            _log_record_admin_event(
                logging.DEBUG,
                "fieldsets_add_redeye",
                "Для add-view выбран источник Redeye: поле barcode скрыто.",
            )
            add_import_fields = ("source", "catalog_number", "source_url")
            description = (
                "Импорт из Redeye поддерживает catalog_number или URL карточки релиза."
            )

        return (
            (
                None,
                {
                    "fields": add_import_fields,
                    "description": description,
                },
            ),
        )

    def get_changeform_initial_data(self, request: HttpRequest) -> dict[str, str]:
        """Синхронизирует источник импорта в форме с query-параметром source."""
        initial = super().get_changeform_initial_data(request)
        source = (request.GET.get("source") or SOURCE_REDEYE).strip().lower()
        if source in {SOURCE_REDEYE, SOURCE_DISCOGS}:
            initial["source"] = source
        return initial

    def get_inline_instances(self, request: HttpRequest, obj: Optional[Record] = None):
        """
        Показываем треки только для существующих записей.
        На странице добавления треки скрыты.
        """
        if obj and obj.pk:
            _log_record_admin_event(
                logging.DEBUG,
                "inline_instances_change",
                "Для change-view включены inline треков.",
                record_id=obj.pk,
            )
            return super().get_inline_instances(request, obj)

        _log_record_admin_event(
            logging.DEBUG,
            "inline_instances_add",
            "Для add-view inline треков отключены.",
        )
        return []

    def get_readonly_fields(self, request: HttpRequest, obj: Optional[Record] = None):
        """
        Базовые поля только для чтения дополняем служебными.
        """
        base = tuple(super().get_readonly_fields(request, obj))
        extra = ("created", "modified")

        readonly_list = list(base)
        for field_name in extra:
            if field_name not in readonly_list:
                readonly_list.append(field_name)
        readonly = tuple(readonly_list)
        _log_record_admin_event(
            logging.DEBUG,
            "readonly_fields_resolved",
            "Сформирован итоговый список readonly fields.",
            base_fields=", ".join(base) or "—",
            added_fields=", ".join(extra) or "—",
            readonly_fields=", ".join(readonly) or "—",
        )
        return readonly

    def has_delete_permission(
        self, request: HttpRequest, obj: Optional[Record] = None
    ) -> bool:
        """Запрещает удаление записей, которые есть в заказах."""
        if obj and hasattr(obj, "order_items") and obj.order_items.exists():
            return False
        return super().has_delete_permission(request, obj)

    def get_deleted_objects(self, objs, request: HttpRequest):
        """Исключает внутренние enrichment-логи из permission-check страницы удаления."""
        deleted_objects, model_count, perms_needed, protected = (
            super().get_deleted_objects(objs, request)
        )
        ignored_verbose_names = {
            AudioEnrichmentJobRecord._meta.verbose_name,
            AudioEnrichmentTrackResult._meta.verbose_name,
            VKPublicationLog._meta.verbose_name,
        }
        filtered_perms_needed = {
            verbose_name
            for verbose_name in perms_needed
            if verbose_name not in ignored_verbose_names
        }
        return deleted_objects, model_count, filtered_perms_needed, protected

    def formfield_for_manytomany(self, db_field, request: HttpRequest, **kwargs):
        """
        Делает M2M-поля необязательными в админке (чтобы не мешали созданию записи).
        """
        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)
        if formfield:
            formfield.required = False
            _log_record_admin_event(
                logging.DEBUG,
                "many_to_many_optional",
                "M2M-поле помечено как необязательное в админке.",
                field_name=db_field.name,
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
        job_id = getattr(obj, "_discogs_enrichment_job_id", None)
        if job_id:
            report_url = reverse(
                "admin:records_audioenrichmentjob_change", args=[job_id]
            )
            messages.info(
                request,
                format_html(
                    'Поставлена в очередь задача YouTube enrichment: <a href="{}">Открыть job report</a>.',
                    report_url,
                ),
            )
        redeye_job_id = getattr(obj, "_redeye_enrichment_job_id", None)
        if redeye_job_id:
            report_url = reverse(
                "admin:records_audioenrichmentjob_change", args=[redeye_job_id]
            )
            messages.info(
                request,
                format_html(
                    'Поставлена в очередь задача Redeye enrichment: <a href="{}">Открыть job report</a>.',
                    report_url,
                ),
            )
        return super().response_add(request, obj, post_url_continue=post_url_continue)

    def save_related(self, request, form, formsets, change) -> None:
        """
        После сохранения M2M и inline-групп гарантирует дефолт legacy-формата.

        Structured rows больше не пересчитывают legacy-формат автоматически:
        legacy-блок живёт как независимый fallback-слой.
        """
        super().save_related(request, form, formsets, change)
        self._persist_requested_active_structured_format_variant(
            request=request,
            record=form.instance,
        )
        ensure_active_structured_format_variant(form.instance)
        ensure_legacy_formats(form.instance)

    @staticmethod
    def _persist_requested_active_structured_format_variant(
        *, request: HttpRequest, record: Record
    ) -> None:
        """Сохраняет выбранный в UI вариант structured format, если он валиден."""
        if not getattr(record, "pk", None):
            return

        raw_variant = str(
            request.POST.get("active_structured_format_variant") or ""
        ).strip()
        if not raw_variant.isdigit():
            return

        requested_variant = int(raw_variant)
        if requested_variant < 1:
            return

        if not record.structured_formats.filter(
            variant_of_format=requested_variant
        ).exists():
            return

        if record.active_structured_format_variant == requested_variant:
            return

        record.active_structured_format_variant = requested_variant
        record.save(update_fields=["active_structured_format_variant", "modified"])

    class Media:
        css = {"all": ("records/admin/record_submit_row.css",)}
        js = (
            "records/js/admin_local_datetime.js",
            "records/admin/record_submit_row.js",
        )


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
        "record_link",
        "mode",
        "status",
        "planned_publish_at",
        "effective_publish_at",
        "vk_post_id",
        "created",
    )
    list_display_links = ("record_link",)
    list_filter = ("mode", "status", "created")
    search_fields = (
        "record__title",
        "record__catalog_number",
        "error_message",
    )
    autocomplete_fields = ("record",)
    ordering = ("-created",)
    list_select_related = ("record",)
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

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Optional[VKPublicationLog] = None
    ) -> bool:
        return False

    @admin.display(description="Релиз", ordering="record__title")
    def record_link(self, obj: VKPublicationLog) -> str:
        record = getattr(obj, "record", None)
        if not record:
            return "—"
        url = reverse("admin:records_record_change", args=[record.pk])
        return format_html('<a href="{}">#{} — {}</a>', url, record.pk, record)


@admin.register(AudioEnrichmentJob)
class AudioEnrichmentJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "source",
        "status",
        "requested_by_user",
        "overwrite_existing",
        "total_records",
        "total_tracks",
        "updated_count",
        "skipped_count",
        "error_count",
        "created",
        "started_at",
        "finished_at",
    )
    list_filter = ("source", "status", "overwrite_existing", "created")
    search_fields = ("id", "requested_by_user__username")
    ordering = ("-created",)
    readonly_fields = (
        "id",
        "source",
        "status",
        "requested_by_user",
        "overwrite_existing",
        "total_records",
        "total_tracks",
        "updated_count",
        "skipped_count",
        "error_count",
        "started_at",
        "finished_at",
        "created",
        "modified",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Optional[AudioEnrichmentJob] = None
    ) -> bool:
        return False


@admin.register(AudioEnrichmentJobRecord)
class AudioEnrichmentJobRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job",
        "record_link",
        "status",
        "reason_code",
        "updated_count",
        "skipped_count",
        "error_count",
        "created",
        "started_at",
        "finished_at",
    )
    list_display_links = ("id",)
    list_filter = ("status", "reason_code", "created")
    search_fields = ("id", "job__id", "record__title", "record__catalog_number")
    ordering = ("-created",)
    autocomplete_fields = ("job", "record")
    list_select_related = ("job", "record")
    readonly_fields = (
        "id",
        "job",
        "record",
        "status",
        "reason_code",
        "updated_count",
        "skipped_count",
        "error_count",
        "started_at",
        "finished_at",
        "created",
        "modified",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Optional[AudioEnrichmentJobRecord] = None
    ) -> bool:
        return False

    @admin.display(description="Релиз", ordering="record__title")
    def record_link(self, obj: AudioEnrichmentJobRecord) -> str:
        record = getattr(obj, "record", None)
        if not record:
            return "—"
        url = reverse("admin:records_record_change", args=[record.pk])
        return format_html('<a href="{}">#{} — {}</a>', url, record.pk, record)


@admin.register(AudioEnrichmentTrackResult)
class AudioEnrichmentTrackResultAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job_record",
        "track",
        "record_link",
        "status",
        "reason_code",
        "attempts",
        "previous_audio_present",
        "created",
    )
    list_display_links = ("id",)
    list_filter = ("status", "reason_code", "attempts", "created")
    search_fields = ("id", "track__title", "job_record__id")
    ordering = ("-created",)
    autocomplete_fields = ("job_record",)
    raw_id_fields = ("track",)
    list_select_related = ("job_record", "track", "track__record")
    readonly_fields = (
        "id",
        "job_record",
        "track",
        "status",
        "reason_code",
        "attempts",
        "previous_audio_present",
        "final_audio_name",
        "error_message",
        "created",
        "modified",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Optional[AudioEnrichmentTrackResult] = None
    ) -> bool:
        return False

    @admin.display(description="Релиз", ordering="track__record__title")
    def record_link(self, obj: AudioEnrichmentTrackResult) -> str:
        track = getattr(obj, "track", None)
        record = getattr(track, "record", None) if track else None
        if not record:
            return "—"
        url = reverse("admin:records_record_change", args=[record.pk])
        return format_html('<a href="{}">#{} — {}</a>', url, record.pk, record)
