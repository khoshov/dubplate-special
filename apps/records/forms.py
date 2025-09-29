import logging
from typing import Optional

from django import forms
from django.core.exceptions import ValidationError

from records.models import Record
from records.services import DiscogsService, ImageService, RecordService
from records.validators import RecordIdentifierValidator

logger = logging.getLogger(__name__)


class RecordForm(forms.ModelForm):
    """
    Форма создания/редактирования Record.

    Главное изменение: добавили поле `source` (Discogs | Redeye).
    При создании запись подтягивается из выбранного источника:
      - Discogs: по barcode или catalog_number (как было).
      - Redeye: по catalog_number.

    Также на этапе рендера формы (создание) скрываем "лишние" поля
    в зависимости от выбранного источника.
    """

    # Поле ИСТОЧНИКА — это НЕ модельное поле, используется только на форме "создать".
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
            # ВНИМАНИЕ: source — не модельное, но Django позволит help_text,
            # т.к. поле определено на форме (см. выше).
            "barcode": "Штрих-код для поиска в Discogs",
            "catalog_number": "Каталожный номер для поиска (Discogs/Redeye)",
            "discogs_id": "ID релиза в базе Discogs (заполняется автоматически)",
        }

    def __init__(self, *args, **kwargs):
        """
        Инициализация:
        - Поднимаем сервисы (RecordService использует DiscogsService/ImageService;
          для Redeye мы добавим метод в RecordService).
        - Делает поля barcode/catalog_number необязательными.
        - Если создаём НОВУЮ запись — показываем только поля идентификаторов
          + поле выбора источника, причём набор полей зависит от выбранного `source`.
        """
        super().__init__(*args, **kwargs)

        self.record_service = RecordService(
            discogs_service=DiscogsService(), image_service=ImageService()
        )
        self.validator = RecordIdentifierValidator()

        # Оба поля не обязательные на уровне формы;
        # обязательность контролируем нашей общей валидацией в .clean()
        self.fields["barcode"].required = False
        self.fields["catalog_number"].required = False

        # При создании записи конфигурируем набор видимых полей
        if not self.instance.pk:
            # Какой источник выбран сейчас?
            # При первом открытии формы берём initial/дефолт, при сабмите — self.data.
            current_source = (
                (self.data.get("source") if self.data else None)
                or self.initial.get("source")
                or self.SOURCE_DISCOGS
            )
            self._setup_fields_for_new_record(current_source)

    # ---- Рендер/настройка полей при создании ----
    def _setup_fields_for_new_record(self, current_source: str):
        """
        Для формы "создать":
        - Оставляем только нужные поля.
        - Плейсхолдеры, классы и т.п.
        """
        # Сохраняем все поля (если когда-то понадобится восстановить)
        self._all_fields = dict(self.fields)

        # Разрешённые поля для каждого источника
        if current_source == self.SOURCE_REDEYE:
            # На Redeye ищем ТОЛЬКО по каталожному номеру
            allowed_fields = ["source", "catalog_number"]
        else:
            # Discogs: можно по barcode И/ИЛИ по catalog_number
            allowed_fields = ["source", "barcode", "catalog_number"]

        for name in list(self.fields.keys()):
            if name not in allowed_fields:
                del self.fields[name]

        # Немного UX
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
        # Поле источника — чтобы было сверху
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
        - Discogs: требуется хотя бы ОДИН идентификатор (как было — валидатор validate_identifiers_required)
        - Redeye: обязателен catalog_number (штрих-код не используется)
        """
        cleaned = super().clean()

        if not self.instance.pk:
            source = cleaned.get("source") or self.data.get("source") or self.SOURCE_DISCOGS

            if source == self.SOURCE_REDEYE:
                # Требуем именно catalog_number
                if not cleaned.get("catalog_number"):
                    raise ValidationError(
                        {"catalog_number": "Для импорта из Redeye укажите каталожный номер."}
                    )
                return cleaned

            # Иначе — Discogs: нужен хотя бы один идентификатор
            return self.validator.validate_identifiers_required(cleaned)

        return cleaned

    # ---- Сохранение ----
    def save(self, commit: bool = True):
        """
        Логика сохранения:
        1) Создаём "черновик" Record (как и раньше).
        2) Если это новая запись — импортируем из выбранного источника.
        3) Если найден дубликат — прокидываем его в admin через self.duplicate_record.
        4) Если импорт создал/нашёл другую запись — удаляем временную.
        """
        instance = super().save(commit=False)

        if commit:
            instance.save()
            self.save_m2m()

        # Импорт выполняем только для НОВЫХ записей
        if not self.instance.pk:
            # Определим источник: сначала cleaned_data (надёжнее), затем POST.
            source = self.cleaned_data.get("source") or self.data.get("source") or self.SOURCE_DISCOGS
            barcode = self.cleaned_data.get("barcode")
            catalog_number = self.cleaned_data.get("catalog_number")

            try:
                # Ветвление по источнику
                if source == self.SOURCE_REDEYE:
                    # Метод добавим в RecordService на следующем шаге
                    record, imported = self.record_service.import_from_redeye(
                        catalog_number=catalog_number
                    )
                    log_source = "Redeye"
                else:
                    record, imported = self.record_service.import_from_discogs(
                        barcode=barcode, catalog_number=catalog_number
                    )
                    log_source = "Discogs"

                if imported:
                    logger.info(f"Record imported from {log_source}: {record.id}")
                else:
                    logger.info(f"Found existing record ({log_source}): {record.id}")
                    # Сообщаем админке, что нужно редиректнуть на найденную запись
                    self.duplicate_record = record

                # Удаляем временную запись, если импорт вернул другой объект
                if imported and instance.pk and record.pk != instance.pk:
                    instance.delete()

                return record

            except ValueError as e:
                # Нормальная ситуация: ничего не нашли / парсер не смог — оставляем пустую запись
                logger.warning(f"Failed to import record ({source}): {e}")

        return instance

    class Media:
        """
        (Опционально) Если хочешь live-переключение полей в админке без перезагрузки —
        подключи маленький JS. Ниже — объявление, сам файл положим в
        `apps/records/static/records/js/record_source_toggle.js` и подключим статику.
        Если не добавлять этот файл — всё равно будет работать сервер-сайд скрытие
        при каждом сабмите/перезагрузке формы.
        """
        js = ("records/js/record_source_toggle.js",)
