from django.contrib import admin

from .models import Artist, Genre, Label, Record, Style, Track

# Register your models here.
admin.site.register(Genre)
admin.site.register(Artist)
admin.site.register(Label)
admin.site.register(Record)
admin.site.register(Style)
admin.site.register(Track)
