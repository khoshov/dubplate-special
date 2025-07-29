from django_ckeditor_5.fields import CKEditor5Field
from django_extensions.db.models import TimeStampedModel
from sorl.thumbnail import ImageField

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class RecordConditions:
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
    name = models.CharField(max_length=255, verbose_name=_("Name"))
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    bio = CKEditor5Field(null=True, blank=True, verbose_name=_("Bio"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Artist")
        verbose_name_plural = _("Artists")
        ordering = ("id",)


class Label(TimeStampedModel):
    name = models.CharField(max_length=255, verbose_name=_("Name"))
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    description = CKEditor5Field(null=True, blank=True, verbose_name=_("Description"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Label")
        verbose_name_plural = _("Labels")
        ordering = ("id",)


class Genre(TimeStampedModel):
    name = models.CharField(max_length=100, verbose_name=_("Name"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Genre")
        verbose_name_plural = _("Genres")
        ordering = ("name",)


class Format(TimeStampedModel):
    name = models.CharField(max_length=100, verbose_name=_("Name"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Format")
        verbose_name_plural = _("Formats")
        ordering = ("name",)


class Style(TimeStampedModel):
    name = models.CharField(max_length=100, verbose_name=_("Name"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Style")
        verbose_name_plural = _("Styles")
        ordering = ("name",)


class Record(TimeStampedModel):
    title = models.CharField(max_length=255, verbose_name=_("Record title"))
    artists = models.ManyToManyField(
        Artist, related_name="records", verbose_name=_("Artist")
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
        Genre, related_name="records", verbose_name=_("Genre")
    )
    formats = models.ManyToManyField(
        Format, related_name="records", verbose_name=_("Format")
    )
    styles = models.ManyToManyField(
        Style, related_name="records", verbose_name=_("Style")
    )
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
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
        verbose_name=_("Catalog number"),
    )
    barcode = models.CharField(max_length=20, unique=True, verbose_name=_("Barcode"))
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

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = _("Record")
        verbose_name_plural = _("Records")
        ordering = ("title",)


class Track(TimeStampedModel):
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
        ordering = (
            "record",
            "position",
        )
