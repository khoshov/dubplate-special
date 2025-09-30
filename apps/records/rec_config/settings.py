from django.db import models

# константы для records.models.py

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
