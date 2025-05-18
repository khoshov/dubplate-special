from records.models import Artist, Genre, Label, Record, Style, Track
from rest_framework import serializers


class ArtistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Artist
        fields = ["id", "name", "discogs_id", "bio"]


class LabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Label
        fields = ["id", "name", "discogs_id", "description"]


class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ["id", "name"]


class StyleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Style
        fields = ["id", "name"]


class TrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Track
        fields = ["id", "position", "title", "duration"]
        read_only_fields = ["id", "created", "modified"]


class RecordSerializer(serializers.HyperlinkedModelSerializer):
    artists = ArtistSerializer(many=True, read_only=True)
    label = LabelSerializer(read_only=True)
    genres = GenreSerializer(many=True, read_only=True)
    styles = StyleSerializer(many=True, read_only=True)
    tracks = TrackSerializer(many=True, read_only=True)
    condition = serializers.CharField(source="get_condition_display", read_only=True)
    format = serializers.CharField(source="get_format_display", read_only=True)

    class Meta:
        model = Record
        fields = [
            "id",
            "url",
            "title",
            "artists",
            "label",
            "release_date",
            "genres",
            "styles",
            "discogs_id",
            "cover_image",
            "notes",
            "stock",
            "condition",
            "catalog_number",
            "barcode",
            "format",
            "country",
            "tracks",
        ]
        read_only_fields = ["id", "url", "created", "modified"]
