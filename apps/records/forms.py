from django import forms

from apps.records.models import Record

from .services.discogs_service import DiscogsService


class RecordForm(forms.ModelForm):
    class Meta:
        model = Record
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["barcode"].required = True

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Сначала сохраняем запись, чтобы получить ID
        if commit:
            instance.save()

        # Затем импортируем данные из Discogs
        service = DiscogsService()
        updated_record = service.import_release_by_barcode(instance.barcode, instance)

        # Если импорт не удался, возвращаем исходную запись
        if not updated_record:
            if commit:
                self.save_m2m()
            return instance

        # Если импорт успешен, возвращаем обновленную запись
        if commit:
            self.save_m2m()
        return updated_record
