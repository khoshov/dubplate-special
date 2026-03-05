from records.services.audio.providers.youtube_audio_enrichment import (
    YouTubeAudioEnrichmentProvider,
)


def test_strict_match_requires_title_and_at_least_one_artist():
    matched, title_ok, artist_ok = YouTubeAudioEnrichmentProvider.strict_match(
        track_title="Track Name",
        candidate_title="track name",
        track_artists=["Artist A", "Artist B"],
        candidate_artists=["Artist B"],
    )

    assert matched is True
    assert title_ok is True
    assert artist_ok is True


def test_strict_match_fails_when_title_does_not_match():
    matched, title_ok, artist_ok = YouTubeAudioEnrichmentProvider.strict_match(
        track_title="Track Name",
        candidate_title="Different Name",
        track_artists=["Artist A"],
        candidate_artists=["Artist A"],
    )

    assert matched is False
    assert title_ok is False
    assert artist_ok is True


def test_strict_match_fails_when_no_artist_overlap():
    matched, title_ok, artist_ok = YouTubeAudioEnrichmentProvider.strict_match(
        track_title="Track Name",
        candidate_title="Track Name",
        track_artists=["Artist A"],
        candidate_artists=["Artist X"],
    )

    assert matched is False
    assert title_ok is True
    assert artist_ok is False
