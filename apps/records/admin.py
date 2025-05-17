from django.contrib import admin

from .forms import RecordForm
from .models import Record


class RecordAdmin(admin.ModelAdmin):
    form = RecordForm
    add_fields = ("barcode",)

    def get_fields(self, request, obj=None):
        if not obj:
            return self.add_fields
        return super().get_fields(request, obj)


admin.site.register(Record, RecordAdmin)
