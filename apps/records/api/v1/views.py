from rest_framework import exceptions, status, viewsets
from rest_framework.response import Response

from django.shortcuts import get_object_or_404

from records.models import Record, Style

from .filters import RecordFilter
from .serializers import RecordSerializer, StyleSerializer


class RecordViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        Record.objects.select_related(
            "label",
        )
        .prefetch_related(
            "artists",
            "tracks",
            "genres",
            "styles",
        )
        .distinct()
    )
    serializer_class = RecordSerializer
    filterset_class = RecordFilter
    search_fields = [
        "title",
        "artists__name",
        "label__name",
        "release_year",
        "genres__name",
        "styles__name",
        "discogs_id",
        "condition",
        "catalog_number",
        "barcode",
        "country",
    ]


class StyleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Style.objects.all()
    serializer_class = StyleSerializer
    pagination_class = None
