from django.db import models


class Artist(models.Model):
    name = models.CharField(max_length=255)
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    bio = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class Label(models.Model):
    name = models.CharField(max_length=255)
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class Genre(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Style(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Record(models.Model):
    title = models.CharField(max_length=255)
    artists = models.ManyToManyField(Artist, related_name='records')
    label = models.ForeignKey(Label, on_delete=models.SET_NULL, null=True, blank=True, related_name='records')
    release_date = models.DateField(null=True, blank=True)
    genres = models.ManyToManyField(Genre, related_name='records')
    styles = models.ManyToManyField(Style, related_name='records')
    discogs_id = models.IntegerField(unique=True, null=True, blank=True)
    cover_image = models.URLField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    # VSNCD001
    # Страна: Netherlands
    # Штрих - код(EAN): 5060156656525
    # Формат: CD, Album

    def __str__(self):
        return self.title


class Track(models.Model):
    record = models.ForeignKey(Record, on_delete=models.CASCADE, related_name='tracks')
    position = models.CharField(max_length=10)
    title = models.CharField(max_length=255)
    duration = models.CharField(max_length=10, blank=True, null=True)

    def __str__(self):
        return f"{self.position}. {self.title}"
