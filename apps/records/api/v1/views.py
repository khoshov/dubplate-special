from records.models import Record
from rest_framework import viewsets

from .serializers import RecordSerializer


class RecordViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Record.objects.all()
    serializer_class = RecordSerializer
