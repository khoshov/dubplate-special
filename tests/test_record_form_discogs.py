from __future__ import annotations

from django.test import TestCase
from django.test import SimpleTestCase

from records.constants import SOURCE_DISCOGS
from records.forms import RecordForm
from records.models import Record


class RecordFormDiscogsTests(SimpleTestCase):
    def test_discogs_id_widget_is_text_input_on_add_form(self) -> None:
        form = RecordForm(data={"source": SOURCE_DISCOGS})

        assert form.fields["discogs_id"].widget.input_type == "text"
        assert form.fields["discogs_id"].widget.attrs.get("inputmode") == "numeric"
        assert (
            form.fields["discogs_id"].widget.attrs.get("placeholder")
            == "Например: [r1724093]"
        )

    def test_discogs_requires_any_identifier(self) -> None:
        form = RecordForm(
            data={
                "source": SOURCE_DISCOGS,
                "discogs_id": "",
                "barcode": "",
                "catalog_number": "",
            }
        )

        assert not form.is_valid()
        assert "__all__" in form.errors


class RecordFormDiscogsDuplicateFieldTests(TestCase):
    def test_discogs_id_accepts_plain_number(self) -> None:
        form = RecordForm(
            data={
                "source": SOURCE_DISCOGS,
                "discogs_id": "1724093",
                "barcode": "",
                "catalog_number": "",
            }
        )

        assert form.is_valid()
        assert form.cleaned_data["discogs_id"] == 1724093

    def test_discogs_id_accepts_bracket_release_notation(self) -> None:
        form = RecordForm(
            data={
                "source": SOURCE_DISCOGS,
                "discogs_id": "[r1724093]",
                "barcode": "",
                "catalog_number": "",
            }
        )

        assert form.is_valid()
        assert form.cleaned_data["discogs_id"] == 1724093

    def test_discogs_id_rejects_invalid_format(self) -> None:
        form = RecordForm(
            data={
                "source": SOURCE_DISCOGS,
                "discogs_id": "release-1724093",
                "barcode": "",
                "catalog_number": "",
            }
        )

        assert not form.is_valid()
        assert "discogs_id" in form.errors
        assert (
            "Укажите Discogs ID в формате 1724093 или [r1724093]."
            in form.errors["discogs_id"][0]
        )

    def test_duplicate_catalog_number_does_not_add_identifiers_required_error(
        self,
    ) -> None:
        Record.objects.create(title="Bleach", catalog_number="SP34")

        form = RecordForm(
            data={
                "source": SOURCE_DISCOGS,
                "discogs_id": "",
                "barcode": "",
                "catalog_number": "SP34",
            }
        )

        assert not form.is_valid()
        assert "catalog_number" in form.errors
        assert "__all__" not in form.errors

    def test_duplicate_barcode_does_not_add_identifiers_required_error(self) -> None:
        Record.objects.create(title="Bleach", barcode="098787003413")

        form = RecordForm(
            data={
                "source": SOURCE_DISCOGS,
                "discogs_id": "",
                "barcode": "098787003413",
                "catalog_number": "",
            }
        )

        assert not form.is_valid()
        assert "barcode" in form.errors
        assert "__all__" not in form.errors

    def test_duplicate_discogs_id_shows_russian_error(self) -> None:
        Record.objects.create(
            title="Bleach",
            discogs_id=9946068,
            release_year=1990,
            release_month=1,
            release_day=1,
        )

        form = RecordForm(
            data={
                "source": SOURCE_DISCOGS,
                "discogs_id": "9946068",
                "barcode": "",
                "catalog_number": "",
            }
        )

        assert not form.is_valid()
        assert "discogs_id" in form.errors
        assert "__all__" not in form.errors
        assert (
            'Запись с таким Discogs ID уже существует: "Bleach"'
            in form.errors["discogs_id"][0]
        )
