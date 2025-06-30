import logging

from django import forms
from django.core.exceptions import ValidationError

from .models import Record
from .services.discogs_service import DiscogsService

logger = logging.getLogger(__name__)


class RecordForm(forms.ModelForm):
    """Форма для создания/редактирования Record с интеграцией Discogs.

    Attributes:
        discogs_service (DiscogsService): Сервис для работы с Discogs API
    """

    class Meta:
        model = Record
        fields = "__all__"

        error_messages = {
            "barcode": {
                "unique": "Этот штрих-код уже существует (кастомизируем обработку)"
            },
            "catalog_number": {"unique": "Этот каталожный номер уже существует"},
        }

    def __init__(self, *args, **kwargs):
        self.discogs_service = DiscogsService()
        super().__init__(*args, **kwargs)

        self.fields["barcode"].required = False
        self.fields["barcode"].widget.attrs.update(
            {"placeholder": "Введите штрих-код", "class": "barcode-input"}
        )
        self.fields["catalog_number"].required = False
        self.fields["catalog_number"].widget.attrs.update(
            {"placeholder": "Введите каталожный номер", "class": "catalog-input"}
        )

        if not self.instance.pk:
            allowed_fields = ["barcode", "catalog_number"]
            for field in list(self.fields.keys()):
                if field not in allowed_fields:
                    del self.fields[field]

    def clean(self):
        """Валидирует форму на уровне всей формы."""
        cleaned_data = super().clean()

        # Для новых записей требуем хотя бы один из идентификаторов
        if not self.instance.pk:
            barcode = cleaned_data.get("barcode", "")
            catalog_number = cleaned_data.get("catalog_number", "")

            if not barcode and not catalog_number:
                raise ValidationError(
                    "Необходимо указать штрих-код или каталожный номер"
                )

        return cleaned_data

    def save(self, commit=True):
        """Сохраняет запись с возможностью импорта данных из Discogs.

        Args:
            commit (bool): Флаг сохранения в базу данных. По умолчанию True.

        Returns:
            Record: Сохраненный экземпляр Record:
                - С импортированными данными из Discogs (если успешно)
                - С оригинальными данными (если импорт не удался)

        Процесс работы:
            1. Сохраняет переданные в форму данные
            2. Для новых записей (без discogs_id) пытается импортировать данные
            3. Возвращает обновленную или оригинальную запись
        """

        instance = super().save(commit=False)

        if commit:
            instance.save()  # Сохраняем для получения ID
            self.save_m2m()  # Сохраняем связи ManyToMany

        if not instance.discogs_id:
            # Пытаемся импортировать по штрих-коду или каталожному номеру
            barcode = self.cleaned_data.get("barcode", "")
            catalog_number = self.cleaned_data.get("catalog_number", "")

            if barcode:
                result = self.discogs_service.importer.import_release_by_barcode(
                    barcode, instance
                )
                if result:
                    return result

            if catalog_number:
                result = self.discogs_service.importer.import_release_by_catalog_number(
                    catalog_number, instance
                )
                if result:
                    return result

        return instance
