import pytest
from django.db import IntegrityError

from records.models import (
    AudioEnrichmentJob,
    AudioEnrichmentJobRecord,
    Record,
    RecordSource,
)
from records.services.record_service import RecordService
from records.services import record_service as record_service_module
from records.services.audio.providers.redeye.redeye_audio_player import (
    attach_audio_from_redeye_player,
)


class DummyDiscogsService:
    pass


class DummyImageService:
    def download_cover(self, record, url):
        return False


class DummyRedeyeService:
    def __init__(self, source_url: str | None = None):
        self.source_url = source_url
        self.calls: list[str] = []

    def fetch_by_catalog_number(self, catalog_number):
        self.calls.append(catalog_number)
        return type("Result", (), {"payload": {}, "source_url": self.source_url})()


class DummyAudioService:
    def attach_audio_from_redeye(self, *args, **kwargs):
        raise AssertionError("attach_audio_from_redeye не должен вызываться")


class RecordingAudioService:
    def __init__(self, updated_count: int = 1):
        self.updated_count = updated_count
        self.calls: list[dict] = []

    def attach_audio_from_redeye(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self.updated_count


@pytest.mark.django_db
def test_import_from_redeye_enqueues_audio_job_for_new_record(monkeypatch):
    captured_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(
        record_service_module.transaction,
        "on_commit",
        lambda callback: callback(),
    )
    monkeypatch.setattr(
        record_service_module.run_redeye_audio_enrichment_job,
        "delay",
        lambda payload: captured_payloads.append(payload),
    )

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_redeye(
        catalog_number="RT900",
        raw_payload={"title": "Redeye import", "tracks": [{"title": "Track 1"}]},
        download_audio_decision=True,
    )

    job = AudioEnrichmentJob.objects.get()
    job_record = AudioEnrichmentJobRecord.objects.get(job=job, record=record)

    assert created is True
    assert job_record.operation_name == "Импорт релиза из Redeye"
    assert job_record.release_source_name == "Redeye"
    assert job_record.audio_source_summary == "Redeye"
    assert job_record.result == "Релиз создан"
    assert captured_payloads == [
        {
            "job_id": record._redeye_enrichment_job_id,
            "record_ids": [record.id],
            "overwrite_existing": False,
            "requested_by_user_id": None,
            "source": "redeye_import",
        }
    ]


@pytest.mark.django_db
def test_import_from_redeye_duplicate_catalog_number_returns_existing():
    existing = Record.objects.create(title="R1", catalog_number="RT123")

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_redeye(
        catalog_number="rt123", download_audio_decision=False
    )

    assert created is False
    assert record.pk == existing.pk


@pytest.mark.django_db
def test_import_from_redeye_integrity_error_returns_existing(monkeypatch):
    existing = Record.objects.create(title="R1", catalog_number="RT123")

    def raise_integrity(_payload):
        raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(
        record_service_module, "build_record_from_payload", raise_integrity
    )

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_redeye(
        catalog_number="RT123", download_audio_decision=False
    )

    assert created is False
    assert record.pk == existing.pk


@pytest.mark.django_db
def test_import_from_redeye_strips_empty_structured_formats_from_payload(monkeypatch):
    captured: dict[str, object] = {}

    def fake_adapt(_raw_payload):
        return {
            "title": "Redeye import",
            "formats": ["CD"],
            "structured_formats": [],
        }

    def fake_build(payload):
        captured["payload"] = dict(payload)
        return Record.objects.create(
            title=payload["title"],
            catalog_number=payload["catalog_number"],
        )

    monkeypatch.setattr(record_service_module, "adapt_redeye_payload", fake_adapt)
    monkeypatch.setattr(record_service_module, "build_record_from_payload", fake_build)

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_redeye(
        catalog_number="RT900",
        raw_payload={},
        download_audio_decision=False,
    )

    assert created is True
    assert record.catalog_number == "RT900"
    assert captured["payload"] == {
        "title": "Redeye import",
        "formats": ["Not specified"],
        "catalog_number": "RT900",
    }


@pytest.mark.django_db
def test_import_from_redeye_existing_record_resolves_source_and_enqueues_audio_job(
    monkeypatch,
):
    existing = Record.objects.create(title="R1", catalog_number="RT123")
    captured_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(
        record_service_module.transaction,
        "on_commit",
        lambda callback: callback(),
    )
    monkeypatch.setattr(
        record_service_module.run_redeye_audio_enrichment_job,
        "delay",
        lambda payload: captured_payloads.append(payload),
    )

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(
            source_url="www.redeyerecords.co.uk/vinyl/191836-test-release"
        ),
        image_service=DummyImageService(),
        audio_service=DummyAudioService(),
    )

    record, created = service.import_from_redeye(
        catalog_number="rt123", download_audio_decision=True
    )

    job = AudioEnrichmentJob.objects.get()
    job_record = AudioEnrichmentJobRecord.objects.get(job=job, record=existing)

    assert created is False
    assert record.pk == existing.pk
    assert job_record.operation_name == "Импорт релиза из Redeye"
    assert job_record.result == "Релиз уже существует"
    assert captured_payloads == [
        {
            "job_id": record._redeye_enrichment_job_id,
            "record_ids": [existing.id],
            "overwrite_existing": False,
            "requested_by_user_id": None,
            "source": "redeye_import",
        }
    ]

    source = existing.sources.get(
        provider=RecordSource.Provider.REDEYE,
        role=RecordSource.Role.PRODUCT_PAGE,
    )
    assert source.url == "https://www.redeyerecords.co.uk/vinyl/191836-test-release"
    assert source.can_fetch_audio is True


