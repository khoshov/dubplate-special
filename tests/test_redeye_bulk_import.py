from __future__ import annotations

import pytest

from records.models import Record
from records.pipelines.redeye.bulk_import_from_redeye import RedeyeBulkImporter


@pytest.mark.django_db
def test_bulk_import_marks_duplicates_and_does_not_attach_genre_style(monkeypatch):
    importer = RedeyeBulkImporter()

    listing_url = "https://www.redeyerecords.co.uk/bass-music/pre-orders"
    product_url = "https://www.redeyerecords.co.uk/vinyl/1-test-product/"

    # Листинг возвращает один URL
    from records.scrapers import redeye_listing

    monkeypatch.setattr(
        redeye_listing.RedeyeListingScraper,
        "iter_product_urls",
        lambda self, url: iter([product_url]),
    )

    raw_payload = {
        "title": "Test title",
        "artists": ["Test artist"],
        "label": "Test label",
        "catalog_number": "CAT001",
        "tracks": [{"title": "A"}],
        "source": {"name": "redeye", "url": product_url},
        "image_url": None,
    }

    monkeypatch.setattr(
        importer.svc, "parse_redeye_product_by_url", lambda url: raw_payload
    )

    existing = Record.objects.create(title="Existing", catalog_number="CAT001")

    import_calls = []

    def fake_import_from_redeye(*, catalog_number, raw_payload, source_url, **kwargs):
        import_calls.append(
            {
                "catalog_number": catalog_number,
                "raw_payload": raw_payload,
                "source_url": source_url,
                **kwargs,
            }
        )
        return existing, False

    monkeypatch.setattr(importer.svc, "import_from_redeye", fake_import_from_redeye)

    attach_calls = []
    monkeypatch.setattr(
        importer,
        "_attach_single_choice",
        lambda *args, **kwargs: attach_calls.append((args, kwargs)),
    )

    results = list(
        importer.crawl_category(
            listing_url,
            attach_genre="Dubstep",
            attach_style="Deep",
            save=True,
        )
    )

    assert len(results) == 1
    res = results[0]
    assert res.ok is True
    assert res.created is False
    assert res.updated is False
    assert res.skipped_duplicate is True

    # bulk должен прокидывать URL + сырой payload в сервис импорта
    assert len(import_calls) == 1
    assert import_calls[0]["catalog_number"] == "CAT001"
    assert import_calls[0]["raw_payload"] is raw_payload
    assert import_calls[0]["source_url"] == product_url

    # при дубле жанр/стиль не должны навешиваться
    assert attach_calls == []


@pytest.mark.django_db
def test_bulk_import_created_record_attaches_genre_and_style(monkeypatch):
    importer = RedeyeBulkImporter()

    listing_url = "https://www.redeyerecords.co.uk/bass-music/pre-orders"
    product_url = "https://www.redeyerecords.co.uk/vinyl/2-test-product/"

    from records.scrapers import redeye_listing

    monkeypatch.setattr(
        redeye_listing.RedeyeListingScraper,
        "iter_product_urls",
        lambda self, url: iter([product_url]),
    )

    raw_payload = {
        "title": "Test title 2",
        "artists": ["Test artist 2"],
        "label": "Test label 2",
        "catalog_number": "CAT002",
        "tracks": [{"title": "A"}],
        "source": {"name": "redeye", "url": product_url},
        "image_url": None,
    }

    monkeypatch.setattr(
        importer.svc, "parse_redeye_product_by_url", lambda url: raw_payload
    )

    created_record = Record.objects.create(title="Created", catalog_number="CAT002")

    def fake_import_from_redeye(*, catalog_number, raw_payload, source_url, **kwargs):
        return created_record, True

    monkeypatch.setattr(importer.svc, "import_from_redeye", fake_import_from_redeye)

    attach_calls = []
    monkeypatch.setattr(
        importer,
        "_attach_single_choice",
        lambda *args, **kwargs: attach_calls.append((args, kwargs)),
    )

    results = list(
        importer.crawl_category(
            listing_url,
            attach_genre="Dubstep",
            attach_style="Deep",
            save=True,
        )
    )

    assert len(results) == 1
    res = results[0]
    assert res.ok is True
    assert res.created is True
    assert res.updated is False
    assert res.skipped_duplicate is False

    # жанр + стиль должны навеситься (2 вызова)
    assert len(attach_calls) == 2
