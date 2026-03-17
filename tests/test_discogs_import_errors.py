import pytest

from records.models import Record
from records.services.providers.discogs.discogs_service import (
    DiscogsApiError,
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

    def get_release(self, discogs_id):
        raise DiscogsNotFoundError("Релиз не найден")


class DiscogsApiStub:
    def search_by_barcode(self, barcode):
        raise DiscogsApiError("Discogs API вернул ошибку HTTP 500.")

    def search_by_catalog_number(self, catalog_number):
        raise DiscogsApiError("Discogs API вернул ошибку HTTP 500.")

    def get_release(self, discogs_id):
        raise DiscogsApiError("Discogs API вернул ошибку HTTP 500.")


class DiscogsReleaseStub:
    def __init__(self, release):
        self.release = release

    def search_by_barcode(self, barcode):
        return self.release

    def search_by_catalog_number(self, catalog_number):
        return self.release

    def get_release(self, discogs_id):
        return self.release


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
def test_record_service_import_from_discogs_handles_not_found_by_discogs_id():
    service = RecordService(
        discogs_service=DiscogsNotFoundStub(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    with pytest.raises(
        ValueError,
        match="Релиз с таким Discogs ID не найден. Попробуйте добавить по barecode.",
    ):
        service.import_from_discogs(discogs_id=123456)


@pytest.mark.django_db
def test_record_service_import_from_discogs_handles_api_error_for_catalog_number():
    service = RecordService(
        discogs_service=DiscogsApiStub(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    with pytest.raises(
        ValueError,
        match="Ошибка при импорте по каталожному номеру. Попробуйте добавить по barecode или по Discogs ID.",
    ):
        service.import_from_discogs(catalog_number="SP34")


@pytest.mark.django_db
def test_record_service_import_from_discogs_handles_api_error_for_discogs_id():
    service = RecordService(
        discogs_service=DiscogsApiStub(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    with pytest.raises(
        ValueError,
        match="Ошибка при импорте по Discogs ID. Попробуйте добавить по barecode.",
    ):
        service.import_from_discogs(discogs_id=123456)


@pytest.mark.django_db
def test_record_service_import_from_discogs_returns_existing_on_payload_barcode_duplicate(
    monkeypatch,
):
    existing = Record.objects.create(
        title="Existing barcode",
        barcode="098787003413",
        release_year=1990,
        release_month=1,
        release_day=1,
    )
    release = object()
    service = RecordService(
        discogs_service=DiscogsReleaseStub(release),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    monkeypatch.setattr(
        "records.services.record_service.adapt_discogs_release",
        lambda _release: {
            "title": "Discogs title",
            "discogs_id": 1724093,
            "barcode": "098787003413",
            "catalog_number": "SP34",
            "release_year": 1990,
            "release_month": 1,
            "release_day": 1,
            "artists": [],
            "genres": [],
            "styles": [],
            "formats": [],
            "tracks": [],
        },
    )

    record, created = service.import_from_discogs(barcode="0 98787-0034-1 3")

    existing.refresh_from_db()
    assert created is False
    assert record.pk == existing.pk
    assert existing.discogs_id is None


@pytest.mark.django_db
def test_record_service_import_from_discogs_returns_existing_on_payload_catalog_duplicate(
    monkeypatch,
):
    existing = Record.objects.create(
        title="Existing catno",
        catalog_number="SP34",
        release_year=1990,
        release_month=1,
        release_day=1,
    )
    release = object()
    service = RecordService(
        discogs_service=DiscogsReleaseStub(release),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    monkeypatch.setattr(
        "records.services.record_service.adapt_discogs_release",
        lambda _release: {
            "title": "Discogs title",
            "discogs_id": 1724093,
            "barcode": "098787003413",
            "catalog_number": "SP34",
            "release_year": 1990,
            "release_month": 1,
            "release_day": 1,
            "artists": [],
            "genres": [],
            "styles": [],
            "formats": [],
            "tracks": [],
        },
    )

    record, created = service.import_from_discogs(catalog_number="SP 34")

    existing.refresh_from_db()
    assert created is False
    assert record.pk == existing.pk
    assert existing.discogs_id == 1724093


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
