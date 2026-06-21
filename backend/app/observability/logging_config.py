"""Structured logging configuration with secret redaction.

``configure_logging`` is idempotent and installs a stdout handler (and an
optional rotating file handler) on the root logger. Two formatters are provided:
a JSON formatter for machine ingestion and a redacting text formatter for human
consumption. Both route message bodies through ``app.policy.redaction``.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

import app.observability.context as obs_context
from app.config import get_env

try:  # pragma: no cover - redaction is always present in the app
    from app.policy.redaction import redact_text, redact_value
except Exception:  # pragma: no cover - defensive fallback only
    def redact_text(text, redact_generic_tokens=True):  # type: ignore[misc]
        return text

    def redact_value(value):  # type: ignore[misc]
        return value


_HANDLER_MARKER = "_lengrvis_observability_handler"
_TEXT_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "[req=%(request_id)s trace=%(trace_id)s span=%(span_id)s] %(message)s"
)
_configured = False


def _env_truthy(name: str, default: bool) -> bool:
    raw = get_env(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class _CorrelationFilter(logging.Filter):
    """Attach correlation IDs to every record (defaulting to ``-``)."""

    def filter(self, record: logging.LogRecord) -> bool:
        snap = obs_context.correlation_snapshot()
        record.request_id = snap.get("request_id", "-")
        record.trace_id = snap.get("trace_id", "-")
        record.span_id = snap.get("span_id", "-")
        return True


class JsonLogFormatter(logging.Formatter):
    """Render log records as a single redacted JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        message = redact_text(record.getMessage(), redact_generic_tokens=True)
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": getattr(record, "request_id", "-"),
            "trace_id": getattr(record, "trace_id", "-"),
            "span_id": getattr(record, "span_id", "-"),
        }
        observability_extra = getattr(record, "observability", None)
        if isinstance(observability_extra, dict):
            payload["observability"] = redact_value(observability_extra)
        if record.exc_info:
            payload["exception"] = redact_text(
                self.formatException(record.exc_info), redact_generic_tokens=True
            )
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class RedactingTextFormatter(logging.Formatter):
    """Apply redaction to the fully-formatted human-readable log line."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_text(formatted, redact_generic_tokens=True)


def _resolve_log_dir() -> Path:
    configured = get_env("LENGRVIS_LOG_DIR")
    if configured:
        return Path(configured)
    data_dir = get_env("LENGRVIS_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "logs"
    return Path.home() / ".lengrvis" / "logs"


def _build_formatter() -> logging.Formatter:
    fmt = (get_env("LENGRVIS_LOG_FORMAT") or "text").strip().lower()
    if fmt == "json":
        return JsonLogFormatter()
    return RedactingTextFormatter(_TEXT_FORMAT)


def configure_logging(force: bool = False) -> None:
    """Configure root logging once (idempotent unless ``force`` is set)."""

    global _configured
    if _configured and not force:
        return

    level_name = (get_env("LENGRVIS_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)

    correlation_filter = _CorrelationFilter()
    formatter = _build_formatter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(correlation_filter)
    setattr(stream_handler, _HANDLER_MARKER, True)
    root.addHandler(stream_handler)

    if _env_truthy("LENGRVIS_LOG_FILE_ENABLED", False):
        try:
            log_dir = _resolve_log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                str(log_dir / "lengrvis.log"),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(correlation_filter)
            setattr(file_handler, _HANDLER_MARKER, True)
            root.addHandler(file_handler)
        except Exception:  # pragma: no cover - file logging is best effort
            logging.getLogger(__name__).warning(
                "observability: file logging disabled (initialization failed)"
            )

    _configured = True
