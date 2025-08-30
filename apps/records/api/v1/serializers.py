from records.models import (
    Artist,
    Format,
    Genre,
    Label,
    Order,
    OrderItem,
    Record,
    Style,
    Track,
)
from rest_framework import serializers

from django.db import transaction


class ArtistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Artist
        fields = ["id", "name", "discogs_id", "bio"]


class LabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Label
        fields = ["id", "name", "discogs_id", "description"]


class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ["id", "name"]


class FormatSerializer(serializers.ModelSerializer):
    class Meta:
        model = Format
        fields = ["id", "name"]


class StyleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Style
        fields = ["id", "name"]


class TrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Track
        fields = ["id", "position", "title", "duration", "audio_file"]
        read_only_fields = ["id", "created", "modified"]


class RecordSerializer(serializers.HyperlinkedModelSerializer):
    artists = ArtistSerializer(many=True, read_only=True)
    label = LabelSerializer(read_only=True)
    genres = GenreSerializer(many=True, read_only=True)
    styles = StyleSerializer(many=True, read_only=True)
    tracks = TrackSerializer(many=True, read_only=True)
    condition = serializers.CharField(source="get_condition_display", read_only=True)
    format = FormatSerializer(many=True, read_only=True)

    class Meta:
        model = Record
        fields = [
            "id",
            "url",
            "title",
            "artists",
            "label",
            "release_year",
            "genres",
            "styles",
            "discogs_id",
            "cover_image",
            "notes",
            "stock",
            "condition",
            "catalog_number",
            "barcode",
            "format",
            "country",
            "tracks",
            "price",
        ]
        read_only_fields = ["id", "url", "created", "modified"]


class OrderItemSerializer(serializers.HyperlinkedModelSerializer):
    id = serializers.PrimaryKeyRelatedField(
        queryset=Record.objects.all(), write_only=True, source="record"
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
        fields = ["id", "name", "phone", "address", "total_price", "items", "created"]
        read_only_fields = ["id", "total_price", "created"]

    def create(self, validated_data):
        if not (items_data := validated_data.pop("items")):
            raise serializers.ValidationError(
                {
                    "items": [
                        "Невозможно создать заказ без пластинок. Добавьте хотя бы одну позицию."
                    ]
                }
            )

        try:
            with transaction.atomic():
                order = Order.objects.create(**validated_data)
                total_price = 0

                for item_data in items_data:
                    record = item_data["record"]
                    quantity = item_data["quantity"]

                    # Проверяем наличие на складе
                    if record.stock < quantity:
                        raise serializers.ValidationError(
                            {
                                "stock": [
                                    f"Недостаточно '{record.title}' в наличии: "
                                    f"доступно {record.stock}, заказано {quantity}"
                                ]
                            }
                        )

                    # Уменьшаем остаток и сохраняем
                    record.stock -= quantity
                    record.save()

                    # Создаем позицию заказа с текущей ценой
                    item = OrderItem.objects.create(
                        order=order,
                        record=record,
                        price=record.price,
                        quantity=quantity,
                    )
                    total_price += item.get_cost()

                order.total_price = total_price
                order.save()
                return order

        except serializers.ValidationError:
            order.delete()
