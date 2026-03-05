from __future__ import annotations

from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from records.constants import SOURCE_REDEYE
from records.forms import RecordForm


class DummyRecord:
    def __init__(self, pk: int = 1) -> None:
        self.pk = pk
        self.saved = False

    def save(self) -> None:
        self.saved = True


class RecordFormRedeyeImportTests(SimpleTestCase):
    def test_redeye_requires_source_url_or_catalog_number(self) -> None:
        form = RecordForm(
            data={
                "source": SOURCE_REDEYE,
                "source_url": "",
                "barcode": "",
                "catalog_number": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("source_url", form.errors)

    def test_save_redeye_uses_parse_and_import_by_url(self) -> None:
        source_url = "https://www.redeyerecords.co.uk/vinyl/12345-test-release"
        payload = {"catalog_number": "RT999"}
        record = DummyRecord(pk=77)

        form = RecordForm()
        form.cleaned_data = {
            "source": SOURCE_REDEYE,
            "source_url": source_url,
            "catalog_number": "",
            "barcode": "",
        }

        with (
            patch.object(
                form.record_service,
                "parse_redeye_product_by_url",
                return_value=payload,
            ) as parse_mock,
            patch.object(
                form.record_service,
                "import_from_redeye",
                return_value=(record, True),
            ) as import_mock,
        ):
            saved_record = form.save()

        self.assertIs(saved_record, record)
        parse_mock.assert_called_once_with(source_url)
        import_mock.assert_called_once_with(
            catalog_number="RT999",
            raw_payload=payload,
            source_url=source_url,
        )

    def test_redeye_invalid_record_shows_source_url_error(self) -> None:
        source_url = "https://www.redeyerecords.co.uk/vinyl/unknown-record"
        form = RecordForm(
            data={
                "source": SOURCE_REDEYE,
                "source_url": source_url,
                "barcode": "",
                "catalog_number": "",
            }
        )

        with patch.object(
            form.record_service,
            "parse_redeye_product_by_url",
            return_value={},
        ):
            self.assertFalse(form.is_valid())

        self.assertIn("source_url", form.errors)
        self.assertIn(RecordForm.REDEYE_URL_NOT_FOUND_ERROR, form.errors["source_url"])

    def test_save_redeye_raises_when_catalog_number_missing(self) -> None:
        source_url = "https://www.redeyerecords.co.uk/vinyl/12345-test-release"
        form = RecordForm()
        form.cleaned_data = {
            "source": SOURCE_REDEYE,
            "source_url": source_url,
            "catalog_number": "",
            "barcode": "",
        }

        with patch.object(
            form.record_service,
            "parse_redeye_product_by_url",
            return_value={},
        ):
            with self.assertRaises(ValidationError) as exc:
                form.save()

        self.assertIn("source_url", exc.exception.message_dict)

    def test_save_redeye_uses_catalog_number_when_url_empty(self) -> None:
        record = DummyRecord(pk=78)
        form = RecordForm()
        form.cleaned_data = {
            "source": SOURCE_REDEYE,
            "source_url": "",
            "catalog_number": "rt001",
            "barcode": "",
        }

        with (
            patch.object(
                form.record_service,
                "parse_redeye_product_by_url",
            ) as parse_mock,
            patch.object(
                form.record_service,
                "import_from_redeye",
                return_value=(record, True),
            ) as import_mock,
        ):
            saved_record = form.save()

        self.assertIs(saved_record, record)
        parse_mock.assert_not_called()
        import_mock.assert_called_once_with(
            catalog_number="RT001",
            source_url=None,
        )
