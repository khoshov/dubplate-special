from django import forms
from django.core.exceptions import ValidationError

from .models import Record
from .services.discogs_service import DiscogsService


class RecordForm(forms.ModelForm):
    """Форма для работы с записями (Record) с интеграцией Discogs API.

    Attributes:
        Meta (class): Вложенный класс для конфигурации формы.

    Форма обеспечивает:
        - Создание и редактирование записей
        - Валидацию штрих-кода
        - Автоматический импорт данных из Discogs по штрих-коду
    """

    class Meta:
        model = Record
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["barcode"].required = True
        self.fields["barcode"].widget.attrs.update(
            {"placeholder": "Введите штрих-код", "class": "barcode-input"}
        )

        if not self.instance.pk:
            allowed_fields = ['barcode']
            for field in list(self.fields.keys()):
                if field not in allowed_fields:
                    del self.fields[field]

    def clean_barcode(self):
        """Валидирует поле штрих-кода.

        Returns:
            str: Очищенный (без пробелов) валидный штрих-код.

        Raises:
            ValidationError: Если штрих-код короче 8 символов.
        """
        """Валидация штрих-кода"""

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

        # Импорт из Discogs
        if not instance.discogs_id:  # Импортируем только для новых записей
            service = DiscogsService()
            return (
                service.import_release_by_barcode(instance.barcode, instance)
                or instance
            )

        return instance
