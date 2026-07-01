from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.observability import (
    JsonLogFormatter,
    RedactingTextFormatter,
    configure_logging,
    metrics,
    report_exception,
    span,
)
from app.observability import context as obs_context
from app.observability import crash as crash_module

_SECRET = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


def test_counter_snapshot_aggregates_by_labels():
    metrics.increment_counter("widgets_total", labels={"kind": "a"})
    metrics.increment_counter("widgets_total", value=2, labels={"kind": "a"})
    metrics.increment_counter("widgets_total", labels={"kind": "b"})
    snap = metrics.snapshot()
    by_key = {(c["name"], tuple(sorted(c["labels"].items()))): c["value"] for c in snap["counters"]}
    assert by_key[("widgets_total", (("kind", "a"),))] == 3
    assert by_key[("widgets_total", (("kind", "b"),))] == 1


def test_histogram_prometheus_render_is_cumulative():
    for value in (0.001, 0.2, 3.0):
        metrics.observe_histogram("latency_seconds", value)
    rendered = metrics.render_prometheus()
    assert "# TYPE latency_seconds histogram" in rendered
    assert 'latency_seconds_bucket{le="+Inf"} 3' in rendered
    assert "latency_seconds_count 3" in rendered


def test_timer_records_observation():
    with metrics.timer("op_seconds"):
        pass
    names = {h["name"] for h in metrics.snapshot()["histograms"]}
    assert "op_seconds" in names


def test_span_sets_context_and_records_metric():
    with span("unit-span") as current:
        assert obs_context.get_trace_id() is not None
        assert obs_context.get_span_id() == current.span_id
    names = {h["name"] for h in metrics.snapshot()["histograms"]}
    assert "span_duration_seconds" in names
    assert obs_context.get_span_id() is None


def test_json_formatter_redacts_and_includes_correlation():
    formatter = JsonLogFormatter()
    token = obs_context.set_trace_id("trace-123")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="leaking %s now",
            args=(_SECRET,),
            exc_info=None,
        )
        record.request_id = "-"
        record.trace_id = obs_context.get_trace_id()
        record.span_id = "-"
        output = formatter.format(record)
        assert "trace-123" in output
        assert _SECRET not in output
    finally:
        obs_context.reset_trace_id(token)


def test_text_formatter_redacts_secret():
    formatter = RedactingTextFormatter("%(message)s")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token is %s",
        args=(_SECRET,),
        exc_info=None,
    )
    output = formatter.format(record)
    assert _SECRET not in output


def test_configure_logging_is_idempotent():
    configure_logging(force=True)
    root = logging.getLogger()
    first = [h for h in root.handlers if getattr(h, "_lengrvis_observability_handler", False)]
    configure_logging()
    second = [h for h in root.handlers if getattr(h, "_lengrvis_observability_handler", False)]
    assert len(first) >= 1
    assert len(first) == len(second)


def test_report_exception_writes_redacted_file_and_counter(tmp_path):
    try:
        raise ValueError("boom " + _SECRET)
    except ValueError as exc:
        path = report_exception(exc, source="unit", report_dir=tmp_path)
    assert path is not None
    report_path = Path(path)
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")
    assert _SECRET not in content
    counters = {c["name"] for c in metrics.snapshot()["counters"]}
    assert "crashes_total" in counters


def test_crash_report_prune_only_suppresses_filesystem_errors():
    class OSErrorDir:
        def glob(self, pattern):
            raise OSError("directory unavailable")

    crash_module._prune_reports(OSErrorDir())

    class RuntimeErrorDir:
        def glob(self, pattern):
            raise RuntimeError("glob bug")

    with pytest.raises(RuntimeError, match="glob bug"):
        crash_module._prune_reports(RuntimeErrorDir())
