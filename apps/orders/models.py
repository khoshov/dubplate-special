from django_extensions.db.models import TimeStampedModel

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from records.models import Record


class OrderStatus:
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (PENDING, _("Ожидает подтверждения")),
        (CONFIRMED, _("Подтвержден")),
        (PROCESSING, _("В обработке")),
        (SHIPPED, _("Отправлен")),
        (DELIVERED, _("Доставлен")),
        (CANCELLED, _("Отменен")),
    ]


class Order(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
        null=True,
        blank=True,
        verbose_name=_("User"),
    )
    name = models.CharField(max_length=100, verbose_name=_("Full name"))
    phone = models.CharField(max_length=20, verbose_name=_("Phone"))
    address = models.CharField(max_length=100, verbose_name=_("Adress"))
    status = models.CharField(
        max_length=20,
        choices=OrderStatus.STATUS_CHOICES,
        default=OrderStatus.PENDING,
        verbose_name=_("Status"),
    )
    total_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name=_("Total price")
    )
    notes = models.TextField(blank=True, verbose_name=_("Order notes"))

    class Meta:
        verbose_name = _("Order")
        verbose_name_plural = _("Orders")
        ordering = ["-created"]

    def __str__(self):
        return f"{_('Order')} - {self.id}"

    def get_status_display_color(self):
        colors = {
            OrderStatus.PENDING: "warning",
            OrderStatus.CONFIRMED: "info",
            OrderStatus.PROCESSING: "primary",
            OrderStatus.SHIPPED: "secondary",
            OrderStatus.DELIVERED: "success",
            OrderStatus.CANCELLED: "danger",
        }
        return colors.get(self.status, "secondary")


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(
        Order, related_name="items", on_delete=models.CASCADE, verbose_name=_("Order")
    )
    record = models.ForeignKey(
        Record,
        related_name="order_items",
        on_delete=models.CASCADE,
        verbose_name=_("Record"),
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name=_("Price"),
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name=_("Count"),
    )

    class Meta:
        verbose_name = "Позиция заказа"
        verbose_name_plural = "Позиции заказа"

    def __str__(self):
        return f"{self.quantity} x {self.record.title}"

    def get_cost(self):
        return self.price * self.quantity
