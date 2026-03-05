from records.services.audio.providers.youtube_audio_enrichment import (
    YouTubeAudioEnrichmentProvider,
)


def test_retry_policy_succeeds_on_third_attempt():
    attempts_state = {"count": 0}
    sleep_calls: list[float] = []

    def operation():
        attempts_state["count"] += 1
        if attempts_state["count"] < 3:
            raise RuntimeError("temporary source error")
        return "media/track.mp3"

    result, attempts, last_error = YouTubeAudioEnrichmentProvider.download_with_retry(
        operation=operation,
        max_attempts=3,
        base_delay_sec=1.0,
        sleep_func=lambda delay: sleep_calls.append(delay),
    )

    assert result == "media/track.mp3"
    assert attempts == 3
    assert last_error is None
    assert sleep_calls == [1.0, 2.0]


def test_retry_policy_returns_terminal_failed_after_three_attempts():
    sleep_calls: list[float] = []

    def operation():
        raise RuntimeError("source unavailable")

    result, attempts, last_error = YouTubeAudioEnrichmentProvider.download_with_retry(
        operation=operation,
        max_attempts=3,
        base_delay_sec=1.0,
        sleep_func=lambda delay: sleep_calls.append(delay),
    )

    assert result is None
    assert attempts == 3
    assert isinstance(last_error, RuntimeError)
    assert "source unavailable" in str(last_error)
    assert sleep_calls == [1.0, 2.0]
