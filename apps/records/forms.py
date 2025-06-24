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
            }
        }

    def __init__(self, *args, **kwargs):
        self.discogs_service = DiscogsService()
        super().__init__(*args, **kwargs)

        self.fields["barcode"].required = True
        self.fields["barcode"].widget.attrs.update(
            {"placeholder": "Введите штрих-код", "class": "barcode-input"}
        )

        if not self.instance.pk:
            allowed_fields = ["barcode"]
            for field in list(self.fields.keys()):
                if field not in allowed_fields:
                    del self.fields[field]

    def clean_barcode(self):
        """Валидирует штрих-код.

        Returns:
            Очищенный штрих-код

        Raises:
            ValidationError: Если штрих-код короче 8 символов
        """
        barcode = self.cleaned_data["barcode"].strip()
        if len(barcode) < 8:
            raise ValidationError("Штрих-код должен содержать минимум 8 символов")
        return barcode

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
            return (
                self.discogs_service.importer.import_release(instance.barcode, instance)
                or instance
            )

        return instance
