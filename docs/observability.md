# Observability

The backend ships a dependency-free observability layer under
`backend/app/observability`. It provides structured logging, in-process metrics,
lightweight tracing, and crash reporting. All output is redacted through
`app.policy.redaction`, and everything is safe-by-default / opt-in.

## Components

- **context** — `contextvars`-based request/trace/span correlation IDs.
- **metrics** — thread-safe counters, gauges, and histograms with a Prometheus
  text renderer. Use `increment_counter`, `set_gauge`, `adjust_gauge`,
  `observe_histogram`, and the `timer(...)` context manager.
- **logging_config** — `configure_logging()` installs a redacting stdout handler
  (JSON or text) plus an optional rotating file handler.
- **tracing** — `span(name)` context manager and `traced()` decorator; records
  `span_duration_seconds`.
- **crash** — `install_crash_handlers()` wires `sys.excepthook` /
  `threading.excepthook`; `report_exception()` writes redacted JSON reports and
  increments `crashes_total`.
- **middleware** — HTTP request correlation + metrics, registered in `app.main`.

## HTTP endpoints

- `GET /api/observability/metrics` — JSON snapshot of all metrics.
- `GET /api/observability/metrics/prometheus` — Prometheus exposition format.

Both are gated by `LENGRVIS_OBSERVABILITY_ENABLED` (default on).

## Key metrics

| Metric | Type | Labels |
| --- | --- | --- |
| `http_requests_total` | counter | method, route, status |
| `http_request_duration_seconds` | histogram | method, route |
| `http_server_errors_total` | counter | method, route |
| `http_unhandled_exceptions_total` | counter | method, route |
| `span_duration_seconds` | histogram | span, status |
| `crashes_total` | counter | source |

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `LENGRVIS_OBSERVABILITY_ENABLED` | `true` | Enable the metrics HTTP endpoints. |
| `LENGRVIS_LOG_FORMAT` | `text` | `json` or `text` log formatter. |
| `LENGRVIS_LOG_LEVEL` | `INFO` | Root log level. |
| `LENGRVIS_LOG_FILE_ENABLED` | `false` | Enable the rotating file handler. |
| `LENGRVIS_LOG_DIR` | _(unset)_ | Override the log directory. |
| `LENGRVIS_DATA_DIR` | _(unset)_ | Base dir for logs/crash reports if specific dirs unset. |
| `LENGRVIS_CRASH_REPORTING_ENABLED` | `true` | Write crash report files. |
| `LENGRVIS_CRASH_REPORT_DIR` | _(unset)_ | Override the crash report directory. |
