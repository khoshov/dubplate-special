from django.contrib import admin

from .forms import RecordForm
from .models import Record, Track


class TrackInline(admin.TabularInline):
    model = Track
    extra = 0
    readonly_fields = ("position", "title", "duration")
    can_delete = False


class RecordAdmin(admin.ModelAdmin):
    form = RecordForm
    inlines = [TrackInline]
    add_fields = ("barcode",)

    def get_fields(self, request, obj=None):
        if not obj:
            return self.add_fields
        return super().get_fields(request, obj)

    def get_inline_instances(self, request, obj=None):
        if obj:
            return [inline(self.model, self.admin_site) for inline in self.inlines]
        return []


admin.site.register(Record, RecordAdmin)
