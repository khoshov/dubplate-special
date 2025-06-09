import pytest
from records.forms import RecordForm
from records.models import Record
from records.services.discogs_service import DiscogsService


@pytest.fixture
def form_data():
    return {
        "barcode": "123456789012",
    }


@pytest.mark.django_db
def test_record_form_clean_barcode(form_data):
    form = RecordForm(data=form_data)
    assert form.is_valid()

    # Тест короткого штрих-кода
    form_data["barcode"] = "123"
    form = RecordForm(data=form_data)
    assert not form.is_valid()
    assert "barcode" in form.errors


@pytest.mark.django_db
def test_record_form_save_new(form_data, mocker):
    mock_import = mocker.patch.object(
        DiscogsService,
        "import_release_by_barcode",
        return_value=Record(barcode="123456789012"),
    )

    form = RecordForm(data=form_data)
    assert form.is_valid()

    instance = form.save()
    mock_import.assert_called_once()
    assert instance.barcode == "123456789012"
