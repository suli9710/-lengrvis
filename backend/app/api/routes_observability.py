"""Observability HTTP endpoints (metrics snapshot + Prometheus exposition).

Both endpoints are gated by ``LENGRVIS_OBSERVABILITY_ENABLED`` (default on) so
they can be disabled in locked-down deployments without removing the
instrumentation itself.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

import app.observability.metrics as metrics
from app.config import get_env

router = APIRouter()


def _env_truthy(name: str, default: bool) -> bool:
    raw = get_env(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _observability_enabled() -> bool:
    return _env_truthy("LENGRVIS_OBSERVABILITY_ENABLED", True)


@router.get("/observability/metrics")
def observability_metrics():
    if not _observability_enabled():
        raise HTTPException(status_code=403, detail="Observability endpoints are disabled")
    return metrics.snapshot()


@router.get("/observability/metrics/prometheus", response_class=PlainTextResponse)
def observability_metrics_prometheus():
    if not _observability_enabled():
        raise HTTPException(status_code=403, detail="Observability endpoints are disabled")
    return PlainTextResponse(metrics.render_prometheus(), media_type="text/plain; version=0.0.4")
