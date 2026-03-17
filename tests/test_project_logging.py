from __future__ import annotations

import logging

from config.logging import (
    NOTICE_LEVEL,
    ProjectColorTextFormatter,
    ProjectJSONFormatter,
    ProjectTextFormatter,
    build_log_extra,
    build_logging_config,
)


def test_build_log_extra_filters_empty_values() -> None:
    payload = build_log_extra(
        component="youtube_audio",
        event="track_skip",
        job_id="job-1",
        record_id=101,
        empty_value="",
        none_value=None,
    )

    assert payload == {
        "component": "youtube_audio",
        "event": "track_skip",
        "log_context": {
            "job_id": "job-1",
            "record_id": 101,
        },
    }


def test_project_text_formatter_renders_structured_context() -> None:
    formatter = ProjectTextFormatter()
    record = logging.makeLogRecord(
        {
            "name": "records.services.tasks",
            "levelno": NOTICE_LEVEL,
            "levelname": "NOTICE",
            "msg": "Трек пропущен: локальный mp3 уже прикреплён.",
            "component": "youtube_audio",
            "event": "track_skip",
            "log_context": {
                "job_id": "job-1",
                "record_id": 1943,
                "track_id": 11275,
                "overwrite": False,
            },
        }
    )

    rendered = formatter.format(record)

    assert "250 NOTICE" in rendered
    assert "[component - youtube_audio]" in rendered
    assert "[event - track_skip]" in rendered
    assert "[job_id - job-1]" in rendered
    assert "[record_id - 1943]" in rendered
    assert "[track_id - 11275]" in rendered
    assert "[overwrite - false]" in rendered
    assert "Трек пропущен: локальный mp3 уже прикреплён." in rendered


def test_project_color_text_formatter_forces_ansi_colors() -> None:
    formatter = ProjectColorTextFormatter(use_color="true")
    record = logging.makeLogRecord(
        {
            "name": "records.services.tasks",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "Запущена задача.",
            "component": "youtube_audio",
            "event": "job_start",
        }
    )

    rendered = formatter.format(record)

    assert "\033[" in rendered
    assert "INFO" in rendered


def test_project_json_formatter_and_config_builder() -> None:
    formatter = ProjectJSONFormatter()
    record = logging.makeLogRecord(
        {
            "name": "records.services.tasks",
            "levelno": logging.WARNING,
            "levelname": "WARNING",
            "msg": "Не удалось обновить YouTube-сессию.",
            "component": "youtube_session",
            "event": "refresh_failed",
            "log_context": {"record_id": 1943, "track_id": 11275},
        }
    )

    rendered = formatter.format(record)
    config = build_logging_config(
        app_level="INFO",
        django_level="WARNING",
        log_format="json",
        destination="stdout",
        color="auto",
        file_path="logs/app.log",
    )

    assert '"component": "youtube_session"' in rendered
    assert '"event": "refresh_failed"' in rendered
    assert '"record_id": 1943' in rendered
    assert config["loggers"]["records"]["level"] == logging.INFO
    assert config["handlers"]["console"]["formatter"] == "json"
