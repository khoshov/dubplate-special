from .models import Record, Genre, Artist, Label, Style, Track
from django.contrib import admin

# Register your models here.
admin.site.register(Genre)
admin.site.register(Artist)
admin.site.register(Label)
admin.site.register(Record)
admin.site.register(Style)
admin.site.register(Track)
