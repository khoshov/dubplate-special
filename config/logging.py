from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

NOTICE_LEVEL = 250
_DEFAULT_LOG_LEVEL = "INFO"
_DEFAULT_LOG_FORMAT = "text"
_DEFAULT_LOG_DESTINATION = "stdout"
_DEFAULT_LOG_COLOR = "auto"
_DEFAULT_CONTEXT_FIELD_ORDER = (
    "job_id",
    "record_id",
    "track_id",
    "source",
    "overwrite",
    "status",
    "reason",
    "attempts",
    "previous_audio_present",
    "old_audio",
    "new_audio",
    "duration_before",
    "duration_after",
    "youtube_url",
    "requested_by_user_id",
    "task_id",
)
_LEVEL_NUMBERS = {
    logging.DEBUG: 100,
    logging.INFO: 200,
    NOTICE_LEVEL: 250,
    logging.WARNING: 300,
    logging.ERROR: 400,
    logging.CRITICAL: 500,
}
_COLOR_CODES = {
    "DEBUG": "\033[94m",
    "INFO": "\033[92m",
    "NOTICE": "\033[96m",
    "WARN": "\033[93m",
    "ERROR": "\033[91m",
    "CRITICAL": "\033[95m",
    "RESET": "\033[0m",
}


def register_notice_level() -> None:
    """Регистрирует пользовательский уровень NOTICE и метод logger.notice."""
    logging.NOTICE = NOTICE_LEVEL  # type: ignore[attr-defined]
    logging.addLevelName(NOTICE_LEVEL, "NOTICE")

    if hasattr(logging.Logger, "notice"):
        return

    def notice(self: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
        if self.isEnabledFor(NOTICE_LEVEL):
            self._log(NOTICE_LEVEL, message, args, **kwargs)

    logging.Logger.notice = notice  # type: ignore[attr-defined]


register_notice_level()


def normalize_log_level(raw_value: str | int | None) -> int:
    """Нормализует строковое значение уровня логирования в integer-level."""
    if isinstance(raw_value, int):
        return raw_value

    normalized = str(raw_value or _DEFAULT_LOG_LEVEL).strip().upper()
    if normalized == "WARN":
        normalized = "WARNING"

    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "NOTICE": NOTICE_LEVEL,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return mapping.get(normalized, logging.INFO)


def normalize_log_format(raw_value: str | None) -> str:
    """Возвращает поддерживаемый формат логирования."""
    normalized = str(raw_value or _DEFAULT_LOG_FORMAT).strip().lower()
    if normalized not in {"text", "json"}:
        return _DEFAULT_LOG_FORMAT
    return normalized


def normalize_log_destination(raw_value: str | None) -> str:
    """Возвращает поддерживаемое назначение логов."""
    normalized = str(raw_value or _DEFAULT_LOG_DESTINATION).strip().lower()
    if normalized not in {"stdout", "file"}:
        return _DEFAULT_LOG_DESTINATION
    return normalized


def normalize_log_color(raw_value: str | None) -> str:
    """Возвращает режим работы ANSI-цветов."""
    normalized = str(raw_value or _DEFAULT_LOG_COLOR).strip().lower()
    if normalized not in {"auto", "true", "false"}:
        return _DEFAULT_LOG_COLOR
    return normalized


def build_log_extra(
    *,
    component: str,
    event: str,
    **context: Any,
) -> dict[str, Any]:
    """Готовит `extra` для структурированных project-логов."""
    cleaned_context: dict[str, Any] = {}
    for key, value in context.items():
        if value in (None, "", [], (), {}):
            continue
        cleaned_context[key] = value

    return {
        "component": component,
        "event": event,
        "log_context": cleaned_context,
    }


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    component: str,
    event: str,
    **context: Any,
) -> None:
    """Пишет лог с единым structured-extra."""
    logger.log(
        level,
        message,
        extra=build_log_extra(component=component, event=event, **context),
    )


