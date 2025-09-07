import logging
from typing import Optional

from records.models import Record
from records.services import DiscogsService, ImageService, RecordService
from records.validators import RecordIdentifierValidator

from django import forms

logger = logging.getLogger(__name__)


class RecordForm(forms.ModelForm):
    """Форма для создания и редактирования записей.

    Интегрирована с Discogs для автоматического импорта данных
    при создании новой записи по штрих-коду или каталожному номеру.

    Attributes:
        record_service: Сервис для работы с записями.
        validator: Валидатор идентификаторов.
        duplicate_record: Найденная запись-дубликат (если есть).
    """

    class Meta:
        model = Record
        fields = "__all__"

        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

        help_texts = {
            "barcode": "Штрих-код для поиска в Discogs",
            "catalog_number": "Каталожный номер для поиска в Discogs",
            "discogs_id": "ID релиза в базе Discogs (заполняется автоматически)",
        }

    def __init__(self, *args, **kwargs):
        """Инициализация формы.

        Args:
            *args: Позиционные аргументы для ModelForm.
            **kwargs: Именованные аргументы для ModelForm.
        """
        super().__init__(*args, **kwargs)

        # Инициализируем сервисы и валидатор
        self.record_service = RecordService(
            discogs_service=DiscogsService(), image_service=ImageService()
        )
        self.validator = RecordIdentifierValidator()

        # Настройка полей
        self.fields["barcode"].required = False
        self.fields["catalog_number"].required = False

        # Для новых записей показываем только поля идентификаторов
        if not self.instance.pk:
            self._setup_fields_for_new_record()

    def _setup_fields_for_new_record(self):
        """Настройка полей для создания новой записи.

        Оставляет только поля штрих-кода и каталожного номера,
        остальные поля будут заполнены автоматически из Discogs.
        """
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

    def clean_barcode(self) -> Optional[str]:
        """Валидация штрих-кода.

        Returns:
            Валидированный штрих-код или None.

        Raises:
            ValidationError: Если штрих-код уже используется.
        """
        barcode = self.cleaned_data.get("barcode")
        return self.validator.validate_barcode(barcode, self.instance.pk)

    def clean_catalog_number(self) -> Optional[str]:
        """Валидация каталожного номера.

        Returns:
            Валидированный каталожный номер или None.

        Raises:
            ValidationError: Если каталожный номер уже используется.
        """
        catalog_number = self.cleaned_data.get("catalog_number")
        return self.validator.validate_catalog_number(catalog_number, self.instance.pk)

    def clean(self):
        """Общая валидация формы.

        Для новых записей проверяет наличие хотя бы одного идентификатора.

        Returns:
            Очищенные данные формы.

        Raises:
            ValidationError: Если не указан ни один идентификатор.
        """
        cleaned_data = super().clean()

        # Для новых записей требуем хотя бы один идентификатор
        if not self.instance.pk:
            return self.validator.validate_identifiers_required(cleaned_data)

        return cleaned_data

    def save(self, commit=True):
        """Сохранение записи с импортом из Discogs.

        Для новых записей пытается импортировать данные из Discogs
        по указанному штрих-коду или каталожному номеру.

        Args:
            commit: Флаг сохранения в БД.

        Returns:
            Сохранённая запись. Может вернуть существующую запись
            если был найден дубликат.
        """
        instance = super().save(commit=False)

        if commit:
            instance.save()
            self.save_m2m()

        # Для новых записей без discogs_id пытаемся импортировать
        if not instance.discogs_id:
            barcode = self.cleaned_data.get("barcode")
            catalog_number = self.cleaned_data.get("catalog_number")

            try:
                record, imported = self.record_service.import_from_discogs(
                    barcode=barcode, catalog_number=catalog_number
                )

                if imported:
                    logger.info(f"Record imported from Discogs: {record.id}")
                else:
                    logger.info(f"Found existing record: {record.id}")
                    # Сохраняем для обработки в admin
                    self.duplicate_record = record

                # Если импортировали новую запись, удаляем временную
                if imported and instance.pk and record.pk != instance.pk:
                    instance.delete()

                return record

            except ValueError as e:
                logger.warning(f"Failed to import record: {e}")
                # Продолжаем с пустой записью

        return instance
