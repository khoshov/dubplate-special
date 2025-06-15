from records.models import Record
from rest_framework import viewsets
from rest_framework import generics

from .serializers import RecordSerializer, StyleSerializer
from .filters import RecordFilter
from records.models import Style


class RecordViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Record.objects.select_related(
        "label",
    ).prefetch_related(
        "artists",
        "tracks",
        "genres",
        "styles",
    ).distinct()
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


class StyleListView(generics.ListAPIView):
    queryset = Style.objects.all()
    serializer_class = StyleSerializer
    pagination_class = None
    name = "style-list"