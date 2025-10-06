# apps/records/forms.py
import logging
from typing import Optional

from django import forms
from django.core.exceptions import ValidationError
from .models import Record
from .services.image_service import ImageService
from .services.record_service import DiscogsService, RecordService
from .validators import RecordIdentifierValidator

logger = logging.getLogger(__name__)


class RecordForm(forms.ModelForm):
    """
    Форма создания/редактирования Record.

    - При СОЗДАНИИ: показываем поле источника (Discogs | Redeye) и тянем данные из него.
    - При РЕДАКТИРОВАНИИ: поле источника скрыто и НЕ участвует в валидации/сохранении.
    """
    # подсказываем типизатору, что атрибут появляется в save()
    duplicate_record: Optional[Record] = None

    # НЕМОДЕЛЬНОЕ поле источника — используем ТОЛЬКО при создании
    SOURCE_DISCOGS = "discogs"
    SOURCE_REDEYE = "redeye"
    SOURCE_CHOICES = (
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
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "barcode": "Штрих-код для поиска в Discogs",
            "catalog_number": "Каталожный номер для поиска (Discogs/Redeye)",
            "discogs_id": "ID релиза в базе Discogs (заполняется автоматически)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.record_service = RecordService(
            discogs_service=DiscogsService(), image_service=ImageService()
        )
        self.validator = RecordIdentifierValidator()

        # Эти поля не обязательные на уровне формы;
        # обязательность контролируем в clean() для кейса "создание"
        if "barcode" in self.fields:
            self.fields["barcode"].required = False
        if "catalog_number" in self.fields:
            self.fields["catalog_number"].required = False

        is_creating = not (self.instance and self.instance.pk)

        if is_creating:
            # ---- Конфигурация формы СОЗДАНИЯ ----
            current_source = (
                    (self.data.get("source") if self.data else None)
                    or self.initial.get("source")
                    or self.SOURCE_DISCOGS
            )
            self._setup_fields_for_new_record(current_source)
        else:
            # ---- РЕДАКТИРОВАНИЕ: поле "source" НЕ нужно и НЕ должно валидироваться ----
            if "source" in self.fields:
                del self.fields["source"]

    # ---- Рендер/настройка полей при создании ----
    def _setup_fields_for_new_record(self, current_source: str):
        """
        Для формы "создать":
        - оставляем только нужные поля,
        - выставляем плейсхолдеры и классы.
        """
        if current_source == self.SOURCE_REDEYE:
            # Redeye: ищем ТОЛЬКО по каталожному номеру
            allowed_fields = ["source", "catalog_number"]
        else:
            # Discogs: можно по barcode И/ИЛИ по catalog_number
            allowed_fields = ["source", "barcode", "catalog_number"]

        for name in list(self.fields.keys()):
            if name not in allowed_fields:
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

    # ---- Чистим индивидуальные поля ----
    def clean_barcode(self) -> Optional[str]:
        barcode = self.cleaned_data.get("barcode")
        return self.validator.validate_barcode(barcode, self.instance.pk)

    def clean_catalog_number(self) -> Optional[str]:
        catalog_number = self.cleaned_data.get("catalog_number")
        return self.validator.validate_catalog_number(catalog_number, self.instance.pk)

    # ---- Общая валидация ----
    def clean(self):
        """
        Для НОВОЙ записи:
        - Redeye: обязателен catalog_number;
        - Discogs: обязателен barcode ИЛИ catalog_number.

        Для РЕДАКТИРОВАНИЯ: ничего доп. не требуем.
        """
        cleaned = super().clean()

        is_creating = not (self.instance and self.instance.pk)
        if not is_creating:
            # редактирование — без требований к идентификаторам
            return cleaned

        source = cleaned.get("source") or self.data.get("source") or self.SOURCE_DISCOGS

        if source == self.SOURCE_REDEYE:
            if not cleaned.get("catalog_number"):
                raise ValidationError(
                    {"catalog_number": "Для импорта из Redeye укажите каталожный номер."}
                )
            return cleaned

        # Discogs: нужен хотя бы один идентификатор
        return self.validator.validate_identifiers_required(cleaned)

    # ---- Сохранение ----
    def save(self, commit: bool = True):
        """
        - Редактирование существующей записи: обычное сохранение ModelForm.
        - Создание новой записи: импорт из выбранного источника и применение значений формы.
        """
        is_creating = not (self.instance and self.instance.pk)

        if not is_creating:
            # === РЕДАКТИРОВАНИЕ СУЩЕСТВУЮЩЕЙ ЗАПИСИ ===
            return super().save(commit=commit)

        # === СОЗДАНИЕ НОВОЙ ЗАПИСИ (импорт) ===
        source = self.cleaned_data.get("source") or self.SOURCE_DISCOGS
        barcode = self.cleaned_data.get("barcode")
        catalog_number = self.cleaned_data.get("catalog_number")

        try:
            if source == self.SOURCE_REDEYE:
                record, imported = self.record_service.import_from_redeye(
                    catalog_number=catalog_number
                )
            else:
                record, imported = self.record_service.import_from_discogs(
                    barcode=barcode,
                    catalog_number=catalog_number,
                )

            if imported:
                logger.info("Record imported: %s", record.id)
            else:
                logger.info("Found existing record: %s", record.id)
                self.duplicate_record = record  # для возможного редиректа в админке

            # Применим значения из формы (если они были заданы)
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
            record.save()

            # M2M из формы (ровно то, что выбрал пользователь)
            if "artists" in self.cleaned_data:
                record.artists.set(self.cleaned_data["artists"])
            if "genres" in self.cleaned_data:
                record.genres.set(self.cleaned_data["genres"])
            if "styles" in self.cleaned_data:
                record.styles.set(self.cleaned_data["styles"])
            if "formats" in self.cleaned_data:
                record.formats.set(self.cleaned_data["formats"])

            # Админка потом вызовет form.save_m2m(); подставляем no-op
            self.save_m2m = lambda: None  # type: ignore[attr-defined]
            return record

        except ValueError as e:
            logger.warning(f"Failed to import record ({source}): {e}")
            # Привязываем ошибку к каталожному номеру (для обоих источников это уместно)
            raise ValidationError({"catalog_number": f"Не удалось импортировать из {source}: {e}"})

    class Media:
        """
        live-переключение полей в админке при выборе источника для парсинга
        """
        js = ("records/js/record_source_toggle.js",)
