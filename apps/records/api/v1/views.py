from rest_framework import exceptions, status, viewsets
from rest_framework.response import Response

from django.shortcuts import get_object_or_404

from records.models import Order, Record, Style

from .filters import RecordFilter
from .serializers import OrderSerializer, RecordSerializer, StyleSerializer


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


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    http_method_names = ["get", "post"]
    permission_classes = []

    def get_object(self):
        return get_object_or_404(self.queryset, pk=self.kwargs.get("pk"))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except exceptions.ValidationError as err:
            return Response({"error": str(err)}, status=status.HTTP_400_BAD_REQUEST)
