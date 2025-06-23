from django.shortcuts import get_object_or_404
from rest_framework import filters, viewsets, status, exceptions
from rest_framework.response import Response

from records.models import Record, Order
from .serializers import RecordSerializer, OrderSerializer


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

class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    http_method_names = ['get', 'post']
    permission_classes = []

    def get_object(self):
        return get_object_or_404(self.queryset, pk=self.kwargs.get("pk"))

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            order = serializer.save()
            return Response(
                serializer.data,
                status=status.HTTP_201_CREATED
            )
        except exceptions.ValidationError as err:
            return Response(
                {"error": str(err)},
                status=status.HTTP_400_BAD_REQUEST
            )
