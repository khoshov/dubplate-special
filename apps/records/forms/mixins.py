from __future__ import annotations

import logging
from typing import Iterable

from django.forms import ModelForm

from config.logging import NOTICE_LEVEL, log_event
from ..models import Record

logger = logging.getLogger(__name__)
_RECORD_FORM_MIXIN_COMPONENT = "record_form_mixins"


def _log_record_form_mixin_event(
    level: int,
    event: str,
    message: str,
    **context: object,
) -> None:
    log_event(
        logger,
        level,
        message,
        component=_RECORD_FORM_MIXIN_COMPONENT,
        event=event,
        **context,
    )


class ApplyFieldsMixin(ModelForm):
    """
    Вспомогательный класс с дополнительными методами для
    применения данных формы к модели Record.
    """

    def _apply_scalar_fields(self, record: Record) -> None:
        """
        Переносит непустые значения из простых (скалярных) полей формы в модель Record.

        Args:
            record: Экземпляр модели Record, в который переносятся значения.
        """
        scalar_fields: Iterable[str] = (
            "title",
            "release_year",
            "release_month",
            "release_day",
            "label",
            "country",
            "notes",
            "catalog_number",
            "barcode",
        )

        for field in scalar_fields:
            if field in self.cleaned_data and self.cleaned_data[field] not in (
                None,
                "",
            ):
                old_val = getattr(record, field, None)
                new_val = self.cleaned_data[field]
                if old_val != new_val:
                    _log_record_form_mixin_event(
                        logging.DEBUG,
                        "field_changed",
                        "Поле записи изменено по данным формы.",
                        record_id=getattr(record, "pk", None),
                        field=field,
                        old_value=old_val,
                        new_value=new_val,
                    )
                setattr(record, field, self.cleaned_data[field])

    def _apply_m2m_fields(self, record: Record) -> None:
        """
        Устанавливает значения в M2M-поля из формы модели Record если они присутствуют в cleaned_data.

        Args:
            record: Экземпляр модели Record для обновления связей.
        """
        changed_data = set(getattr(self, "changed_data", []))

        if "artists" in self.cleaned_data:
            record.artists.set(self.cleaned_data["artists"])  # type: ignore[attr-defined]
        if "genres" in self.cleaned_data:
            record.genres.set(self.cleaned_data["genres"])  # type: ignore[attr-defined]
        if "styles" in self.cleaned_data:
            record.styles.set(self.cleaned_data["styles"])  # type: ignore[attr-defined]
        if "formats" in self.cleaned_data:
            if getattr(record, "pk", None) and "formats" not in changed_data:
                _log_record_form_mixin_event(
                    NOTICE_LEVEL,
                    "formats_unchanged_skip",
                    "Поле formats не изменилось; повторная запись legacy M2M пропущена.",
                    record_id=getattr(record, "pk", None),
                )
                return
            record.formats.set(self.cleaned_data["formats"])  # type: ignore[attr-defined]
