from django.contrib import admin

from .models import Record


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin):
    list_display = ("title", "display_artists", "label", "release_date", "format")
    list_filter = ("format", "genres", "styles")
    search_fields = ("title", "catalog_number", "barcode")
    filter_horizontal = ("artists", "genres", "styles")

    def display_artists(self, obj):
        return ", ".join([artist.name for artist in obj.artists.all()])

    display_artists.short_description = "Artists"
