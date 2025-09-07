from orders.models import Order, OrderItem
from records.models import Record
from rest_framework import serializers

from django.db import transaction


class OrderItemSerializer(serializers.HyperlinkedModelSerializer):
    id = serializers.PrimaryKeyRelatedField(
        queryset=Record.objects.all(), source="record"
    )
    title = serializers.CharField(source="record.title", read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "title", "price", "quantity"]
        read_only_fields = ["title", "price", "created"]
        extra_kwargs = {"quantity": {"min_value": 1}}


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "name",
            "phone",
            "address",
            "status",
            "total_price",
            "items",
            "created",
        ]
        read_only_fields = ["id", "total_price", "created", "status"]

    def create(self, validated_data):
        if not (items_data := validated_data.pop("items")):
            raise serializers.ValidationError(
                {
                    "items": [
                        "Невозможно создать заказ без пластинок. Добавьте хотя бы одну позицию."
                    ]
                }
            )

        with transaction.atomic():
            # Присваиваем пользователя, если он аутентифицирован
            user = (
                self.context.get("request").user
                if self.context.get("request").user.is_authenticated
                else None
            )
            order = Order.objects.create(user=user, **validated_data)
            total_price = 0

            for item_data in items_data:
                record = item_data["record"]

                # Создаем позицию заказа с текущей ценой
                item = OrderItem.objects.create(
                    order=order,
                    record=record,
                    price=record.price,
                    quantity=item_data["quantity"],
                )
                total_price += item.get_cost()

            order.total_price = total_price
            order.save()
            return order
