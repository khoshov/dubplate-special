from __future__ import annotations

import logging
from typing import Callable, Optional

from django import forms
from django.core.exceptions import ValidationError

from .mixins import ApplyFieldsMixin
from .validators import RecordIdentifierValidator
from records.constants import SOURCE_DISCOGS, SOURCE_REDEYE, SOURCE_CHOICES
from ..models import Record
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.image.image_service import ImageService
from ..services.providers.redeye.redeye_service import RedeyeService
from ..services.record_service import RecordService

logger = logging.getLogger(__name__)


class RecordForm(ApplyFieldsMixin, forms.ModelForm):
    """
    Форма создания/редактирования модели Record.

    Назначение:
        - Create: импортировать данные из выбранного источника (Redeye/Discogs),
          применить явные значения из формы и установить M2M-связи.
        - Edit: стандартное сохранение ModelForm.

    Особенности:
        - При создании записи catalog_number не считается уникальным — если возвращается дубликат,
          это приводит обновлению существующей записи (в админке это обрабатывается как duplicate_record).

    """

    record_service: RecordService
    save_m2m: Callable[[], None]
    duplicate_record: Optional[Record] = None

    source = forms.ChoiceField(
        choices=SOURCE_CHOICES,
        initial=SOURCE_DISCOGS,
        required=True,
        label="Источник данных",
        help_text="Выберите источник для автоматического импорта",
    )

    class Meta:
        model = Record
        fields = "__all__"
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "barcode": "Штрих-код для поиска в Discogs",
            "catalog_number": "Каталожный номер для поиска (Discogs/Redeye)",
            "discogs_id": "ID релиза в базе Discogs (заполняется автоматически)",
        }

    @property
    def is_editing(self) -> bool:
        """
        Определяет режим формы.

        Возвращает:
            True — если форма открыта для редактирования существующей записи (у instance есть pk);
            False — если форма создаёт новую запись.
        """
        return bool(getattr(self.instance, "pk", None))

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.record_service = RecordService(
            discogs_service=DiscogsService(),
            redeye_service=RedeyeService(),
            image_service=ImageService(),
        )
        self.save_m2m = lambda: None  # type: ignore[assignment]

        if "barcode" in self.fields:
            self.fields["barcode"].required = False
        if "catalog_number" in self.fields:
            self.fields["catalog_number"].required = False

        if not self.is_editing:
            current_source = (
                self.data.get("source") or self.initial.get("source") or SOURCE_DISCOGS
            )
            self._setup_fields_for_new_record(current_source)
            logger.debug(
                "Создание записи: источник=%s, поля формы ограничены под источник.",
                current_source,
            )
        else:
            self.fields.pop("source", None)

    def _setup_fields_for_new_record(self, current_source: str) -> None:
        """Убирает поля не соответствующие источнику."""
        allowed: set = set()
        if current_source == SOURCE_REDEYE:
            allowed = {"source", "catalog_number"}
        elif current_source == SOURCE_DISCOGS:
            allowed = {"source", "barcode", "catalog_number"}

        for name in list(self.fields.keys()):
            if name not in allowed:
                del self.fields[name]

        if "barcode" in self.fields:
            self.fields["barcode"].widget.attrs.update(
                {
                    "placeholder": "Например: 5060384616698",
                    "class": "forms-control barcode-input",
                    "autofocus": True,
                }
            )
        if "catalog_number" in self.fields:
            self.fields["catalog_number"].widget.attrs.update(
                {
                    "placeholder": "Например: RT0541LP2",
                    "class": "forms-control catalog-input",
                }
            )
        if "source" in self.fields:
            self.fields["source"].widget.attrs.update(
                {"class": "forms-control source-input"}
            )

    def clean_barcode(self) -> Optional[str]:
        """Строгая проверка штрих-кода (для edit и create)."""
        barcode = self.cleaned_data.get("barcode")
        return RecordIdentifierValidator.validate_barcode(barcode, self.instance.pk)

    def clean_catalog_number(self) -> Optional[str]:
        """Строгая проверка каталожного номера (для edit и create)."""
        catalog_number = self.cleaned_data.get("catalog_number")
        return RecordIdentifierValidator.validate_catalog_number(catalog_number, self.instance.pk)

    def clean(self):
        """
        Общая валидация формы.

        Create:
            - Redeye: обязателен catalog_number (НО не проверяем его уникальность).
            - Discogs: обязателен хотя бы один идентификатор (barcode ИЛИ catalog_number).
        Edit:
            - поведение без изменений (валидация как обычно).
        """
        cleaned = super().clean()
        if self.is_editing:
            return cleaned

        source = cleaned.get("source") or self.data.get("source") or SOURCE_DISCOGS

        if source == SOURCE_REDEYE:
            if not cleaned.get("catalog_number"):
                logger.debug("Валидация: Redeye без каталожного номера — ошибка формы.")
                raise ValidationError(
                    {
                        "catalog_number": "Для импорта из Redeye укажите каталожный номер."
                    }
                )
            return cleaned
        elif source == SOURCE_DISCOGS:
            return RecordIdentifierValidator.validate_identifiers_required(cleaned)
        return cleaned

    def validate_unique(self) -> None:
        """
        Переопределяет проверку уникальности модели в форме.

        Создание (pk нет):
            — исключает `catalog_number` из unique-проверки, чтобы дубликат
              трактовался как «обновить существующую запись», а не как ошибка формы.
        Редактирование:
            — стандартная проверка уникальности.
        """
        exclude = list(self._get_validation_exclusions())  # type: ignore[attr-defined]
        if not self.is_editing and "catalog_number" not in exclude:
            exclude.append("catalog_number")

        try:
            self.instance.validate_unique(exclude=exclude)
        except ValidationError as e:
            self._update_errors(e)  # type: ignore[attr-defined]

    def save(self, commit: bool = True) -> Record:
        """
        Сохранение формы.
        Edit:
            — обычное сохранение ModelForm.

        Create:
            — импорт из выбранного источника (Redeye по catalog_number, Discogs по barcode/catalog_number),
            — применение значений формы (если заданы),
            — установка duplicate_record, если импорт обнаружил существующую запись (imported=False).
        """

        if self.is_editing:
            return super().save(commit=commit)

        source: str = self.cleaned_data.get("source") or SOURCE_DISCOGS
        barcode: Optional[str] = self.cleaned_data.get("barcode")
        catalog_number: Optional[str] = self.cleaned_data.get("catalog_number")

        try:
            if source == SOURCE_REDEYE:
                logger.debug("Выбран redeye в качестве источника")
                record, record_is_new = self.record_service.import_from_redeye(catalog_number=catalog_number)
            elif source == SOURCE_DISCOGS:
                logger.debug("Выбран discogs в качестве источника")
                record, record_is_new = self.record_service.import_from_discogs(
                    barcode=barcode, catalog_number=catalog_number
                )
            else:
                logger.error("Неизвестный источник %s.", source)
                raise ValidationError({"source": f"Неизвестный источник {source}"})

            if record is None:
                logger.error(f"Импорт из {source} не вернул объект записи.")
                raise ValidationError(
                    {
                        "source": f"Не удалось получить данные записи из источника {source}."
                    }
                )

            if not record_is_new:
                self.duplicate_record = record
            status = (
                "Обнаружен дубликат" if not record_is_new else "Создана новая запись"
            )
            logger.info("%s (pk=%s) при импорте из %s.", status, record.pk, source)

            self._apply_scalar_fields(record=record)
            record.save()
            self._apply_m2m_fields(record=record)
            return record

        except ValueError as err:
            logger.warning("Не удалось импортировать из %s: %s.", source, err)
            raise ValidationError(
                {"catalog_number": f"Не удалось импортировать из {source}: {err}"}
            )

    class Media:
        """live-переключение полей при выборе источника в create-форме."""

        js = ("records/js/record_source_toggle.js",)
