"""Structured redaction-safe logging helpers for Duckln."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any


LOGGER_NAME = "duckln"
_LOGGER = logging.getLogger(LOGGER_NAME)
_LOGGER.addHandler(logging.NullHandler())
_LOGGER.propagate = False


@dataclass(frozen=True)
class LogRecord:
    """Structured log record emitted as redaction-safe JSON."""

    timestamp: str
    level: str
    event: str
    message: str
    metadata: dict[str, Any]


def emit_log(
    *,
    level: str,
    event: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> LogRecord:
    """Emit a structured redaction-safe log record."""

    payload = LogRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        level=level.upper(),
        event=event,
        message=_redact_text(message),
        metadata=_redact_metadata(metadata or {}),
    )
    _LOGGER.log(_coerce_log_level(payload.level), json.dumps(payload.__dict__, sort_keys=True))
    return payload


def log_provider_failure(
    *,
    provider: str,
    model: str | None,
    status_code: int | None,
    message: str,
) -> LogRecord:
    """Log a redaction-safe provider failure event."""

    return emit_log(
        level="error",
        event="provider_failure",
        message=message,
        metadata={
            "provider": provider,
            "model": model or "",
            "status_code": status_code,
        },
    )


def log_blocked_command(
    *,
    command: str,
    reason: str,
    safety_class: str,
) -> LogRecord:
    """Log a blocked command event without raw outputs."""

    return emit_log(
        level="warning",
        event="blocked_command",
        message="Duckln blocked a command based on safety policy.",
        metadata={
            "command": command,
            "reason": reason,
            "safety_class": safety_class,
        },
    )


def log_execution_result(
    *,
    command: str,
    success: bool,
    exit_code: int | None,
    timed_out: bool,
    duration_seconds: float,
) -> LogRecord:
    """Log a controlled execution result without full stdout or stderr."""

    return emit_log(
        level="info" if success else "error",
        event="execution_result",
        message="Duckln finished a controlled command execution.",
        metadata={
            "command": command,
            "success": success,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(duration_seconds, 4),
        },
    )


def _redact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact structured metadata values."""

    redacted: dict[str, Any] = {}
    for key, value in metadata.items():
        safe_key = _redact_text(str(key))
        if isinstance(value, dict):
            redacted[safe_key] = _redact_metadata(value)
        elif isinstance(value, (list, tuple)):
            redacted[safe_key] = [_redact_sequence_value(item) for item in value]
        elif value is None:
            redacted[safe_key] = None
        else:
            redacted[safe_key] = _redact_text(str(value))
    return redacted


def _redact_sequence_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _redact_metadata(value)
    if isinstance(value, (list, tuple)):
        return [_redact_sequence_value(item) for item in value]
    if value is None:
        return None
    return _redact_text(str(value))


def _redact_text(text: str) -> str:
    from duckln.diagnostics import redact_sensitive_data

    return redact_sensitive_data(text)


def _coerce_log_level(level: str) -> int:
    levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return levels.get(level.upper(), logging.INFO)
