from django_countries.serializer_fields import CountryField
from django_countries.serializers import CountryFieldMixin
from records.models import Artist, Genre, Label, Record, Style, Track
from rest_framework import serializers

from django.utils.translation import gettext_lazy as _


class ArtistSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Artist
        fields = ["id", "url", "name", "discogs_id", "bio"]


class LabelSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Label
        fields = ["id", "url", "name", "discogs_id", "description"]


class GenreSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Genre
        fields = ["id", "url", "name"]


class StyleSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Style
        fields = ["id", "url", "name"]


class RecordSerializer(CountryFieldMixin, serializers.HyperlinkedModelSerializer):
    artists = ArtistSerializer(many=True, read_only=True)
    artist_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Artist.objects.all(),
        write_only=True,
        label=_("Artists"),
        source="artists",
    )
    label = LabelSerializer(read_only=True)
    label_id = serializers.PrimaryKeyRelatedField(
        queryset=Label.objects.all(),
        write_only=True,
        allow_null=True,
        required=False,
        label=_("Label"),
        source="label",
    )
    genres = GenreSerializer(many=True, read_only=True)
    genre_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Genre.objects.all(),
        write_only=True,
        label=_("Genres"),
        source="genres",
    )
    styles = StyleSerializer(many=True, read_only=True)
    style_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Style.objects.all(),
        write_only=True,
        label=_("Styles"),
        source="styles",
    )
    country = CountryField(name_only=True)

    class Meta:
        model = Record
        fields = [
            "id",
            "url",
            "title",
            "artists",
            "artist_ids",
            "label",
            "label_id",
            "release_date",
            "genres",
            "genre_ids",
            "styles",
            "style_ids",
            "discogs_id",
            "cover_image",
            "notes",
            "stock",
            "condition",
            "catalog_number",
            "barcode",
            "format",
            "country",
        ]
        read_only_fields = ["id", "url", "created", "modified"]

    def validate_artist_ids(self, value):
        if not value:
            raise serializers.ValidationError(_("At least one artist is required."))
        return value

    def validate_genre_ids(self, value):
        if not value:
            raise serializers.ValidationError(_("At least one genre is required."))
        return value

    def validate_style_ids(self, value):
        if not value:
            raise serializers.ValidationError(_("At least one style is required."))
        return value


class TrackSerializer(serializers.HyperlinkedModelSerializer):
    record = RecordSerializer(read_only=True)
    record_id = serializers.PrimaryKeyRelatedField(
        queryset=Record.objects.all(),
        write_only=True,
        label=_("Record"),
        source="record",
    )

    class Meta:
        model = Track
        fields = ["id", "url", "record", "record_id", "position", "title", "duration"]
        read_only_fields = ["id", "url", "created", "modified"]
