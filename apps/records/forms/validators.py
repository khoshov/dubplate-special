from typing import Any, Dict, Optional

from django.core.exceptions import ValidationError

from ..models import Record


class RecordIdentifierValidator:
    """Валидатор идентификаторов записей.

    Проверяет уникальность штрих-кодов и каталожных номеров,
    а также обеспечивает наличие хотя бы одного идентификатора
    при создании записи.
    """
    @staticmethod
    def validate_barcode(
        barcode: Optional[str], exclude_pk: Optional[int] = None
    ) -> Optional[str]:
        """Валидация штрих-кода на уникальность.

        Args:
            barcode: Штрих-код для проверки.
            exclude_pk: ID записи для исключения из проверки (при редактировании).

        Returns:
            Валидированный штрих-код или None если не указан.

        Raises:
            ValidationError: Если запись с таким штрих-кодом уже существует.
        """
        if not barcode:
            return None

        existing = Record.objects.find_by_barcode(barcode)
        if existing and existing.pk != exclude_pk:
            raise ValidationError(
                f'Запись с таким штрих-кодом уже существует: "{existing.title}" '
                f"(ID: {existing.pk})"
            )

        return barcode

    @staticmethod
    def validate_catalog_number(
        catalog_number: Optional[str], exclude_pk: Optional[int] = None
    ) -> Optional[str]:
        """Валидация каталожного номера на уникальность.

        Args:
            catalog_number: Каталожный номер для проверки.
            exclude_pk: ID записи для исключения из проверки (при редактировании).

        Returns:
            Валидированный каталожный номер или None если не указан.

        Raises:
            ValidationError: Если запись с таким номером уже существует.
        """
        if not catalog_number:
            return None

        existing = Record.objects.find_by_catalog_number(catalog_number)
        if existing and existing.pk != exclude_pk:
            raise ValidationError(
                f'Запись с таким каталожным номером уже существует: "{existing.title}" '
                f"(ID: {existing.pk})"
            )

        return catalog_number

    @staticmethod
    def validate_identifiers_required(
        cleaned_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Проверка наличия хотя бы одного идентификатора.

        Используется при создании новой записи для обеспечения
        возможности импорта из Discogs.

        Args:
            cleaned_data: Очищенные данные формы.

        Returns:
            Данные формы без изменений.

        Raises:
            ValidationError: Если не указан ни один идентификатор.
        """
        barcode = cleaned_data.get("barcode")
        catalog_number = cleaned_data.get("catalog_number")

        if not barcode and not catalog_number:
            raise ValidationError(
                {
                    "__all__": "Необходимо указать штрих-код или каталожный номер для импорта из Discogs"
                }
            )

        return cleaned_data
