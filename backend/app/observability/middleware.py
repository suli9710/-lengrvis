"""FastAPI/Starlette middleware that emits HTTP metrics and correlation IDs.

The middleware is registered last so it is the outermost layer: it establishes
request/trace/span correlation IDs, times the request, records request/error
metrics, and echoes ``X-Request-ID`` / ``X-Trace-ID`` response headers.
"""

from __future__ import annotations

import logging
import time

import app.observability.context as obs_context
import app.observability.metrics as metrics

_logger = logging.getLogger("lengrvis.observability.access")


def _route_template(request) -> str:
    try:
        route = request.scope.get("route")
    except AttributeError:  # pragma: no cover
        route = None
    path = getattr(route, "path", None)
    if path:
        return path
    return "other"


def register_observability_middleware(app) -> None:
    @app.middleware("http")
    async def observability_middleware(request, call_next):
        request_id = request.headers.get("x-request-id") or obs_context.new_span_id()
        trace_id = request.headers.get("x-trace-id") or obs_context.new_trace_id()
        span_id = obs_context.new_span_id()
        request_token = obs_context.set_request_id(request_id)
        trace_token = obs_context.set_trace_id(trace_id)
        span_token = obs_context.set_span_id(span_id)
        method = request.method
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Trace-ID"] = trace_id
            return response
        except Exception:  # noqa: BLE001 - broad-exception-boundary
            route = _route_template(request)
            metrics.increment_counter(
                "http_unhandled_exceptions_total",
                labels={"method": method, "route": route},
            )
            raise
        finally:
            duration = time.perf_counter() - start
            route = _route_template(request)
            metrics.increment_counter(
                "http_requests_total",
                labels={"method": method, "route": route, "status": str(status_code)},
            )
            metrics.observe_histogram(
                "http_request_duration_seconds",
                duration,
                labels={"method": method, "route": route},
            )
            if status_code >= 500:
                metrics.increment_counter(
                    "http_server_errors_total",
                    labels={"method": method, "route": route},
                )
            _logger.info(
                "http.access",
                extra={
                    "observability": {
                        "method": method,
                        "route": route,
                        "status": status_code,
                        "duration_seconds": duration,
                    }
                },
            )
            obs_context.reset_span_id(span_token)
            obs_context.reset_trace_id(trace_token)
            obs_context.reset_request_id(request_token)
