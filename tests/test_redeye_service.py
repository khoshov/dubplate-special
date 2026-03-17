import pytest

from records.services.providers.redeye.redeye_service import RedeyeService


class DummyHTTPClient:
    def __init__(self, html_by_url):
        self.html_by_url = html_by_url
        self.calls = []

    def get_text(self, url, *, referer=None, slow=False):
        self.calls.append((url, referer, slow))
        return self.html_by_url.get(url, "")


class DummyParser:
    def __init__(self, payload_by_url):
        self.payload_by_url = payload_by_url

    def parse(self, url, html_text):
        return dict(self.payload_by_url.get(url, {}))


def test_fetch_by_catalog_number_uses_exact_match_from_candidates():
    search_url = "https://www.redeyerecords.co.uk/search/?searchType=CAT&keywords=SP34"
    first_url = "https://www.redeyerecords.co.uk/vinyl/111-first"
    second_url = "https://www.redeyerecords.co.uk/vinyl/222-second"

    http = DummyHTTPClient(
        {
            search_url: (
                '<a href="/vinyl/111-first">One</a><a href="/vinyl/222-second">Two</a>'
            ),
            first_url: "<html></html>",
            second_url: "<html></html>",
        }
    )
    service = RedeyeService(http=http)
    service.parser = DummyParser(
        {
            first_url: {"catalog_number": "SP034"},
            second_url: {"catalog_number": "SP34"},
        }
    )

    result = service.fetch_by_catalog_number("SP34")

    assert result.source_url == second_url
    assert result.payload["catalog_number"] == "SP34"
    assert [url for url, _, _ in http.calls] == [search_url, first_url, second_url]


def test_fetch_by_catalog_number_raises_when_exact_match_not_found():
    search_url = "https://www.redeyerecords.co.uk/search/?searchType=CAT&keywords=SP34"
    first_url = "https://www.redeyerecords.co.uk/vinyl/111-first"

    http = DummyHTTPClient(
        {
            search_url: '<a href="/vinyl/111-first">One</a>',
            first_url: "<html></html>",
        }
    )
    service = RedeyeService(http=http)
    service.parser = DummyParser({first_url: {"catalog_number": "SP034"}})

    with pytest.raises(ValueError, match="точным совпадением каталожного номера"):
        service.fetch_by_catalog_number("SP34")
