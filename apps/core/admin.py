from django.contrib import admin
from solo.admin import SingletonModelAdmin

from .models import CurrencyRate


@admin.register(CurrencyRate)
class CurrencyRateAdmin(SingletonModelAdmin):
    pass
