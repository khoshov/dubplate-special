from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from django import forms
from django.core.exceptions import ValidationError

from .mixins import ApplyFieldsMixin
from .validators import RecordIdentifierValidator
from records.constants import (
    SOURCE_CHOICES,
    SOURCE_DISCOGS,
    SOURCE_REDEYE,
)
from records.models import Record
from records.services.providers.discogs.discogs_service import DiscogsService
from records.services.image.image_service import ImageService
from records.services.audio.audio_service import AudioService
from records.services.providers.redeye.helpers import validate_redeye_product_url
from records.services.providers.redeye.redeye_service import RedeyeService
from records.services.record_service import RecordService

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
    REDEYE_URL_NOT_FOUND_ERROR = "Некорректный URL: запись в Redeye Records не найдена."
    REDEYE_IDENTIFIER_REQUIRED_ERROR = (
        "Для импорта из Redeye укажите URL карточки или catalog_number."
    )

    source = forms.ChoiceField(
        choices=SOURCE_CHOICES,
        initial=SOURCE_REDEYE,
        required=True,
        label="Источник данных",
        help_text="Выберите источник для автоматического импорта",
    )
    source_url = forms.URLField(
        required=False,
        label="URL карточки Redeye",
        help_text="Прямая ссылка на карточку релиза на сайте Redeye Records (или укажите catalog_number).",
    )
    discogs_id = forms.CharField(
        required=False,
        label="Discogs ID",
        help_text="ID релиза Discogs: 1724093 или [r1724093].",
    )

    class Meta:
        model = Record
        fields = "__all__"
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "barcode": "Штрихкод для поиска в Discogs",
            "catalog_number": "Каталожный номер для поиска (Discogs/Redeye)",
            "discogs_id": "ID релиза в базе Discogs (можно указать для импорта)",
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
            audio_service=AudioService(),
        )
        self.save_m2m = lambda: None  # type: ignore[assignment]
        self._prefetched_redeye_payload: dict | None = None
        self._prefetched_redeye_catalog_number: str | None = None

        if "barcode" in self.fields:
            self.fields["barcode"].required = False
        if "catalog_number" in self.fields:
            self.fields["catalog_number"].required = False
        if "discogs_id" in self.fields:
            self.fields["discogs_id"].required = False

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
            self.fields.pop("source_url", None)
            if "active_structured_format_variant" in self.fields:
                self.fields["active_structured_format_variant"].required = False
                self.fields[
                    "active_structured_format_variant"
                ].widget = forms.HiddenInput()

    def _setup_fields_for_new_record(self, _current_source: str) -> None:
        """Убирает поля, не участвующие в импорте при создании записи."""
        allowed = {"source", "discogs_id", "barcode", "catalog_number", "source_url"}

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
        if "discogs_id" in self.fields:
            self.fields["discogs_id"].widget = forms.TextInput()
            self.fields["discogs_id"].widget.attrs.update(
                {
                    "placeholder": "Например: [r1724093]",
                    "class": "forms-control discogs-id-input",
                    "inputmode": "numeric",
                }
            )
        if "catalog_number" in self.fields:
            self.fields["catalog_number"].widget.attrs.update(
                {
                    "placeholder": "Например: RT0541LP2",
                    "class": "forms-control catalog-input",
                }
            )
        if "source_url" in self.fields:
            self.fields["source_url"].widget.attrs.update(
                {
                    "placeholder": ("https://www.redeyerecords.co.uk/vinyl/..."),
                    "class": "forms-control source-url-input",
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

    def clean_discogs_id(self) -> Optional[int]:
        """Строгая проверка Discogs ID (для edit и create)."""
        raw_value = str(self.cleaned_data.get("discogs_id") or "").strip()
        if not raw_value:
            return None

        if raw_value.isdigit():
            discogs_id = int(raw_value)
        else:
            match = re.fullmatch(r"\[\s*[rR](\d+)\s*\]", raw_value) or re.fullmatch(
                r"[rR](\d+)", raw_value
            )
            if not match:
                raise ValidationError(
                    "Укажите Discogs ID в формате 1724093 или [r1724093]."
                )
            discogs_id = int(match.group(1))

        return RecordIdentifierValidator.validate_discogs_id(
            discogs_id, self.instance.pk
        )

    def clean_catalog_number(self) -> Optional[str]:
        """Строгая проверка каталожного номера (для edit и create)."""
        catalog_number = self.cleaned_data.get("catalog_number")
        return RecordIdentifierValidator.validate_catalog_number(
            catalog_number, self.instance.pk
        )

    def clean(self):
        """
        Общая валидация формы.

        Create:
            - Redeye: обязателен хотя бы один идентификатор (source_url ИЛИ catalog_number).
            - Discogs: обязателен хотя бы один идентификатор (discogs_id ИЛИ barcode ИЛИ catalog_number).
        Edit:
            - поведение без изменений (валидация как обычно).
        """
        cleaned = super().clean()
        if self.is_editing:
            return cleaned

        source = cleaned.get("source") or self.data.get("source") or SOURCE_DISCOGS

        if source == SOURCE_REDEYE:
            source_url = (cleaned.get("source_url") or "").strip()
            catalog_number = (cleaned.get("catalog_number") or "").strip().upper()
            if not source_url and not catalog_number:
                raise ValidationError(
                    {"source_url": self.REDEYE_IDENTIFIER_REQUIRED_ERROR}
                )
            if source_url:
                try:
                    validate_redeye_product_url(source_url)
                except ValueError as err:
                    raise ValidationError({"source_url": str(err)})
            if source_url and not catalog_number:
                try:
                    prefetched_payload = self._sanitize_optional_structured_payload(
                        self.record_service.parse_redeye_product_by_url(source_url)
                    )
                except ValueError:
                    raise ValidationError(
                        {"source_url": self.REDEYE_URL_NOT_FOUND_ERROR}
                    )
                except Exception:
                    logger.warning(
                        "Не удалось разобрать карточку Redeye по URL во время валидации формы.",
                        exc_info=True,
                    )
                    raise ValidationError(
                        {"source_url": self.REDEYE_URL_NOT_FOUND_ERROR}
                    )

                parsed_catalog_number = (
                    str(prefetched_payload.get("catalog_number") or "").strip().upper()
                )
                if not parsed_catalog_number:
                    raise ValidationError(
                        {"source_url": self.REDEYE_URL_NOT_FOUND_ERROR}
                    )
                self._prefetched_redeye_payload = prefetched_payload
                self._prefetched_redeye_catalog_number = parsed_catalog_number
                cleaned["catalog_number"] = parsed_catalog_number
            return cleaned
        if source == SOURCE_DISCOGS:
            return RecordIdentifierValidator.validate_identifiers_required(
                cleaned, raw_data=self.data
            )
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
            — импорт из выбранного источника:
              Redeye по catalog_number или прямой ссылке,
              Discogs по barcode/catalog_number,
            — применение значений формы (если заданы),
            — установка duplicate_record, если импорт обнаружил существующую запись (imported=False).
        """

        if self.is_editing:
            return super().save(commit=commit)

        source: str = self.cleaned_data.get("source") or SOURCE_DISCOGS
        discogs_id: Optional[int] = self.cleaned_data.get("discogs_id")
        barcode: Optional[str] = self.cleaned_data.get("barcode")
        catalog_number: Optional[str] = self.cleaned_data.get("catalog_number")
        source_url: Optional[str] = self.cleaned_data.get("source_url")

        try:
            if source == SOURCE_REDEYE:
                logger.debug("Выбран redeye в качестве источника")
                normalized_source_url = (source_url or "").strip()
                fallback_catalog_number = (catalog_number or "").strip().upper()
                if normalized_source_url:
                    raw_payload = self._sanitize_optional_structured_payload(
                        self._prefetched_redeye_payload
                        if self._prefetched_redeye_payload is not None
                        else self.record_service.parse_redeye_product_by_url(
                            normalized_source_url
                        )
                    )
                    parsed_catalog_number = (
                        self._prefetched_redeye_catalog_number
                        or str(raw_payload.get("catalog_number") or "").strip().upper()
                    )
                    effective_catalog_number = parsed_catalog_number
                    if not effective_catalog_number:
                        raise ValidationError(
                            {"source_url": self.REDEYE_URL_NOT_FOUND_ERROR}
                        )
                    record, record_is_new = self.record_service.import_from_redeye(
                        catalog_number=effective_catalog_number,
                        raw_payload=raw_payload,
                        source_url=normalized_source_url,
                    )
                else:
                    if not fallback_catalog_number:
                        raise ValidationError(
                            {"source_url": self.REDEYE_IDENTIFIER_REQUIRED_ERROR}
                        )
                    record, record_is_new = self.record_service.import_from_redeye(
                        catalog_number=fallback_catalog_number,
                        source_url=normalized_source_url or None,
                    )
            elif source == SOURCE_DISCOGS:
                logger.info(
                    "Запрошен импорт Discogs: discogs_id=%s, barcode=%s, catalog_number=%s",
                    discogs_id,
                    barcode,
                    catalog_number,
                )
                record, record_is_new = self.record_service.import_from_discogs(
                    discogs_id=discogs_id,
                    barcode=barcode,
                    catalog_number=catalog_number,
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
                setattr(record, "_duplicate_record", True)
            status = (
                "Обнаружен дубликат" if not record_is_new else "Создана новая запись"
            )
            logger.info("%s (pk=%s) при импорте из %s.", status, record.pk, source)

            self._apply_scalar_fields(record=record)
            record.save()
            self._apply_m2m_fields(record=record)
            self.instance = record
            self._log_import_result(record=record, source=source)
            return record

        except ValueError as err:
            logger.info("Не удалось импортировать из %s: %s.", source, err)
            if source == SOURCE_REDEYE:
                error_field = "source_url"
            elif source == SOURCE_DISCOGS and self.cleaned_data.get("discogs_id"):
                error_field = "discogs_id"
            elif source == SOURCE_DISCOGS and self.cleaned_data.get("barcode"):
                error_field = "barcode"
            else:
                error_field = "catalog_number"
            raise ValidationError(
                {error_field: f"Не удалось импортировать из {source}: {err}"}
            )

    @staticmethod
    def _sanitize_optional_structured_payload(payload: dict | None) -> dict:
        """
        Очищает пустой structured_formats у не-Discogs payload, чтобы он не
        активировал structured-flow без реальных строк формата.
        """
        normalized = dict(payload or {})
        if not normalized.get("structured_formats"):
            normalized.pop("structured_formats", None)
        return normalized

    def _log_import_result(self, *, record: Record, source: str) -> None:
        structured_count = 0
        structured_manager = getattr(record, "structured_formats", None)
        if getattr(record, "pk", None) and structured_manager is not None:
            counter = getattr(structured_manager, "count", None)
            if callable(counter):
                structured_count = counter()

        if source != SOURCE_DISCOGS and structured_count == 0:
            logger.info(
                "Импорт из %s завершён без structured_formats для записи %s: это допустимо для не-Discogs источника.",
                source,
                record.pk,
            )
            return

        logger.info(
            "Импорт из %s завершён: record_id=%s, structured_formats=%d",
            source,
            record.pk,
            structured_count,
        )

    class Media:
        """live-переключение полей при выборе источника в create-форме."""

        js = ("records/js/record_source_toggle.js",)
