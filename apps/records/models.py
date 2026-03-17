import calendar
import uuid
from datetime import date

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_ckeditor_5.fields import CKEditor5Field
from django_extensions.db.models import TimeStampedModel
from solo.models import SingletonModel
from sorl.thumbnail import ImageField

from apps.records.utils.storage_paths import PathByInstance as _PathByInstance
from .managers import (
    ArtistManager,
    FormatManager,
    GenreManager,
    LabelManager,
    RecordManager,
    StyleManager,
)


class PathByInstance(_PathByInstance):
    """
    Обёртка над utils.storage_paths.PathByInstance для совместимости
    с существующими миграциями (сохраняем dotted-path apps.records.models.PathByInstance).
    Новой логики здесь нет.
    """

    pass


class GenreChoices(models.TextChoices):
    NOT_SPECIFIED = "Not specified", _("Not specified")

    JUNGLE = "Jungle", _("Jungle")
    DRUM_AND_BASS = "Drum and Bass", _("Drum and Bass")
    HARDCORE_BREAKBEAT = "Hardcore Breakbeat", _("Hardcore Breakbeat")
    BREAKBEAT = "Breakbeat", _("Breakbeat")
    GARAGE = "Garage", _("Garage")
    ELECTRO = "Electro", _("Electro")
    DUBSTEP = "Dubstep", _("Dubstep")
    GRIME = "Grime", _("Grime")
    BASS = "Bass", _("Bass")
    FUNK = "Funk", _("Funk")
    DISCO = "Disco", _("Disco")
    HOUSE = "House", _("House")
    REGGAE = "Reggae", _("Reggae")
    DANCEHALL = "Dancehall", _("Dancehall")
    DUB = "Dub", _("Dub")
    DUB_TECHNO = "Dub Techno", _("Dub Techno")
    TECHNO = "Techno", _("Techno")
    TRANCE = "Trance", _("Trance")
    AMBIENT = "Ambient", _("Ambient")


class StyleChoices(models.TextChoices):
    NOT_SPECIFIED = "Not specified", _("Not specified")
    DEEP_HOUSE = "Bass Music", _("Bass Music")
    MINIMAL = "Drum n Bass", _("Drum n Bass")


class FormatChoices(models.TextChoices):
    NOT_SPECIFIED = "Not specified", _("Не указан")
    INCH_12 = '12" Vinyl', _('12" Vinyl')
    INCH_2X12 = '2x12" Vinyl', _('2x12" Vinyl')
    INCH_10 = '10" Vinyl', _('10" Vinyl')
    INCH_7 = '7" Vinyl', _('7" Vinyl')
    INCH_3X12 = '3x12" Vinyl', _('3x12" Vinyl')
    INCH_4X12 = '4x12" Vinyl', _('4x12" Vinyl')
    INCH_5X12 = '5x12" Vinyl', _('5x12" Vinyl')


class RecordConditions:
    """Константы состояния пластинок."""

    NEW = "SS"
    NOT_SPECIFIED = "NOT_SPECIFIED"
    M = "M"
    NM = "NM"
    VGP = "VG+"
    VG = "VG"
    GP = "G+"
    G = "G"
    F = "F"
    P = "P"

    CONDITION_CHOICES = (
        (NEW, "НОВАЯ"),
        (M, "Mint (M)"),
        (NM, "Near Mint (NM)"),
        (VGP, "Very Good Plus (VG+)"),
        (VG, "Very Good (VG)"),
        (GP, "Good Plus (G+)"),
        (G, "Good (G)"),
        (F, "Fair (F)"),
        (P, "Poor (P)"),
        (NOT_SPECIFIED, "Не указано"),
    )


class AvailableChoices(models.TextChoices):
    """Варианты наличия товара"""

    IN_STOCK = "IN_STOCK", _("В НАЛИЧИИ")
    PREORDER = "PREORDER", _("ПРЕДЗАКАЗ")


class Artist(TimeStampedModel):
    """Модель исполнителя."""

    name = models.CharField(max_length=255, verbose_name=_("Name"))
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    bio = CKEditor5Field(null=True, blank=True, verbose_name=_("Bio"))

    objects = ArtistManager()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Artist")
        verbose_name_plural = _("Artists")
        ordering = ("id",)


