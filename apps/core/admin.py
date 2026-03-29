from solo.admin import SingletonModelAdmin

from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import CurrencyRate


@admin.register(CurrencyRate)
class CurrencyRateAdmin(SingletonModelAdmin, ModelAdmin):
    pass