@pytest.mark.django_db
def test_attach_audio_from_redeye_normalizes_malformed_redeye_source_url():
    record = Record.objects.create(title="R1", catalog_number="RT123")
    RecordSource.objects.create(
        record=record,
        provider=RecordSource.Provider.REDEYE,
        role=RecordSource.Role.PRODUCT_PAGE,
        url="https://www.redeyerecords.co.uk/www.redeyerecords.co.uk/vinyl/191836-test-release",
        can_fetch_audio=True,
    )
    audio = RecordingAudioService(updated_count=3)

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(),
        image_service=DummyImageService(),
        audio_service=audio,
    )

    updated = service.attach_audio_from_redeye(record, force=False)

    assert updated == 3
    assert len(audio.calls) == 1
    assert (
        audio.calls[0]["page_url"]
        == "https://www.redeyerecords.co.uk/vinyl/191836-test-release"
    )

    source = record.sources.get(
        provider=RecordSource.Provider.REDEYE,
        role=RecordSource.Role.PRODUCT_PAGE,
    )
    assert source.url == "https://www.redeyerecords.co.uk/vinyl/191836-test-release"


@pytest.mark.django_db
def test_attach_audio_from_redeye_raises_when_strict_source_required():
    record = Record.objects.create(title="R1", catalog_number="SP34")
    audio = RecordingAudioService(updated_count=3)

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=DummyRedeyeService(source_url=None),
        image_service=DummyImageService(),
        audio_service=audio,
    )

    with pytest.raises(
        ValueError,
        match="Обновление из Redeye невозможно: не найден релиз с точным совпадением каталожного номера 'SP34'",
    ):
        service.attach_audio_from_redeye(record, force=False, require_source=True)


@pytest.mark.django_db
def test_attach_audio_from_redeye_ignores_none_like_catalog_number_in_strict_mode():
    record = Record.objects.create(title="R1", catalog_number=" none ")
    audio = RecordingAudioService(updated_count=3)
    redeye = DummyRedeyeService(
        source_url="www.redeyerecords.co.uk/vinyl/191836-test-release"
    )

    service = RecordService(
        discogs_service=DummyDiscogsService(),
        redeye_service=redeye,
        image_service=DummyImageService(),
        audio_service=audio,
    )

    with pytest.raises(
        ValueError,
        match="Обновление из Redeye невозможно: у записи отсутствует каталожный номер",
    ):
        service.attach_audio_from_redeye(record, force=False, require_source=True)

    assert redeye.calls == []
    assert audio.calls == []


@pytest.mark.django_db
def test_attach_audio_from_redeye_player_sets_track_audio_source(monkeypatch):
    record = Record.objects.create(title="R1", catalog_number="RT123")
    track = record.tracks.create(title="Track 1", position_index=1)

    monkeypatch.setattr(
        "records.services.audio.providers.redeye.redeye_audio_player.collect_redeye_audio_urls",
        lambda *args, **kwargs: ["https://example.com/audio.mp3"],
    )
    monkeypatch.setattr(
        "records.services.audio.providers.redeye.redeye_audio_player.download_audio_to_track",
        lambda *args, **kwargs: "records/track/audio_preview/1/test.mp3",
    )

    updated = attach_audio_from_redeye_player(
        record,
        page_url="https://www.redeyerecords.co.uk/vinyl/123-test-release",
        force=False,
    )

    track.refresh_from_db()

    assert updated == 1
    assert track.audio_source == track.AudioSource.REDEYE
