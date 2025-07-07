from django_extensions.db.models import TimeStampedModel
from solo.models import SingletonModel

from django.db import models


class CurrencyRate(SingletonModel, TimeStampedModel):
    dollar_value = models.FloatField(default=80.0, verbose_name="Курс доллара к рублю")
    dollar_auto_update = models.BooleanField(
        default=True,
        verbose_name="Автообновление Доллара",
    )
    euro_value = models.FloatField(default=100.0, verbose_name="Курс евро к рублю")
    euro_auto_update = models.BooleanField(
        default=True,
        verbose_name="Автообновление Евро",
    )

    class Meta:
        verbose_name = "Курс валют"

    def __str__(self):
        return "Курс валют"
