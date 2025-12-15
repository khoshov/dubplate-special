import calendar
from datetime import date

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_ckeditor_5.fields import CKEditor5Field
from django_extensions.db.models import TimeStampedModel
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
    UK_GARAGE = "UK Garage", _("UK Garage")
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
    NOT_SPECIFIED = "Not specified", _("Not specified")
    INCH_7 = '7"', _('7"')
    INCH_10 = '10"', _('10"')
    INCH_12 = '12"', _('12"')
    EP = "EP", _("EP")
    SINGLE = "Single", _("Single")
    LP = "LP", _("LP")
    LP2 = "2LP", _("2LP")
    LP3 = "3LP", _("3LP")
    LP4 = "4LP", _("4LP")
    BOX_SET = "Box Set", _("Box Set")
    PICTURE_DISC = "Picture Disc", _("Picture Disc")


class RecordConditions:
    """Константы состояния пластинок."""

    NEW = "НОВАЯ"
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
    )


class Artist(TimeStampedModel):
    """Модель артиста."""

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
        return self.name

    class Meta:
        verbose_name = _("Format")
        verbose_name_plural = _("Formats")
        ordering = ("name",)


class Record(TimeStampedModel):
    """Модель записи (пластинки)."""

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
            # если указан день — используем его, иначе берём последний день месяца
            if self.release_day:
                day = int(self.release_day)
            else:
                day = calendar.monthrange(year, month)[1]
            return date(year, month, day)

        # только год — считаем «до конца года»
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

    release_year = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Год релиза"
    )
    release_month = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Месяц релиза"
    )
    release_day = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="День релиза"
    )

    # ФЛАГ для быстрого фильтра и сортировок
    is_expected = models.BooleanField(
        default=False, db_index=True, verbose_name="Предзаказ (ожидается)"
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
    discogs_id = models.IntegerField(
        unique=True, null=True, blank=True, verbose_name=_("Discogs ID")
    )
    cover_image = ImageField(
        upload_to=PathByInstance("cover_image"),
        null=True,
        blank=True,
        verbose_name=_("Обложка"),
    )
    notes = CKEditor5Field(null=True, blank=True, verbose_name=_("Notes"))
    stock = models.PositiveIntegerField(
        default=1,
        verbose_name=_("Storage on hand"),
    )
    condition = models.CharField(
        max_length=10,
        choices=RecordConditions.CONDITION_CHOICES,
        default=RecordConditions.M,
        verbose_name=_("Состояние"),
    )
    catalog_number = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        verbose_name=_("Catalog number"),
    )
    barcode = models.CharField(
        max_length=20, unique=True, null=True, blank=True, verbose_name=_("Barcode")
    )
    country = models.CharField(
        null=True, blank=True, verbose_name=_("Country"), max_length=50
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Manual price in Russian Rubles"),
        verbose_name=_("Price"),
    )

    objects = RecordManager()

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = _("Record")
        verbose_name_plural = _("Records")
        ordering = ("title",)


# --- добавлено: вспомогательная модель ссылок на внешние источники записи ---
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


# --- конец добавления ---


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