class ProjectBaseFormatter(logging.Formatter):
    """Базовый форматтер project-логов."""

    def _timestamp(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created)
        return f"{dt:%Y-%m-%d %H:%M:%S},{int(record.msecs):03d}"

    def _level_number(self, record: logging.LogRecord) -> int:
        return _LEVEL_NUMBERS.get(record.levelno, record.levelno)

    def _level_name(self, record: logging.LogRecord) -> str:
        if record.levelname == "WARNING":
            return "WARN"
        return record.levelname

    def _component(self, record: logging.LogRecord) -> str:
        value = str(getattr(record, "component", "") or "").strip()
        if value:
            return value
        logger_name = str(getattr(record, "name", "") or "").strip()
        if not logger_name:
            return "app"
        return logger_name.split(".")[-1]

    def _event(self, record: logging.LogRecord) -> str:
        value = str(getattr(record, "event", "") or "").strip()
        return value or "log"

    def _context(self, record: logging.LogRecord) -> dict[str, Any]:
        value = getattr(record, "log_context", {})
        if isinstance(value, dict):
            return value
        return {}

    def _render_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (list, tuple, set)):
            return ", ".join(self._render_value(item) for item in value)
        return str(value)

    def _ordered_context_items(
        self, record: logging.LogRecord
    ) -> list[tuple[str, str]]:
        context = self._context(record)
        ordered: list[tuple[str, str]] = []
        seen: set[str] = set()

        for field_name in _DEFAULT_CONTEXT_FIELD_ORDER:
            if field_name not in context:
                continue
            ordered.append((field_name, self._render_value(context[field_name])))
            seen.add(field_name)

        for field_name in sorted(context):
            if field_name in seen:
                continue
            ordered.append((field_name, self._render_value(context[field_name])))

        return ordered


class ProjectTextFormatter(ProjectBaseFormatter):
    """Текстовый формат project-логов без ANSI-цветов."""

    def format(self, record: logging.LogRecord) -> str:
        prefix_parts = [
            f"[{self._timestamp(record)}]",
            str(self._level_number(record)),
            self._level_name(record),
            "|",
            f"[component - {self._component(record)}]",
            f"[event - {self._event(record)}]",
        ]
        for key, value in self._ordered_context_items(record):
            prefix_parts.append(f"[{key} - {value}]")
        prefix = " ".join(prefix_parts)
        return f"{prefix} | {record.getMessage()}"


class ProjectColorTextFormatter(ProjectTextFormatter):
    """Текстовый формат project-логов с ANSI-цветом уровня."""

    def __init__(self, *args: Any, use_color: str = _DEFAULT_LOG_COLOR, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.use_color = normalize_log_color(use_color)

    def _colors_enabled(self) -> bool:
        if self.use_color == "true":
            return True
        if self.use_color == "false":
            return False
        return bool(getattr(sys.stdout, "isatty", lambda: False)())

    def _level_name(self, record: logging.LogRecord) -> str:
        base_name = super()._level_name(record)
        if not self._colors_enabled():
            return base_name
        color = _COLOR_CODES.get(base_name, "")
        reset = _COLOR_CODES["RESET"]
        return f"{color}{base_name}{reset}"


class ProjectJSONFormatter(ProjectBaseFormatter):
    """JSON-формат project-логов."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self._timestamp(record),
            "level_num": self._level_number(record),
            "level_name": self._level_name(record),
            "component": self._component(record),
            "event": self._event(record),
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = self._context(record)
        if context:
            payload["context"] = context
        return json.dumps(payload, ensure_ascii=False)


def build_logging_config(
    *,
    app_level: str | int | None,
    django_level: str | int | None = "WARNING",
    log_format: str | None = None,
    destination: str | None = None,
    color: str | None = None,
    file_path: str | None = None,
) -> dict[str, Any]:
    """Собирает Django LOGGING-конфиг проекта."""
    normalized_format = normalize_log_format(log_format)
    normalized_destination = normalize_log_destination(destination)
    normalized_color = normalize_log_color(color)
    handler_name = "console" if normalized_destination == "stdout" else "file"

    formatter_name = "json"
    if normalized_format == "text":
        formatter_name = "color_text" if normalized_destination == "stdout" else "text"

    handlers: dict[str, Any] = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": formatter_name,
        }
    }

    if normalized_destination == "file":
        target_path = Path(str(file_path or "logs/app.log"))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.FileHandler",
            "filename": str(target_path),
            "encoding": "utf-8",
            "formatter": formatter_name,
        }

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "text": {
                "()": "config.logging.ProjectTextFormatter",
            },
            "color_text": {
                "()": "config.logging.ProjectColorTextFormatter",
                "use_color": normalized_color,
            },
            "json": {
                "()": "config.logging.ProjectJSONFormatter",
            },
        },
        "handlers": handlers,
        "root": {
            "handlers": [handler_name],
            "level": normalize_log_level("WARNING"),
        },
        "loggers": {
            "django": {
                "handlers": [handler_name],
                "level": normalize_log_level(django_level),
                "propagate": False,
            },
            "django.request": {
                "handlers": [handler_name],
                "level": normalize_log_level("WARNING"),
                "propagate": False,
            },
            "celery": {
                "handlers": [handler_name],
                "level": normalize_log_level(app_level),
                "propagate": False,
            },
            "records": {
                "handlers": [handler_name],
                "level": normalize_log_level(app_level),
                "propagate": False,
            },
        },
    }