class Label(TimeStampedModel):
    """Модель лейбла."""

    name = models.CharField(max_length=255, verbose_name=_("Name"))
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    description = CKEditor5Field(null=True, blank=True, verbose_name=_("Description"))

    objects = LabelManager()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Label")
        verbose_name_plural = _("Labels")
        ordering = ("id",)


class Genre(TimeStampedModel):
    """Модель жанра (справочник)."""

    name = models.CharField(max_length=100, unique=True, verbose_name=_("Name"))

    objects = GenreManager()

    def __str__(self):
        if self.name == GenreChoices.NOT_SPECIFIED:
            return "Не указан"
        return self.name

    class Meta:
        verbose_name = _("Genre")
        verbose_name_plural = _("Genres")
        ordering = ("name",)


class Style(TimeStampedModel):
    """Модель стиля (справочник)."""

    name = models.CharField(max_length=100, unique=True, verbose_name=_("Name"))

    objects = StyleManager()

    def __str__(self):
        if self.name == StyleChoices.NOT_SPECIFIED:
            return "Не указан"
        return self.name

    class Meta:
        verbose_name = _("Style")
        verbose_name_plural = _("Styles")
        ordering = ("name",)


class Format(TimeStampedModel):
    """Модель формата."""

    name = models.CharField(max_length=100, unique=True, verbose_name=_("Name"))

    objects = FormatManager()

    def __str__(self):
        if self.name == FormatChoices.NOT_SPECIFIED:
            return "Не указан"
        return self.name

    class Meta:
        verbose_name = _("Format")
        verbose_name_plural = _("Formats")
        ordering = ("name",)


class Record(TimeStampedModel):
    """Модель записи (пластинки)."""

    @property
    def release_date_effective(self) -> str:
        """
        Строка для админки: итоговая дата релиза, которую используем для статуса.
        """
        d = self.get_release_date()
        return d.isoformat() if d else "—"

    def get_release_date(self) -> date | None:
        """
        Возвращает конкретную дату релиза, если она однозначно определена.
        Если задан только год — возвращаем последнюю дату года (31 декабря),
        если год+месяц — последнюю дату месяца.
        Это позволяет корректно сравнивать с сегодняшним днём.
        """
        if not self.release_year:
            return None

        year = int(self.release_year)

        if self.release_month:
            month = int(self.release_month)
            if self.release_day:
                day = int(self.release_day)
            else:
                day = calendar.monthrange(year, month)[1]
            return date(year, month, day)

        return date(year, 12, 31)

    def refresh_expected_flag(self) -> None:
        """
        Обновляет флаг is_expected: True, если релиз в будущем (предзаказ),
        False — если дата уже наступила или неизвестна.
        """
        d = self.get_release_date()
        today = timezone.localdate()
        self.is_expected = bool(d and d > today)

    def save(self, *args, **kwargs):
        # перед сохранением всегда пересчитываем флаг
        self.refresh_expected_flag()
        super().save(*args, **kwargs)

    title = models.CharField(max_length=255, verbose_name=_("Название"))
    artists = models.ManyToManyField(
        Artist, related_name="records", verbose_name=_("Исполнитель")
    )
    label = models.ForeignKey(
        Label,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="records",
        verbose_name=_("Label"),
    )

    release_year = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Год релиза"
    )
    release_month = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Месяц релиза"
    )
    release_day = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="День релиза"
    )

    is_expected = models.BooleanField(
        default=False, db_index=True, verbose_name=_("Ожидается")
    )

    genres = models.ManyToManyField(
        Genre, related_name="records", verbose_name=_("Жанры")
    )
    formats = models.ManyToManyField(
        Format, related_name="records", verbose_name=_("Форматы")
    )
    styles = models.ManyToManyField(
        Style, related_name="records", verbose_name=_("Стили")
    )
    active_structured_format_variant = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Активный вариант формата"),
    )

    cover_image = ImageField(
        upload_to=PathByInstance("cover_image"),
        null=True,
        blank=True,
        verbose_name=_("Обложка"),
    )

    stock = models.PositiveIntegerField(
        default=1,
        verbose_name=_("Storage on hand"),
    )

    availability_status = models.CharField(
        max_length=16,
        choices=AvailableChoices.choices,
        default=AvailableChoices.PREORDER,
        db_index=True,
        verbose_name=_("Наличие"),
    )

    condition = models.CharField(
        max_length=20,
        choices=RecordConditions.CONDITION_CHOICES,
        default=RecordConditions.NEW,
        verbose_name=_("Состояние"),
    )
    vk_published_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Опубликовано в VK"),
    )
    discogs_id = models.IntegerField(
        unique=True, null=True, blank=True, verbose_name=_("Discogs ID")
    )

    catalog_number = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        verbose_name=_("Каталожный номер"),
    )
    barcode = models.CharField(
        max_length=20, unique=True, null=True, blank=True, verbose_name=_("Barcode")
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=0,
        null=True,
        blank=True,
        help_text=_("Например: 5000"),
        verbose_name=_("Цена"),
    )

    notes = CKEditor5Field(null=True, blank=True, verbose_name=_("Заметки"))
    country = models.CharField(
        null=True, blank=True, verbose_name=_("Страна"), max_length=50
    )

    objects = RecordManager()

    def __str__(self):
        return self.title

    def selected_structured_format_variant(self) -> int | None:
        """Возвращает активный variant number или первый доступный."""
        if not self.pk:
            return None

        variants = list(
            self.structured_formats.order_by("variant_of_format", "id").values_list(
                "variant_of_format",
                flat=True,
            )
        )
        if not variants:
            return None

        active_variant = self.active_structured_format_variant
        if active_variant in variants:
            return active_variant
        return variants[0]

    class Meta:
        verbose_name = _("Record")
        verbose_name_plural = _("Records")
        ordering = ("title",)


