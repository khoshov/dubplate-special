from django import forms
from django.core.exceptions import ValidationError

from .models import Record
from .services.discogs_service import DiscogsService


class RecordForm(forms.ModelForm):
    """
    Форма для создания и редактирования Record с интеграцией Discogs.
    Автоматически импортирует данные по штрих-коду при сохранении.
    """

    class Meta:
        model = Record
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["barcode"].required = True
        self.fields["barcode"].widget.attrs.update({
            'placeholder': 'Введите штрих-код',
            'class': 'barcode-input'
        })

    def clean_barcode(self):
        """Валидация штрих-кода"""
        barcode = self.cleaned_data['barcode']
        if len(barcode) < 8:
            raise ValidationError("Штрих-код должен содержать минимум 8 символов")
        return barcode.strip()

    def save(self, commit=True):
        """
        Сохраняет запись и пытается импортировать данные из Discogs.
        В случае ошибки импорта сохраняет только введенные данные.
        """
        instance = super().save(commit=False)

        if commit:
            instance.save()  # Сохраняем для получения ID
            self.save_m2m()  # Сохраняем связи ManyToMany

        # Импорт из Discogs
        if not instance.discogs_id:  # Импортируем только для новых записей
            service = DiscogsService()
            return service.import_release_by_barcode(instance.barcode, instance) or instance


        return instance