from records.models import Record
from rest_framework import viewsets
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.reverse import reverse

from .serializers import RecordSerializer


class RecordViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Record.objects.all()
    serializer_class = RecordSerializer


@api_view(["GET"])
def api_root(request):
    return Response(
        {
            "records": reverse("record-list", request=request),
        }
    )
