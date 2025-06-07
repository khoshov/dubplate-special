import requests
from unittest.mock import patch, MagicMock
from django.test import TestCase

from config import settings
from records.models import Record, Artist, Label, Genre, Style, Track
from records.services.discogs_service import DiscogsService


class DiscogsServiceTestCase(TestCase):
    def setUp(self):
        self.service = DiscogsService()
        self.record = Record.objects.create(
            barcode="123456789",
            catalog_number="TEST001",
            condition="M",
            discogs_id=12345
        )

        # Реалистичные мок-объекты вместо MagicMock
        class MockArtist:
            id = 1
            name = "Test Artist"

        class MockLabel:
            id = 1
            name = "Test Label"
            catno = "LAB001"

        self.mock_release = MagicMock(
            title="Test Album",
            year=2020,
            id=12345,
            notes="Test notes",
            country="US",
            labels=[MockLabel()],
            artists=[MockArtist()],
            genres=["Rock"],
            styles=["Alternative"],
            formats=[{"qty": 1, "descriptions": ["LP", "Album"]}],
            tracklist=[MagicMock(position="A1", title="Test Track", duration="3:45")],
            images=[{"uri": "http://example.com/cover.jpg"}]
        )

    @patch('discogs_client.Client')
    def test_init(self, mock_client):
        service = DiscogsService()
        mock_client.assert_called_once_with(
            user_agent=settings.DISCOGS_USER_AGENT,
            user_token=settings.DISCOGS_TOKEN
        )

    @patch('records.services.discogs_service.DiscogsService._make_request')
    def test_get_release_by_barcode(self, mock_make_request):
        mock_make_request.return_value = [self.mock_release]
        release = self.service._get_release_by_barcode("123456789")
        self.assertEqual(release.title, "Test Album")

    @patch('records.services.discogs_service.DiscogsService._get_release_by_barcode')
    @patch('records.services.discogs_service.DiscogsService._process_release_data')
    def test_import_release_by_barcode(self, mock_process, mock_get_release):
        mock_get_release.return_value = self.mock_release
        mock_process.return_value = self.record

        result = self.service.import_release_by_barcode("123456789", self.record)
        self.assertEqual(result, self.record)

    def test_process_artist(self):
        artist = self.service._process_artist(self.mock_release.artists[0])
        self.assertEqual(artist.name, "Test Artist")
        self.assertEqual(Artist.objects.count(), 1)

    def test_process_label(self):
        label = self.service._process_label(self.mock_release.labels[0])
        self.assertEqual(label.name, "Test Label")
        self.assertEqual(Label.objects.count(), 1)

    def test_process_genre(self):
        genre = self.service._process_genre("Rock")
        self.assertEqual(genre.name, "Rock")
        self.assertEqual(Genre.objects.count(), 1)

    def test_process_style(self):
        style = self.service._process_style("Alternative")
        self.assertEqual(style.name, "Alternative")
        self.assertEqual(Style.objects.count(), 1)

    def test_process_tracks(self):
        self.service._process_tracks(self.record, self.mock_release.tracklist)
        self.assertEqual(Track.objects.count(), 1)

    def test_determine_formats(self):
        formats = self.service._determine_formats(self.mock_release.formats)
        self.assertEqual(len(formats), 2)
        self.assertEqual({f.name for f in formats}, {"LP", "ALBUM"})

    @patch('requests.get')
    def test_download_cover_image_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'test_image_data'
        mock_get.return_value = mock_response

        self.service._download_cover_image(self.record, "http://example.com/cover.jpg")

        self.assertTrue(hasattr(self.record, 'cover_image'))
        if hasattr(self.record, 'cover_image'):
            self.record.cover_image.close()  # Освобождаем ресурсы

    @patch('requests.get')
    def test_download_cover_image_failure(self, mock_get):
        mock_get.side_effect = requests.exceptions.RequestException("Error")

        with self.assertLogs(level='ERROR') as log:
            self.service._download_cover_image(self.record, "http://example.com/cover.jpg")

        self.assertIn("Error downloading cover", log.output[0])

    @patch('records.services.discogs_service.DiscogsService._download_cover_image')
    def test_process_release_data(self, mock_download):
        self.service._process_release_data(self.mock_release, self.record, True)
        self.assertEqual(self.record.title, "Test Album")
        mock_download.assert_called_once()