class StructuredFormat(TimeStampedModel):
    """Структурированный вариант формата конкретного релиза."""

    record = models.ForeignKey(
        Record,
        on_delete=models.CASCADE,
        related_name="structured_formats",
        verbose_name=_("Запись"),
    )
    variant_of_format = models.PositiveSmallIntegerField(
        verbose_name=_("Вариант формата")
    )
    carrier = models.CharField(max_length=64, blank=True, verbose_name=_("Носитель"))
    quantity = models.PositiveSmallIntegerField(default=1, verbose_name=_("Количество"))
    format_name = models.CharField(max_length=64, blank=True, verbose_name=_("Формат"))
    details = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Дополнительно"),
    )

    def __str__(self) -> str:
        carrier = (self.carrier or "").strip()
        format_name = (self.format_name or "").strip()
        try:
            quantity = int(self.quantity)
        except (TypeError, ValueError):
            quantity = 1

        if carrier == "Vinyl" and format_name in {'7"', '10"', '12"'}:
            base = (
                f"{quantity}x{format_name} Vinyl"
                if quantity > 1
                else f"{format_name} Vinyl"
            )
            return base

        parts: list[str] = []
        if quantity > 1 and (carrier or format_name):
            parts.append(f"{quantity}x")
        if carrier:
            parts.append(carrier)
        if format_name:
            parts.append(format_name)
        return " ".join(parts) or f"Format variant #{self.variant_of_format}"

    class Meta:
        verbose_name = _("Структурированный формат релиза")
        verbose_name_plural = _("Структурированные форматы релиза")
        ordering = ("record", "variant_of_format", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["record", "variant_of_format"],
                name="uq_structuredformat_record_variant_of_format",
            )
        ]


class VKPublicationLog(TimeStampedModel):
    """Лог публикаций записей в VK."""

    class Mode(models.TextChoices):
        IMMEDIATE = "IMMEDIATE", _("Сразу")
        SCHEDULED = "SCHEDULED", _("Отложено")

    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", _("Успех")
        FAILED = "FAILED", _("Ошибка")

    record = models.ForeignKey(
        Record,
        on_delete=models.CASCADE,
        related_name="vk_publication_logs",
        verbose_name=_("Запись"),
    )
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        db_index=True,
        verbose_name=_("Режим публикации"),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        db_index=True,
        verbose_name=_("Статус публикации"),
    )
    planned_publish_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Запланировано на"),
    )
    effective_publish_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Фактическое время публикации"),
    )
    vk_post_id = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("ID поста VK"),
    )
    error_message = models.TextField(
        blank=True,
        default="",
        verbose_name=_("Текст ошибки"),
    )

    class Meta:
        verbose_name = _("Лог публикации VK")
        verbose_name_plural = _("Логи публикаций VK")
        ordering = ("-created",)

    def __str__(self) -> str:
        status = self.get_status_display()
        mode = self.get_mode_display()
        record_id = getattr(self, "record_id", None)
        return f"VK [{status}] ({mode}) record_id={record_id}"


