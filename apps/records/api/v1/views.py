from records.models import Record
from rest_framework import viewsets, filters
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.reverse import reverse

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


@api_view(["GET"])
def api_root(request):
    return Response(
        {
            "records": reverse("record-list", request=request),
        }
    )
