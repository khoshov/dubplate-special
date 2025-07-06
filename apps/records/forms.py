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
                "unique": "Запись с таким штрих-кодом уже существует в базе данных"
            },
            "catalog_number": {
                "unique": "Запись с таким каталожным номером уже существует в базе данных"
            },
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

    def clean_barcode(self):
        """Валидация штрих-кода с проверкой существующих записей."""
        barcode = self.cleaned_data.get('barcode')

        if barcode:
            # Проверяем, существует ли запись с таким штрих-кодом
            existing = Record.objects.filter(barcode=barcode).exclude(pk=self.instance.pk).first()
            if existing:
                raise ValidationError(
                    f'Запись с таким штрих-кодом уже существует: "{existing.title}" '
                    f'(ID: {existing.pk}, Каталожный номер: {existing.catalog_number})'
                )

        return barcode

    def clean_catalog_number(self):
        """Валидация каталожного номера с проверкой существующих записей."""
        catalog_number = self.cleaned_data.get('catalog_number')

        if catalog_number:
            # Проверяем, существует ли запись с таким каталожным номером
            existing = Record.objects.filter(catalog_number=catalog_number).exclude(pk=self.instance.pk).first()
            if existing:
                raise ValidationError(
                    f'Запись с таким каталожным номером уже существует: "{existing.title}" '
                    f'(ID: {existing.pk}, Штрих-код: {existing.barcode})'
                )

        return catalog_number

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
                - Существующая запись (если найден дубликат по discogs_id)
                - С импортированными данными из Discogs (если успешно)
                - С оригинальными данными (если импорт не удался)

        Процесс работы:
            1. Сохраняет переданные в форму данные
            2. Для новых записей (без discogs_id) пытается импортировать данные
            3. Возвращает обновленную, существующую или оригинальную запись
        """

        instance = super().save(commit=False)

        if commit:
            instance.save()  # Сохраняем для получения ID
            self.save_m2m()  # Сохраняем связи ManyToMany

        if not instance.discogs_id:
            # Пытаемся импортировать по штрих-коду или каталожному номеру
            barcode = self.cleaned_data.get("barcode", "")
            catalog_number = self.cleaned_data.get("catalog_number", "")

            # Приоритет у штрих-кода
            if barcode:
                try:
                    result = self.discogs_service.importer.import_release_by_barcode(
                        barcode, instance
                    )
                    if result:
                        logger.info(f"Successfully imported record by barcode: {barcode}")
                        # Если вернулась другая запись (дубликат), сохраняем её в атрибут формы
                        if result.pk != instance.pk:
                            self.duplicate_record = result
                        return result
                except Exception as e:
                    logger.error(f"Failed to import by barcode {barcode}: {str(e)}")

            if catalog_number:
                try:
                    result = self.discogs_service.importer.import_release_by_catalog_number(
                        catalog_number, instance
                    )
                    if result:
                        logger.info(f"Successfully imported record by catalog number: {catalog_number}")
                        # Если вернулась другая запись (дубликат), сохраняем её в атрибут формы
                        if result.pk != instance.pk:
                            self.duplicate_record = result
                        return result
                except Exception as e:
                    logger.error(f"Failed to import by catalog number {catalog_number}: {str(e)}")

            # Если импорт не удался, логируем это
            logger.warning(
                f"Failed to import record. Barcode: {barcode or 'N/A'}, "
                f"Catalog: {catalog_number or 'N/A'}"
            )

        return instance