class RecordSource(models.Model):
    """
    Нормализованные ссылки на внешние источники конкретной записи (Record).

    Зачем:
      - у одной записи может быть несколько источников (Redeye / Discogs );
      - храним тип ссылки (product_page / api / listing);
      - помечаем, можно ли с этой страницы забирать аудио-превью (can_fetch_audio);
      - фиксируем результат последней попытки сбора превью (audio_urls_count, last_audio_scrape_at).

    Примеры:
      RecordSource(record=R, provider='redeye', role='product_page', url='https://…', can_fetch_audio=True)
      RecordSource(record=R, provider='discogs', role='api', url='https://api.discogs.com/releases/…', can_fetch_audio=False)
    """

    class Provider(models.TextChoices):
        REDEYE = "redeye", "Redeye"
        DISCOGS = "discogs", "Discogs"
        JUNO = "juno", "Juno"
        # при необходимости добавим другие провайдеры

    class Role(models.TextChoices):
        PRODUCT_PAGE = "product_page", "Product page"  # страница карточки товара (UI)
        API = "api", "API"  # программный ресурс (например, Discogs API)
        LISTING = (
            "listing",
            "Listing",
        )  # страница списка/категории (обычно не нужна для mp3)

    record = models.ForeignKey(
        "Record",
        on_delete=models.CASCADE,
        related_name="sources",
        verbose_name=_("Record"),
    )
    provider = models.CharField(
        max_length=24,
        choices=Provider.choices,
        verbose_name=_("Provider"),
        help_text=_("Провайдер данных: redeye / discogs / juno и т.д."),
    )
    role = models.CharField(
        max_length=24,
        choices=Role.choices,
        default=Role.PRODUCT_PAGE,
        verbose_name=_("Role"),
        help_text=_("Роль ссылки: product_page / api / listing."),
    )
    url = models.URLField(
        verbose_name=_("Source URL"),
        help_text=_("Ссылка на внешний источник для этой записи."),
    )

    # Под mp3-задачу
    can_fetch_audio = models.BooleanField(
        default=False,
        verbose_name=_("Can fetch audio previews"),
        help_text=_("Можно ли пытаться собирать mp3-превью с этой страницы."),
    )
    last_audio_scrape_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last audio scrape at"),
        help_text=_("Когда последний раз пытались собрать mp3-ссылки с этой страницы."),
    )
    audio_urls_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Audio URLs found (last scrape)"),
        help_text=_("Сколько mp3-ссылок нашли в прошлую попытку."),
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Updated at"))

    class Meta:
        verbose_name = _("Record source")
        verbose_name_plural = _("Record sources")
        # На одну запись по провайдеру и роли — одна «главная» ссылка
        constraints = [
            models.UniqueConstraint(
                fields=["record", "provider", "role"],
                name="uq_recordsource_record_provider_role",
            )
        ]
        indexes = [
            models.Index(fields=["provider", "role"], name="idx_source_provider_role"),
            models.Index(fields=["can_fetch_audio"], name="idx_source_can_fetch_audio"),
        ]

    def __str__(self) -> str:  # type: ignore[override]
        return f"{self.get_provider_display()}:{self.get_role_display()} → {self.url}"


class AudioEnrichmentJob(TimeStampedModel):
    """Фоновая операция докачки аудио из YouTube."""

    class Source(models.TextChoices):
        DISCOGS_IMPORT = "discogs_import", _("Discogs import")
        MANUAL_LIST = "manual_list", _("Manual list action")
        MANUAL_RECORD = "manual_record", _("Manual record form")

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        COMPLETED_WITH_ERRORS = "completed_with_errors", _("Completed with errors")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        verbose_name=_("Source"),
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
        verbose_name=_("Status"),
    )
    requested_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audio_enrichment_jobs",
        verbose_name=_("Requested by"),
    )
    overwrite_existing = models.BooleanField(
        default=False,
        verbose_name=_("Overwrite existing"),
    )
    total_records = models.PositiveIntegerField(default=0, verbose_name=_("Records"))
    total_tracks = models.PositiveIntegerField(default=0, verbose_name=_("Tracks"))
    updated_count = models.PositiveIntegerField(default=0, verbose_name=_("Updated"))
    skipped_count = models.PositiveIntegerField(default=0, verbose_name=_("Skipped"))
    error_count = models.PositiveIntegerField(default=0, verbose_name=_("Errors"))
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Started at"),
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Finished at"),
    )

    class Meta:
        verbose_name = _("Audio enrichment job")
        verbose_name_plural = _("Audio enrichment jobs")
        ordering = ("-created",)

    def __str__(self) -> str:
        return f"AudioJob {self.id} [{self.status}]"


