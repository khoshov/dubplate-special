from django.db import models
from django.utils.translation import gettext_lazy as _
from django_ckeditor_5.fields import CKEditor5Field
from django_extensions.db.models import TimeStampedModel
from records.managers import (
    ArtistManager,
    FormatManager,
    GenreManager,
    LabelManager,
    RecordManager,
    StyleManager,
)
from sorl.thumbnail import ImageField


class GenreChoices(models.TextChoices):
    NOT_SPECIFIED = "not specified", _("Not specified")
    HOUSE = "House", _("House")
    TECHNO = "Techno", _("Techno")
    ELECTRO = "Electro", _("Electro")
    BREAKS = "Breaks", _("Breaks")
    DRUM_N_BASS = "Drum & Bass", _("Drum & Bass")
    GARAGE = "Garage", _("Garage")
    AMBIENT = "Ambient", _("Ambient")
    EXPERIMENTAL = "Experimental", _("Experimental")
    DISCO = "Disco", _("Disco")
    HIPHOP = "Hip-Hop", _("Hip-Hop")
    REGGAE = "Reggae", _("Reggae")
    DUB = "Dub", _("Dub")
    TRANCE = "Trance", _("Trance")


class StyleChoices(models.TextChoices):
    NOT_SPECIFIED = "not specified", _("Not specified")
    DEEP_HOUSE = "Deep House", _("Deep House")
    MINIMAL = "Minimal", _("Minimal")
    ACID = "Acid", _("Acid")
    DETROIT = "Detroit", _("Detroit")
    PROGRESSIVE = "Progressive", _("Progressive")
    JUNGLE = "Jungle", _("Jungle")
    BREAKBEAT = "Breakbeat", _("Breakbeat")
    UK_GARAGE = "UK Garage", _("UK Garage")
    IDM = "IDM", _("IDM")
    DOWNTEMPO = "Downtempo", _("Downtempo")
    LOFI = "Lo-Fi", _("Lo-Fi")
    HARDCORE = "Hardcore", _("Hardcore")
    ITALO = "Italo", _("Italo")
    LEFTFIELD = "Leftfield", _("Leftfield")


class FormatChoices(models.TextChoices):
    NOT_SPECIFIED = "not specified", _("Not specified")
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

    M = "M"
    NM = "NM"
    VGP = "VG+"
    VG = "VG"
    GP = "G+"
    G = "G"
    F = "F"
    P = "P"

    CONDITION_CHOICES = (
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
    """Модель жанра."""

    name = models.CharField(max_length=100, unique=True, choices=GenreChoices.choices, verbose_name=_("Name"))

    objects = GenreManager()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Genre")
        verbose_name_plural = _("Genres")
        ordering = ("name",)


class Style(TimeStampedModel):
    """Модель стиля."""

    name = models.CharField(max_length=100, unique=True, choices=StyleChoices.choices, verbose_name=_("Name"))

    objects = StyleManager()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Style")
        verbose_name_plural = _("Styles")
        ordering = ("name",)


class Format(TimeStampedModel):
    """Модель формата."""

    name = models.CharField(max_length=100, unique=True, choices=FormatChoices.choices, verbose_name=_("Name"))

    objects = FormatManager()

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Format")
        verbose_name_plural = _("Formats")
        ordering = ("name",)


class Record(TimeStampedModel):
    """Модель записи (пластинки)."""

    title = models.CharField(max_length=255, verbose_name=_("Record title"))
    artists = models.ManyToManyField(
        Artist, related_name="records", verbose_name=_("Artists")
    )
    label = models.ForeignKey(
        Label,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="records",
        verbose_name=_("Label"),
    )
    release_year = models.PositiveIntegerField(
        null=True, blank=True, verbose_name=_("Release year")
    )
    genres = models.ManyToManyField(
        Genre, related_name="records", verbose_name=_("Genres")
    )
    formats = models.ManyToManyField(
        Format, related_name="records", verbose_name=_("Formats")
    )
    styles = models.ManyToManyField(
        Style, related_name="records", verbose_name=_("Styles")
    )
    discogs_id = models.IntegerField(
        unique=True, null=True, blank=True, verbose_name=_("Discogs ID")
    )
    cover_image = ImageField(
        upload_to="images/",
        null=True,
        blank=True,
        verbose_name=_("Record image"),
    )
    notes = CKEditor5Field(null=True, blank=True, verbose_name=_("Notes"))
    stock = models.PositiveIntegerField(
        default=1,
        verbose_name=_("Storage on hand"),
    )
    condition = models.CharField(
        max_length=3,
        choices=RecordConditions.CONDITION_CHOICES,
        default=RecordConditions.M,
        verbose_name=_("Condition"),
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


class Track(TimeStampedModel):
    """Модель трека."""

    record = models.ForeignKey(
        Record,
        on_delete=models.CASCADE,
        related_name="tracks",
        verbose_name=_("Record"),
    )
    position = models.CharField(max_length=10, verbose_name=_("Position"))
    title = models.CharField(max_length=255, verbose_name=_("Track title"))
    duration = models.CharField(
        max_length=10, null=True, blank=True, verbose_name=_("Duration")
    )
    youtube_url = models.URLField(
        max_length=512,
        null=True,
        blank=True,
        verbose_name=_("Track URL"),
        help_text=_("URL to track (YouTube)"),
    )

    def __str__(self):
        return f"{self.position}. {self.title}"

    class Meta:
        verbose_name = _("Track")
        verbose_name_plural = _("Tracks")
        ordering = ("record", "position")
