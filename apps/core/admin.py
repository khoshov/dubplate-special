from solo.admin import SingletonModelAdmin

from django.contrib import admin

from .models import CurrencyRate


@admin.register(CurrencyRate)
class CurrencyRateAdmin(SingletonModelAdmin):
    pass
