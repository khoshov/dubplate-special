from typing import Any, Dict, Mapping, Optional

from django.core.exceptions import ValidationError

from ..models import Record


class RecordIdentifierValidator:
    """Валидатор идентификаторов записей.

    Проверяет уникальность штрих-кодов и каталожных номеров,
    а также обеспечивает наличие хотя бы одного идентификатора
    при создании записи.
    """

    @staticmethod
    def validate_discogs_id(
        discogs_id: Optional[int], exclude_pk: Optional[int] = None
    ) -> Optional[int]:
        """Валидация Discogs ID на уникальность.

        Args:
            discogs_id: Discogs ID для проверки.
            exclude_pk: ID записи для исключения из проверки (при редактировании).

        Returns:
            Валидированный Discogs ID или None если не указан.

        Raises:
            ValidationError: Если запись с таким Discogs ID уже существует.
        """
        if not discogs_id:
            return None

        existing = Record.objects.find_by_discogs_id(discogs_id)
        if existing and existing.pk != exclude_pk:
            raise ValidationError(
                f'Запись с таким Discogs ID уже существует: "{existing.title}" '
                f"(ID: {existing.pk})"
            )

        return discogs_id

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
        cleaned_data: Dict[str, Any], raw_data: Optional[Mapping[str, Any]] = None
    ) -> Dict[str, Any]:
        """Проверка наличия хотя бы одного идентификатора.

        Используется при создании новой записи для обеспечения
        возможности импорта из Discogs.

        Args:
            cleaned_data: Очищенные данные формы.
            raw_data: Сырые данные формы (например, self.data), чтобы
                не терять факт ввода при ошибке валидации конкретного поля.

        Returns:
            Данные формы без изменений.

        Raises:
            ValidationError: Если не указан ни один идентификатор.
        """

        def _has_value(key: str) -> bool:
            value = cleaned_data.get(key)
            if value not in (None, ""):
                return True
            if raw_data is None:
                return False
            raw_value = raw_data.get(key)
            if raw_value is None:
                return False
            if isinstance(raw_value, str):
                return bool(raw_value.strip())
            return True

        if not (
            _has_value("discogs_id")
            or _has_value("barcode")
            or _has_value("catalog_number")
        ):
            raise ValidationError(
                {
                    "__all__": "Необходимо указать хотя бы один идентификатор: discogs_id, barecode или catalog_number."
                }
            )

        return cleaned_data
