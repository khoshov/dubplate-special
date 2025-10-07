# apps/records/forms.py  — ПОЛНАЯ ЗАМЕНА ФАЙЛА
from __future__ import annotations

import logging
from typing import Callable, Optional, Tuple

from django import forms
from django.core.exceptions import ValidationError

from .models import Record
from .services.image_service import ImageService
from .services.record_service import DiscogsService, RecordService
from .validators import RecordIdentifierValidator

logger = logging.getLogger(__name__)


class RecordForm(forms.ModelForm):
    """
    Форма создания/редактирования записи Record.

    КЛЮЧЕВОЕ ПОВЕДЕНИЕ:
    - При СОЗДАНИИ и вводе уже существующего catalog_number:
        * форма НЕ генерирует ошибку уникальности;
        * в self.duplicate_record кладётся найденная запись;
        * админка (save_model/response_add) запускает авто-обновление существующей записи
          и делает редирект на неё.
    - При РЕДАКТИРОВАНИИ: всё как обычно, включая проверку уникальности других полей.

    Поле `source` (Discogs | Redeye) используется только при создании — для выбора
    механизма импорта.
    """

    # --- служебный атрибут, читается админкой при сабмите формы ---
    duplicate_record: Optional[Record] = None  # заполнится, если нашли дубликат

    # немодельное поле выбора источника — используется только в create-форме
    SOURCE_DISCOGS = "discogs"
    SOURCE_REDEYE = "redeye"
    SOURCE_CHOICES: Tuple[Tuple[str, str], ...] = (
        (SOURCE_DISCOGS, "Discogs"),
        (SOURCE_REDEYE, "Redeye Records"),
    )

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

    # --- инфраструктурные зависимости формы ---
    record_service: RecordService
    validator: RecordIdentifierValidator
    save_m2m: Callable[[], None]  # заполняется в save()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # Инициализируем сервисы и валидатор
        self.record_service = RecordService(
            discogs_service=DiscogsService(),
            image_service=ImageService(),
        )
        self.validator = RecordIdentifierValidator()

        # Эти поля делаем необязательными на уровне формы;
        # обязательность контролируем в clean() в зависимости от источника.
        if "barcode" in self.fields:
            self.fields["barcode"].required = False
        if "catalog_number" in self.fields:
            self.fields["catalog_number"].required = False

        is_creating = not (self.instance and self.instance.pk)
        if is_creating:
            # --- конфигурация полей для create-формы ---
            current_source = (
                (self.data.get("source") if self.data else None)
                or self.initial.get("source")
                or self.SOURCE_DISCOGS
            )
            self._setup_fields_for_new_record(current_source)
        else:
            # --- при edit поле source не нужно и не должно валидироваться ---
            if "source" in self.fields:
                del self.fields["source"]

    # =========================================================================
    # UI-подстройка формы под выбранный источник (только для create)
    # =========================================================================
    def _setup_fields_for_new_record(self, current_source: str) -> None:
        """Оставляем в форме только поля, релевантные выбранному источнику."""
        if current_source == self.SOURCE_REDEYE:
            allowed = {"source", "catalog_number"}
        else:  # Discogs
            allowed = {"source", "barcode", "catalog_number"}

        for name in list(self.fields.keys()):
            if name not in allowed:
                del self.fields[name]

        if "barcode" in self.fields:
            self.fields["barcode"].widget.attrs.update(
                {
                    "placeholder": "Например: 5060384616698",
                    "class": "form-control barcode-input",
                    "autofocus": True,
                }
            )
        if "catalog_number" in self.fields:
            self.fields["catalog_number"].widget.attrs.update(
                {
                    "placeholder": "Например: RT0541LP2",
                    "class": "form-control catalog-input",
                }
            )
        if "source" in self.fields:
            self.fields["source"].widget.attrs.update({"class": "form-control source-input"})

    # =========================================================================
    # Валидация полей
    # =========================================================================
    def clean_barcode(self) -> Optional[str]:
        """Строгая проверка штрих-кода (для edit и create)."""
        barcode = self.cleaned_data.get("barcode")
        return self.validator.validate_barcode(barcode, self.instance.pk)

    def clean_catalog_number(self) -> Optional[str]:
        """
        Нормализация/валидация каталожного номера.

        Edit (instance.pk есть):
            — стандартная строгая проверка (включая уникальность).
        Create:
            — НЕ проверяем уникальность (не генерируем ValidationError);
            — если в базе уже есть запись с таким номером, пишем её в self.duplicate_record,
              далее админка займётся авто-обновлением и редиректом.
        """
        catalog_number = (self.cleaned_data.get("catalog_number") or "") or None

        # Редактирование — строгая проверка через валидатор формы/проекта
        if self.instance and self.instance.pk:
            return self.validator.validate_catalog_number(catalog_number, self.instance.pk)

        # Создание — мягкая проверка, без unique-ошибки
        normalized = (
            self.validator.normalize_catalog_number(catalog_number)  # type: ignore[attr-defined]
            if hasattr(self.validator, "normalize_catalog_number")
            else catalog_number
        )

        if normalized:
            try:
                existing = Record.objects.filter(catalog_number=normalized).first()
                if existing:
                    self.duplicate_record = existing
            except Exception:
                # Ничего не ломаем, просто не отмечаем дубликат
                logger.exception("Failed to check duplicate by catalog_number during create")

        return normalized

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
        is_creating = not (self.instance and self.instance.pk)
        if not is_creating:
            return cleaned

        source = cleaned.get("source") or self.data.get("source") or self.SOURCE_DISCOGS

        if source == self.SOURCE_REDEYE:
            if not cleaned.get("catalog_number"):
                raise ValidationError({"catalog_number": "Для импорта из Redeye укажите каталожный номер."})
            return cleaned  # уникальность не проверяем — это сделает/использует админка

        # Discogs: нужен хотя бы один идентификатор
        return self.validator.validate_identifiers_required(cleaned)

    # ВАЖНО: Django по умолчанию проверяет уникальность модели в ModelForm.validate_unique().
    # Мы переопределяем этот метод, чтобы ПРИ СОЗДАНИИ исключить catalog_number из
    # проверки unique — дубликат это сигнал «обновить существующую запись», а не ошибка формы.
    def validate_unique(self) -> None:  # type: ignore[override]
        exclude = list(self._get_validation_exclusions())  # type: ignore[attr-defined]
        is_creating = not (self.instance and self.instance.pk)
        if is_creating and "catalog_number" not in exclude:
            exclude.append("catalog_number")

        try:
            self.instance.validate_unique(exclude=exclude)
        except ValidationError as e:
            self._update_errors(e)

    # =========================================================================
    # Сохранение
    # =========================================================================
    def save(self, commit: bool = True) -> Record:
        """
        Edit:
            — обычное сохранение ModelForm.

        Create:
            — импорт из выбранного источника:
                * Redeye: по catalog_number,
                * Discogs: по barcode и/или catalog_number.
            — применение значений формы (если заданы),
            — установка duplicate_record, если импорт обнаружил существующую запись.
        """
        is_creating = not (self.instance and self.instance.pk)
        if not is_creating:
            return super().save(commit=commit)

        source = self.cleaned_data.get("source") or self.SOURCE_DISCOGS
        barcode = self.cleaned_data.get("barcode")
        catalog_number = self.cleaned_data.get("catalog_number")

        try:
            if source == self.SOURCE_REDEYE:
                record, imported = self._import_from_redeye(catalog_number)
            else:
                record, imported = self._import_from_discogs(barcode, catalog_number)

            if not imported:
                # Нашли существующую запись — сообщаем админке через duplicate_record
                self.duplicate_record = record

            # Применим явные значения из формы поверх импортированных
            self._apply_scalar_fields(record)
            record.save()

            # M2M по данным формы (если они присутствуют)
            self._apply_m2m_fields(record)

            # чтобы админка не пыталась вызвать реальный save_m2m (она нужна только при commit=False)
            self.save_m2m = lambda: None  # type: ignore[assignment]
            return record

        except ValueError as e:
            logger.warning("Failed to import record from %s: %s", source, e)
            raise ValidationError({"catalog_number": f"Не удалось импортировать из {source}: {e}"})

    # --- помощники импорта/применения полей ---------------------------------
    def _import_from_redeye(self, catalog_number: Optional[str]) -> Tuple[Record, bool]:
        return self.record_service.import_from_redeye(catalog_number=catalog_number)

    def _import_from_discogs(
        self, barcode: Optional[str], catalog_number: Optional[str]
    ) -> Tuple[Record, bool]:
        return self.record_service.import_from_discogs(
            barcode=barcode, catalog_number=catalog_number
        )

    def _apply_scalar_fields(self, record: Record) -> None:
        """Переносит скалярные поля из формы в запись (только непустые)."""
        for field in (
            "title",
            "release_year",
            "label",
            "country",
            "notes",
            "catalog_number",
            "barcode",
        ):
            if field in self.cleaned_data and self.cleaned_data[field] not in (None, ""):
                setattr(record, field, self.cleaned_data[field])

    def _apply_m2m_fields(self, record: Record) -> None:
        """Применяет M2M-поля из формы (если они присутствуют в cleaned_data)."""
        if "artists" in self.cleaned_data:
            record.artists.set(self.cleaned_data["artists"])
        if "genres" in self.cleaned_data:
            record.genres.set(self.cleaned_data["genres"])
        if "styles" in self.cleaned_data:
            record.styles.set(self.cleaned_data["styles"])
        if "formats" in self.cleaned_data:
            record.formats.set(self.cleaned_data["formats"])

    class Media:
        """live-переключение полей при выборе источника в create-форме."""
        js = ("records/js/record_source_toggle.js",)
