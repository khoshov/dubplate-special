from django.db import models
from django.utils.translation import gettext_lazy as _

from django_ckeditor_5.fields import CKEditor5Field
from django_countries.fields import CountryField
from sorl.thumbnail import ImageField


class RecordConditions:
    M = 'M'
    NM = 'NM'
    VGP = 'VG+'
    VG = 'VG'
    GP = 'G+'
    G = 'G'
    F = 'F'
    P = 'P'
    CONDITION_CHOICES = (
        (M, 'Mint (M)'),
        (NM, 'Near Mint (NM)'),
        (VGP, 'Very Good Plus (VG+)'),
        (VG, 'Very Good (VG)'),
        (GP, 'Good Plus (G+)'),
        (G, 'Good (G)'),
        (F, 'Fair (F)'),
        (P, 'Poor (P)'),
    )


class RecordFormats:
    LP = 'LP'
    LP2 = '2LP'
    LP3 = '3LP'
    EP = 'EP'
    SEVEN = '7"'
    TEN = '10"'
    TWELVE = '12"'
    BOX = 'BOX'
    PIC = 'PIC'
    SHAPED = 'SHAPED'
    FLEXI = 'FLEXI'
    ACETATE = 'ACETATE'
    TEST = 'TEST'
    OTHER = 'OTHER'
    FORMAT_CHOICES = (
        (LP, 'LP (12" Long Play)'),
        (LP2, '2LP (Double Album)'),
        (LP3, '3LP (Triple Album)'),
        (EP, 'EP (12" Extended Play)'),
        (SEVEN, '7" (Single)'),
        (TEN, '10" (Single/EP)'),
        (TWELVE, '12" Single'),
        (BOX, 'Box Set'),
        (PIC, 'Picture Disc'),
        (SHAPED, 'Shaped Disc'),
        (FLEXI, 'Flexi Disc'),
        (ACETATE, 'Acetate'),
        (TEST, 'Test Pressing'),
        (OTHER, 'Other Format'),
    )


class Artist(models.Model):
    name = models.CharField(max_length=255, verbose_name=_("Name"))
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    bio = CKEditor5Field(null=True, blank=True, verbose_name=_("Bio"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Artist")
        verbose_name_plural = _("Artists")


class Label(models.Model):
    name = models.CharField(max_length=255, verbose_name=_("Name"))
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    description = CKEditor5Field(null=True, blank=True,
                                 verbose_name=_("Description"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Label")
        verbose_name_plural = _("Labels")


class Genre(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("Name"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Genre")
        verbose_name_plural = _("Genres")


class Style(models.Model):
    name = models.CharField(max_length=100, verbose_name=_("Name"))

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = _("Style")
        verbose_name_plural = _("Styles")


class Record(models.Model):
    title = models.CharField(max_length=255, verbose_name=_("Record title"))
    artists = models.ManyToManyField(Artist, related_name='records',
                                     verbose_name=_("Artist"))
    label = models.ForeignKey(Label, on_delete=models.SET_NULL, null=True,
                              blank=True, related_name='records',
                              verbose_name=_("Label"))
    release_date = models.DateField(null=True, blank=True,
                                    verbose_name=_("Release date"))
    genres = models.ManyToManyField(Genre, related_name='records',
                                    verbose_name=_("Genre"))
    styles = models.ManyToManyField(Style, related_name='records',
                                    verbose_name=_("Style"))
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    cover_image = ImageField(
        upload_to='images/',
        null=True,
        blank=True,
        verbose_name=_("Record image"),
    )
    notes = CKEditor5Field(null=True, blank=True, verbose_name=_("Notes"))
    stock = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Storage on hand"),
    )
    condition = models.CharField(
        max_length=3,
        choices=RecordConditions.CONDITION_CHOICES,
        default=RecordConditions.NM,
        verbose_name=_("Condition"),
    )
    catalog_number = models.CharField(max_length=50, unique=True,
                                      null=True, blank=True,
                                      verbose_name=_("Catalog number"))
    barcode = models.CharField(max_length=20, unique=True,
                               null=True, blank=True,
                               verbose_name=_("Barcode"))
    format = models.CharField(
        max_length=7,
        choices=RecordFormats.FORMAT_CHOICES,
        default=RecordFormats.OTHER,
        verbose_name=_("Format"),
    )
    country = CountryField(null=True, blank=True, verbose_name=_("Country"))

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = _("Record")
        verbose_name_plural = _("Records")


class Track(models.Model):
    record = models.ForeignKey(Record, on_delete=models.CASCADE,
                               related_name='tracks')
    position = models.CharField(max_length=10)
    title = models.CharField(max_length=255, verbose_name=_("Track title"))
    duration = models.CharField(max_length=10, null=True,
                                blank=True, verbose_name=_("Duration"))

    def __str__(self):
        return f"{self.position}. {self.title}"

    class Meta:
        verbose_name = _("Track")
        verbose_name_plural = _("Tracks")
