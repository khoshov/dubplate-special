import logging
from typing import Optional

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

        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
            "cover_image": forms.FileInput(),
        }

        help_texts = {
            "barcode": "Штрих-код для поиска в Discogs",
            "catalog_number": "Каталожный номер для поиска в Discogs",
            "discogs_id": "ID релиза в базе Discogs (заполняется автоматически)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Создаем сервис только когда он нужен (lazy loading)
        self._discogs_service = None

        # Настройка полей
        self.fields["barcode"].required = False
        self.fields["catalog_number"].required = False

        # Для новых записей показываем только поля идентификаторов
        if not self.instance.pk:
            # Сохраняем все поля для последующего восстановления
            self._all_fields = dict(self.fields)

            # Оставляем только нужные поля
            allowed_fields = ["barcode", "catalog_number"]
            for field_name in list(self.fields.keys()):
                if field_name not in allowed_fields:
                    del self.fields[field_name]

            # Добавляем плейсхолдеры и классы
            self.fields["barcode"].widget.attrs.update(
                {
                    "placeholder": "Например: 5060384616698",
                    "class": "form-control barcode-input",
                    "autofocus": True,
                }
            )
            self.fields["catalog_number"].widget.attrs.update(
                {
                    "placeholder": "Например: RT0541LP2",
                    "class": "form-control catalog-input",
                }
            )

    @property
    def discogs_service(self) -> DiscogsService:
        """Ленивая инициализация Discogs сервиса."""
        if self._discogs_service is None:
            self._discogs_service = DiscogsService()
        return self._discogs_service

    def clean_barcode(self) -> Optional[str]:
        """Валидация штрих-кода."""
        barcode = self.cleaned_data.get("barcode")

        if barcode:
            # Проверяем, существует ли запись с таким штрих-кодом
            existing = (
                Record.objects.filter(barcode=barcode)
                .exclude(pk=self.instance.pk)
                .first()
            )
            if existing:
                raise ValidationError(
                    f'Запись с таким штрих-кодом уже существует: "{existing.title}" '
                    f"(ID: {existing.pk}, Каталожный номер: {existing.catalog_number})"
                )

        return barcode if barcode else None

    def clean_catalog_number(self) -> Optional[str]:
        """Валидация каталожного номера."""
        catalog_number = self.cleaned_data.get("catalog_number")

        if catalog_number:
            # Проверяем, существует ли запись с таким каталожным номером
            existing = (
                Record.objects.filter(catalog_number=catalog_number)
                .exclude(pk=self.instance.pk)
                .first()
            )
            if existing:
                raise ValidationError(
                    f'Запись с таким каталожным номером уже существует: "{existing.title}" '
                    f"(ID: {existing.pk}, Штрих-код: {existing.barcode})"
                )

        return catalog_number if catalog_number else None

    def clean(self):
        """Общая валидация формы."""
        cleaned_data = super().clean()

        # Для новых записей требуем хотя бы один идентификатор
        if not self.instance.pk:
            barcode = cleaned_data.get("barcode")
            catalog_number = cleaned_data.get("catalog_number")

            if not barcode and not catalog_number:
                raise ValidationError(
                    {
                        "__all__": "Необходимо указать штрих-код или каталожный номер для импорта из Discogs"
                    }
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
                        logger.info(
                            f"Successfully imported record by barcode: {barcode}"
                        )
                        # Если вернулась другая запись (дубликат), сохраняем её в атрибут формы
                        if result.pk != instance.pk:
                            self.duplicate_record = result
                        return result
                except Exception as e:
                    logger.error(f"Failed to import by barcode {barcode}: {str(e)}")

            if catalog_number:
                try:
                    result = (
                        self.discogs_service.importer.import_release_by_catalog_number(
                            catalog_number, instance
                        )
                    )
                    if result:
                        logger.info(
                            f"Successfully imported record by catalog number: {catalog_number}"
                        )
                        # Если вернулась другая запись (дубликат), сохраняем её в атрибут формы
                        if result.pk != instance.pk:
                            self.duplicate_record = result
                        return result
                except Exception as e:
                    logger.error(
                        f"Failed to import by catalog number {catalog_number}: {str(e)}"
                    )

            # Если импорт не удался, логируем это
            logger.warning(
                f"Failed to import record. Barcode: {barcode or 'N/A'}, "
                f"Catalog: {catalog_number or 'N/A'}"
            )

        return instance


#     def _try_import_from_discogs(self, record: Record) -> Optional[Record]:
#         """
#         Пытается импортировать данные из Discogs.
#
#         Args:
#             record: Запись для импорта
#
#         Returns:
#             Optional[Record]: Импортированная запись или None
#         """
#         barcode = self.cleaned_data.get('barcode')
#         catalog_number = self.cleaned_data.get('catalog_number')
#
#         # Приоритет у штрих-кода
#         if barcode:
#             logger.info(f"Attempting to import by barcode: {barcode}")
#             try:
#                 result = self.discogs_service.importer.import_release_by_barcode(
#                     barcode, record
#                 )
#                 if result:
#                     logger.info(
#                         f"Successfully imported record {result.pk} by barcode: {barcode}"
#                     )
#                     return result
#             except Exception as e:
#                 logger.error(f"Failed to import by barcode {barcode}: {str(e)}", exc_info=True)
#
#         # Если не получилось по штрих-коду, пробуем по каталожному номеру
#         if catalog_number:
#             logger.info(f"Attempting to import by catalog number: {catalog_number}")
#             try:
#                 result = self.discogs_service.importer.import_release_by_catalog_number(
#                     catalog_number, record
#                 )
#                 if result:
#                     logger.info(
#                         f"Successfully imported record {result.pk} by catalog: {catalog_number}"
#                     )
#                     return result
#             except Exception as e:
#                 logger.error(f"Failed to import by catalog {catalog_number}: {str(e)}", exc_info=True)
#
#         logger.warning(
#             f"Import failed. Barcode: {barcode or 'N/A'}, "
#             f"Catalog: {catalog_number or 'N/A'}"
#         )
#
#         # Если импорт не удался, удаляем временную запись
#         if record.pk and record.title == "Импорт из Discogs...":
#             logger.info(f"Deleting temporary record {record.pk} after failed import")
#             record.delete()
#             return None
#
#         return None
#
#
# class RecordSearchForm(forms.Form):
#     """Форма для поиска записей."""
#
#     q = forms.CharField(
#         required=False,
#         widget=forms.TextInput(attrs={
#             'placeholder': 'Поиск по названию, артисту, штрих-коду...',
#             'class': 'form-control',
#         })
#     )
#
#     genre = forms.ModelMultipleChoiceField(
#         required=False,
#         queryset=None,  # Устанавливается в __init__
#         widget=forms.CheckboxSelectMultiple,
#     )
#
#     style = forms.ModelMultipleChoiceField(
#         required=False,
#         queryset=None,  # Устанавливается в __init__
#         widget=forms.CheckboxSelectMultiple,
#     )
#
#     in_stock = forms.BooleanField(
#         required=False,
#         initial=False,
#         widget=forms.CheckboxInput(attrs={
#             'class': 'form-check-input',
#         }),
#         label='Только в наличии'
#     )
#
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#
#         # Импортируем здесь, чтобы избежать циклических импортов
#         from .models import Genre, Style
#
#         self.fields['genre'].queryset = Genre.objects.all()
#         self.fields['style'].queryset = Style.objects.all()
