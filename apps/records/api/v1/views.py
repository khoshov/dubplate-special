from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import filters, viewsets, permissions, status
from rest_framework.exceptions import ValidationError, NotFound
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
    permission_classes = [permissions.AllowAny]

    def get_object(self):
        try:
            obj = get_object_or_404(queryset, pk=self.kwargs["pk"])
        except (ValidationError, ValueError):
            raise NotFound("Заказ не найден")

        return obj

    def create(self, request, *args, **kwargs):
        # Форматируем данные для соответствия сериализатору
        request_data = {
            "name": request.data.get("name"),
            "phone": request.data.get("phone"),
            "address": request.data.get("address"),
            "items": request.data.get("items", [])
        }

        serializer = self.get_serializer(data=request_data)
        serializer.is_valid(raise_exception=True)

        try:
            order = serializer.save()
            return Response(
                serializer.data,
                status=status.HTTP_201_CREATED
            )
        except ValidationError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    def validate_items(self, items):
        # Проверяем наличие товара на складе
        for item_data in items:
            record = item_data['record']
            quantity = item_data['quantity']
            if record.stock < quantity:
                raise ValidationError(
                    f"Недостаточно пластинок '{record.title}' в наличии. "
                    f"Доступно: {record.stock}, заказано: {quantity}"
                )

            # Уменьшаем количество на складе
            record.stock -= quantity
            record.save()