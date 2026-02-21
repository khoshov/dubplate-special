import pytest

from records.models import Record
from records.services.providers.discogs.discogs_service import (
    DiscogsAuthError,
    DiscogsConfigError,
    DiscogsNotFoundError,
    DiscogsService,
)
from records.services.record_service import RecordService


class DummyImageService:
    def download_cover(self, record, url):
        return False


class DummyRedeyeService:
    pass


class DummyAudioService:
    pass


class DiscogsConfigStub:
    def search_by_barcode(self, barcode):
        raise DiscogsConfigError(
            "Импорт из Discogs недоступен: не задан API-ключ (DISCOGS_API_KEY)."
        )

    def search_by_catalog_number(self, catalog_number):
        raise DiscogsConfigError(
            "Импорт из Discogs недоступен: не задан API-ключ (DISCOGS_API_KEY)."
        )


class DiscogsAuthStub:
    def search_by_barcode(self, barcode):
        raise DiscogsAuthError("Ошибка авторизации Discogs.")

    def search_by_catalog_number(self, catalog_number):
        raise DiscogsAuthError("Ошибка авторизации Discogs.")

    def get_release(self, discogs_id):
        raise DiscogsAuthError("Ошибка авторизации Discogs.")


class DiscogsNotFoundStub:
    def search_by_barcode(self, barcode):
        raise DiscogsNotFoundError("Релиз не найден")

    def search_by_catalog_number(self, catalog_number):
        raise DiscogsNotFoundError("Релиз не найден")


@pytest.mark.django_db
def test_discogs_service_missing_token_raises_config_error(settings):
    settings.DISCOGS_TOKEN = ""
    settings.DISCOGS_USER_AGENT = "test-agent/1.0"

    service = DiscogsService()

    with pytest.raises(DiscogsConfigError, match="API-ключ"):
        service.search_by_barcode("123456789")


@pytest.mark.django_db
def test_record_service_import_from_discogs_handles_config_error():
    service = RecordService(
        discogs_service=DiscogsConfigStub(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    with pytest.raises(ValueError, match="API-ключ"):
        service.import_from_discogs(barcode="123456789")


@pytest.mark.django_db
def test_record_service_import_from_discogs_handles_auth_error():
    service = RecordService(
        discogs_service=DiscogsAuthStub(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    with pytest.raises(ValueError, match="авторизоваться"):
        service.import_from_discogs(barcode="123456789")


@pytest.mark.django_db
def test_record_service_import_from_discogs_handles_not_found():
    service = RecordService(
        discogs_service=DiscogsNotFoundStub(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    with pytest.raises(ValueError, match="Релиз не найден"):
        service.import_from_discogs(barcode="000000000")


@pytest.mark.django_db
def test_record_service_update_from_discogs_handles_auth_error():
    service = RecordService(
        discogs_service=DiscogsAuthStub(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )
    record = Record.objects.create(
        title="R1",
        discogs_id=123,
        release_year=2000,
        release_month=1,
        release_day=1,
    )

    with pytest.raises(ValueError, match="авторизоваться"):
        service.update_from_discogs(record)
