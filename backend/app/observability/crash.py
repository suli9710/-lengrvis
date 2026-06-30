"""Crash reporting: redacted JSON crash dumps + excepthook installation.

Reports are written to a bounded directory (pruned to the most recent
``_MAX_REPORTS`` files) and every report increments the ``crashes_total``
counter. All message and traceback text is redacted before it touches disk.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from pathlib import Path

import app.observability.metrics as metrics
from app.config import get_env

try:  # pragma: no cover - redaction is always present in the app
    from app.policy.redaction import redact_text
except Exception:  # pragma: no cover - defensive fallback only  # noqa: BLE001

    def redact_text(text, redact_generic_tokens=True):  # type: ignore[misc]
        return text


_logger = logging.getLogger("lengrvis.observability.crash")
_MAX_REPORTS = 50
_install_lock = threading.Lock()
_installed = False


def _env_truthy(name: str, default: bool) -> bool:
    raw = get_env(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _default_report_dir() -> Path:
    configured = get_env("LENGRVIS_CRASH_REPORT_DIR")
    if configured:
        return Path(configured)
    data_dir = get_env("LENGRVIS_DATA_DIR")
    if data_dir:
        return Path(data_dir) / "crash_reports"
    return Path.home() / ".lengrvis" / "crash_reports"


def _prune_reports(report_dir: Path) -> None:
    try:
        reports = sorted(report_dir.glob("crash_*.json"))
    except Exception:  # pragma: no cover  # noqa: BLE001
        return
    excess = len(reports) - _MAX_REPORTS
    if excess <= 0:
        return
    for path in reports[:excess]:
        try:
            path.unlink()
        except Exception:  # pragma: no cover  # noqa: S112, BLE001
            continue


def _write_report(exc: BaseException, source: str, report_dir: Path | None) -> str | None:
    metrics.increment_counter("crashes_total", labels={"source": source})
    message = redact_text(f"{type(exc).__name__}: {exc}", redact_generic_tokens=True)
    tb = redact_text(
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        redact_generic_tokens=True,
    )
    _logger.error(
        "crash.reported",
        extra={"observability": {"source": source, "error": message}},
    )
    if not _env_truthy("LENGRVIS_CRASH_REPORTING_ENABLED", True):
        return None
    target_dir = report_dir or _default_report_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        filename = f"crash_{timestamp}_{os.getpid()}.json"
        path = target_dir / filename
        payload = {
            "source": source,
            "error": message,
            "traceback": tb,
            "pid": os.getpid(),
            "timestamp": timestamp,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _prune_reports(target_dir)
        return str(path)
    except Exception:  # pragma: no cover - reporting must never raise  # noqa: BLE001
        _logger.warning("crash.report_write_failed")
        return None


def report_exception(exc: BaseException, source: str = "manual", report_dir: Path | None = None) -> str | None:
    """Record a crash report for ``exc`` and return the report path (if written)."""

    return _write_report(exc, source, report_dir)


def install_crash_handlers(report_dir: Path | None = None) -> None:
    """Install ``sys.excepthook`` and ``threading.excepthook`` handlers once."""

    global _installed
    with _install_lock:
        if _installed:
            return
        previous_excepthook = sys.excepthook

        def _hook(exc_type, exc_value, exc_tb):
            if exc_value is not None:
                _write_report(exc_value, "sys.excepthook", report_dir)
            previous_excepthook(exc_type, exc_value, exc_tb)

        sys.excepthook = _hook

        if hasattr(threading, "excepthook"):
            previous_thread_hook = threading.excepthook

            def _thread_hook(args):
                exc_value = getattr(args, "exc_value", None)
                if exc_value is not None:
                    _write_report(exc_value, "threading.excepthook", report_dir)
                previous_thread_hook(args)

            threading.excepthook = _thread_hook

        _installed = True


def reset_crash_handlers_for_tests() -> None:
    """Reset the install guard so tests can re-install handlers."""

    global _installed
    with _install_lock:
        _installed = False