class AudioEnrichmentJobRecord(TimeStampedModel):
    """Состояние обработки одной записи в рамках job."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        COMPLETED_WITH_ERRORS = "completed_with_errors", _("Completed with errors")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")

    class Reason(models.TextChoices):
        NONE = "", _("None")
        ALREADY_RUNNING = "already_running", _("Already running")
        VALIDATION_ERROR = "validation_error", _("Validation error")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    job = models.ForeignKey(
        "AudioEnrichmentJob",
        on_delete=models.CASCADE,
        related_name="job_records",
        verbose_name=_("Job"),
    )
    record = models.ForeignKey(
        "Record",
        on_delete=models.CASCADE,
        related_name="audio_enrichment_job_records",
        verbose_name=_("Record"),
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
        verbose_name=_("Status"),
    )
    reason_code = models.CharField(
        max_length=64,
        choices=Reason.choices,
        default=Reason.NONE,
        blank=True,
        verbose_name=_("Reason code"),
    )
    updated_count = models.PositiveIntegerField(default=0, verbose_name=_("Updated"))
    skipped_count = models.PositiveIntegerField(default=0, verbose_name=_("Skipped"))
    error_count = models.PositiveIntegerField(default=0, verbose_name=_("Errors"))
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Started at"),
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Finished at"),
    )

    class Meta:
        verbose_name = _("Audio enrichment job record")
        verbose_name_plural = _("Audio enrichment job records")
        ordering = ("-created",)
        constraints = [
            models.UniqueConstraint(
                fields=["job", "record"],
                name="uq_audio_enrichment_job_record",
            ),
            models.UniqueConstraint(
                fields=["record"],
                condition=Q(status__in=["queued", "running"]),
                name="uq_audio_enrichment_active_record",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"AudioJobRecord job={self.job_id} record={self.record_id} [{self.status}]"
        )


class AudioEnrichmentTrackResult(TimeStampedModel):
    """Результат обработки одного трека в рамках job-record."""

    class Status(models.TextChoices):
        UPDATED = "updated", _("Updated")
        SKIPPED = "skipped", _("Skipped")
        FAILED = "failed", _("Failed")

    class Reason(models.TextChoices):
        NONE = "", _("None")
        MISSING_YOUTUBE_URL = "missing_youtube_url", _("Missing YouTube URL")
        INVALID_URL = "invalid_url", _("Invalid URL")
        ALREADY_PRESENT = "already_present", _("Already present")
        DOWNLOAD_ERROR = "download_error", _("Download error")
        RETRY_EXHAUSTED = "retry_exhausted", _("Retry exhausted")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
    )
    job_record = models.ForeignKey(
        "AudioEnrichmentJobRecord",
        on_delete=models.CASCADE,
        related_name="track_results",
        verbose_name=_("Job record"),
    )
    track = models.ForeignKey(
        "Track",
        on_delete=models.CASCADE,
        related_name="audio_enrichment_track_results",
        verbose_name=_("Track"),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        verbose_name=_("Status"),
    )
    reason_code = models.CharField(
        max_length=64,
        choices=Reason.choices,
        default=Reason.NONE,
        blank=True,
        verbose_name=_("Reason code"),
    )
    attempts = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(3)],
        verbose_name=_("Attempts"),
    )
    previous_audio_present = models.BooleanField(
        default=False,
        verbose_name=_("Previous audio present"),
    )
    final_audio_name = models.CharField(
        max_length=512,
        blank=True,
        default="",
        verbose_name=_("Final audio name"),
    )
    error_message = models.TextField(
        blank=True,
        default="",
        verbose_name=_("Error message"),
    )

    class Meta:
        verbose_name = _("Audio enrichment track result")
        verbose_name_plural = _("Audio enrichment track results")
        ordering = ("created",)
        constraints = [
            models.UniqueConstraint(
                fields=["job_record", "track"],
                name="uq_audio_enrichment_track_result",
            )
        ]

    def __str__(self) -> str:
        return (
            f"AudioTrackResult job_record={self.job_record_id} "
            f"track={self.track_id} [{self.status}]"
        )


class YouTubeSessionState(SingletonModel, TimeStampedModel):
    """Текущее состояние YouTube-сессии для фоновой загрузки аудио."""

    class Status(models.TextChoices):
        UNKNOWN = "unknown", _("Unknown")
        HEALTHY = "healthy", _("Healthy")
        AUTH_REQUIRED = "auth_required", _("Authorization required")
        LOGIN_IN_PROGRESS = "login_in_progress", _("Login in progress")

    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.UNKNOWN,
        db_index=True,
        verbose_name=_("Status"),
    )
    status_message = models.TextField(
        blank=True,
        default="",
        verbose_name=_("Status message"),
    )
    last_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Last checked at"),
    )
    last_authenticated_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Last authenticated at"),
    )
    last_error_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Last error at"),
    )
    last_login_started_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Last login started at"),
    )
    last_login_finished_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Last login finished at"),
    )

    class Meta:
        verbose_name = _("YouTube session state")
        verbose_name_plural = _("YouTube session state")

    def __str__(self) -> str:
        return f"YouTube session [{self.status}]"


class Track(TimeStampedModel):
    """
    Трек издания.

    ДВА уровня представления позиции:
      1) position_index (int) — ГЛАВНАЯ сортировка и порядок треков в издании.
         Это последовательный номер 1..N, независимо от сторон (A/B/C/D).
         Им руководствуются админка, API и любая логика "след./пред. трек".
      2) position (str) — «позиция со стороны», если она есть на сайте/в источнике:
         'A1', 'B2', '1', 'A', '' (пусто). Нужна для совместимости с Discogs и отображения.

    Почему так:
      - строки вроде '1', '10', '11' сортируются лексикографически и ломают порядок;
      - Discogs возвращает поле position (например 'A1'), но "естественный" порядок удобнее хранить отдельно.
      - при отсутствии буквенных сторон мы сохраняем пустой `position`, но `position_index` остаётся обязательным.
    """

    record = models.ForeignKey(
        Record,
        on_delete=models.CASCADE,
        related_name="tracks",
        verbose_name=_("Record"),
    )
    # Человекочитаемая позиция со стороны (если есть у источника).
    # Примеры: 'A1', 'B2', '1', 'A', ''.
    position = models.CharField(
        max_length=10,
        blank=True,
        verbose_name=_("Position"),
        help_text=_(
            "Original position from the source (e.g., 'A1', 'B2'); may be empty."
        ),
    )

    # Главный числовой порядок (1..N), независимый от сторон.
    # Этот индекс выставляет импортёр (Redeye/Discogs) при создании треков.
    # Всегда используем его для сортировок.
    position_index = models.PositiveIntegerField(
        default=0,
        db_index=True,
        verbose_name=_("Order"),
        help_text=_(
            "Sequential order across the release (1..N), independent of sides."
        ),
    )

    title = models.CharField(max_length=255, verbose_name=_("Track title"))

    # Длительность сохраняем как строку (например, '05:58'), так как у разных источников форматы разнятся.
    duration = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        verbose_name=_("Duration"),
        help_text=_("Optional; e.g., '05:58'."),
    )

    # Ссылка на ролик/превью (если есть, обычно из Discogs/YouTube).
    youtube_url = models.URLField(
        max_length=512,
        null=True,
        blank=True,
        verbose_name=_("Track video URL"),
        help_text=_("Optional preview/video URL (e.g., YouTube)."),
    )

    # Локальный MP3-файл превью. Путь генерируется callable-классом PathByInstance,
    # который уже используется проектом для обложек и формирует путь:
    #   <app>/<model>/<field>/<id>/<slugified_title>.<ext>
    # Пример: records/track/audio_preview/123/my-track.mp3
    audio_preview = models.FileField(
        upload_to=PathByInstance("audio_preview"),  # type: ignore[arg-type]
        null=True,
        blank=True,
        verbose_name=_("mp3"),
        help_text=_("Local preview file stored in media (optional)."),
    )

    def __str__(self) -> str:
        """
        Удобный вид для админки/логов.
        Показываем position, если она есть; главный порядок остаётся в position_index.
        """
        prefix = f"{self.position}. " if self.position else ""
        return f"{prefix}{self.title}"

    class Meta:
        verbose_name = _("Track")
        verbose_name_plural = _("Tracks")
        # сначала сортируем по порядковому номеру; при равенстве — по текстовой позиции.
        ordering = ("record", "position_index", "position")
