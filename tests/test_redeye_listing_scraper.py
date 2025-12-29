from django.core.management import call_command
from django.core.management.base import CommandError
import pytest

from records.scrapers.redeye_listing import RedeyeListingScraper


class DummyHTTPClient:
    def __init__(self, html_by_url):
        self.html_by_url = html_by_url
        self.calls = []

    def get_text(self, url, *, referer=None, slow=False):
        self.calls.append((url, referer, slow))
        return self.html_by_url.get(url, "")


def _listing_html():
    return """
    <html>
      <body>
        <div id="pageLinks"></div>
        <a href="/vinyl/123-abc">A</a>
        <a href="/downloads/456-def">B</a>
      </body>
    </html>
    """


def test_redeye_listing_scraper_uses_client_and_limit():
    url = "https://www.redeyerecords.co.uk/bass-music/pre-orders"
    client = DummyHTTPClient({url: _listing_html()})
    scraper = RedeyeListingScraper(http=client)

    links = list(scraper.iter_product_urls(url, limit=1))

    assert len(links) == 1
    assert links[0].startswith("https://www.redeyerecords.co.uk/")
    assert client.calls[0][0] == url


def test_redeye_listing_scraper_returns_all_when_no_limit():
    url = "https://www.redeyerecords.co.uk/bass-music/pre-orders"
    client = DummyHTTPClient({url: _listing_html()})
    scraper = RedeyeListingScraper(http=client)

    links = list(scraper.iter_product_urls(url))

    assert len(links) == 2
    assert links[0] != links[1]


def test_parse_redeye_rejects_removed_flags():
    with pytest.raises(CommandError):
        call_command("parse_redeye", "--delay", "1")
