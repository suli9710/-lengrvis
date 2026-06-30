"""In-process metrics registry with Prometheus text rendering.

This is intentionally dependency-free: counters, gauges, and histograms are kept
in thread-safe dictionaries and rendered into the Prometheus text exposition
format on demand. Histogram bucket counts are stored cumulatively so the render
step is a direct mapping.
"""

from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager

DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)

LabelsInput = dict[str, object] | None
FrozenLabels = tuple[tuple[str, str], ...]


def _freeze_labels(labels: LabelsInput) -> FrozenLabels:
    if not labels:
        return ()
    items: list[tuple[str, str]] = []
    for key in sorted(labels.keys()):
        items.append((str(key), str(labels[key])))
    return tuple(items)


class _Histogram:
    """A single histogram series with cumulative bucket counts."""

    def __init__(self, buckets: tuple[float, ...]):
        bounds = sorted(float(b) for b in buckets if not math.isinf(float(b)))
        self._bounds: tuple[float, ...] = tuple(bounds)
        self._counts: list[int] = [0 for _ in self._bounds]
        self._sum: float = 0.0
        self._count: int = 0

    def observe(self, value: float) -> None:
        self._sum += value
        self._count += 1
        for index, bound in enumerate(self._bounds):
            if value <= bound:
                self._counts[index] += 1

    def snapshot(self) -> dict[str, object]:
        buckets = []
        for index, bound in enumerate(self._bounds):
            buckets.append({"le": bound, "count": self._counts[index]})
        return {"buckets": buckets, "sum": self._sum, "count": self._count}


class MetricsRegistry:
    """Thread-safe registry of counters, gauges, and histograms."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, FrozenLabels], float] = {}
        self._gauges: dict[tuple[str, FrozenLabels], float] = {}
        self._histograms: dict[tuple[str, FrozenLabels], _Histogram] = {}
        self._histogram_buckets: dict[str, tuple[float, ...]] = {}

    def increment_counter(self, name: str, value: float = 1.0, labels: LabelsInput = None) -> None:
        key = (name, _freeze_labels(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + float(value)

    def set_gauge(self, name: str, value: float, labels: LabelsInput = None) -> None:
        key = (name, _freeze_labels(labels))
        with self._lock:
            self._gauges[key] = float(value)

    def adjust_gauge(self, name: str, delta: float, labels: LabelsInput = None) -> None:
        key = (name, _freeze_labels(labels))
        with self._lock:
            self._gauges[key] = self._gauges.get(key, 0.0) + float(delta)

    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: LabelsInput = None,
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        key = (name, _freeze_labels(labels))
        with self._lock:
            hist = self._histograms.get(key)
            if hist is None:
                resolved = tuple(buckets) if buckets else self._histogram_buckets.get(name, DEFAULT_BUCKETS)
                self._histogram_buckets.setdefault(name, resolved)
                hist = _Histogram(resolved)
                self._histograms[key] = hist
            hist.observe(float(value))

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._histogram_buckets.clear()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counters = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._counters.items())
            ]
            gauges = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._gauges.items())
            ]
            histograms = []
            for (name, labels), hist in sorted(self._histograms.items()):
                entry = {"name": name, "labels": dict(labels)}
                entry.update(hist.snapshot())
                histograms.append(entry)
        return {"counters": counters, "gauges": gauges, "histograms": histograms}

    def render_prometheus(self) -> str:
        with self._lock:
            counters = sorted(self._counters.items())
            gauges = sorted(self._gauges.items())
            histograms = [(name, labels, hist.snapshot()) for (name, labels), hist in sorted(self._histograms.items())]
        lines: list[str] = []
        emitted_type = set()

        def emit_type(metric_name: str, metric_type: str) -> None:
            if metric_name in emitted_type:
                return
            emitted_type.add(metric_name)
            lines.append(f"# TYPE {metric_name} {metric_type}")

        for (name, labels), value in counters:
            metric_name = _sanitize_name(name)
            emit_type(metric_name, "counter")
            lines.append(f"{metric_name}{_render_labels(labels)} {_render_number(value)}")

        for (name, labels), value in gauges:
            metric_name = _sanitize_name(name)
            emit_type(metric_name, "gauge")
            lines.append(f"{metric_name}{_render_labels(labels)} {_render_number(value)}")

        for name, labels, snap in histograms:
            metric_name = _sanitize_name(name)
            emit_type(metric_name, "histogram")
            for bucket in snap["buckets"]:
                bucket_labels = _render_labels(labels, extra=[("le", _format_bound(bucket["le"]))])
                lines.append("{}_bucket{} {}".format(metric_name, bucket_labels, _render_number(bucket["count"])))
            inf_labels = _render_labels(labels, extra=[("le", "+Inf")])
            lines.append("{}_bucket{} {}".format(metric_name, inf_labels, _render_number(snap["count"])))
            lines.append("{}_sum{} {}".format(metric_name, _render_labels(labels), _render_number(snap["sum"])))
            lines.append("{}_count{} {}".format(metric_name, _render_labels(labels), _render_number(snap["count"])))

        if not lines:
            return ""
        return "\n".join(lines) + "\n"


def _sanitize_name(name: str) -> str:
    chars = []
    for ch in str(name):
        if ch.isalnum() or ch in ("_", ":"):
            chars.append(ch)
        else:
            chars.append("_")
    sanitized = "".join(chars)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "_"


def _escape_label_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _render_labels(labels: FrozenLabels, extra: list[tuple[str, str]] | None = None) -> str:
    parts: list[str] = []
    for key, value in labels:
        parts.append(f'{_sanitize_name(key)}="{_escape_label_value(value)}"')
    if extra:
        for key, value in extra:
            parts.append(f'{_sanitize_name(key)}="{_escape_label_value(value)}"')
    if not parts:
        return ""
    return "{" + ",".join(parts) + "}"


def _render_number(value: float) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return repr(value)


def _format_bound(bound: float) -> str:
    bound = float(bound)
    if bound.is_integer():
        return str(int(bound))
    return repr(bound)


_REGISTRY = MetricsRegistry()


def registry() -> MetricsRegistry:
    return _REGISTRY


def increment_counter(name: str, value: float = 1.0, labels: LabelsInput = None) -> None:
    _REGISTRY.increment_counter(name, value=value, labels=labels)


def set_gauge(name: str, value: float, labels: LabelsInput = None) -> None:
    _REGISTRY.set_gauge(name, value, labels=labels)


def adjust_gauge(name: str, delta: float, labels: LabelsInput = None) -> None:
    _REGISTRY.adjust_gauge(name, delta, labels=labels)


def observe_histogram(
    name: str,
    value: float,
    labels: LabelsInput = None,
    buckets: tuple[float, ...] | None = None,
) -> None:
    _REGISTRY.observe_histogram(name, value, labels=labels, buckets=buckets)


def snapshot() -> dict[str, object]:
    return _REGISTRY.snapshot()


def render_prometheus() -> str:
    return _REGISTRY.render_prometheus()


def reset() -> None:
    _REGISTRY.reset()


@contextmanager
def timer(name: str, labels: LabelsInput = None, buckets: tuple[float, ...] | None = None):
    start = time.perf_counter()
    try:
        yield
    finally:
        observe_histogram(name, time.perf_counter() - start, labels=labels, buckets=buckets)
