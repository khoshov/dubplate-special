from records.models import Record
from rest_framework import viewsets, filters

from .serializers import RecordSerializer


class RecordViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Record.objects.all()
    serializer_class = RecordSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = [
        "title",
        "artists__name",
        "label__name",
        "release_date",
        "genres__name",
        "styles__name",
        "discogs_id",
        "condition",
        "catalog_number",
        "barcode",
        "format",
        "country",
    ]
