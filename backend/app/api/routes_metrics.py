from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.llm.registry import get_effective_settings
from app.services.local_metrics_service import collect_local_metrics

router = APIRouter()


@router.get("/metrics/local")
def local_metrics(days: int = 7):
    """Opt-in local metrics panel data (privacy-first; counts only, no payloads).

    Gated by the ``local_metrics_enabled`` setting / ``LENGRVIS_LOCAL_METRICS_ENABLED``
    env var. Returns 403 when the user has not opted in so the desktop panel can
    render an explicit enable hint instead of silently showing empty data.
    """
    settings = get_effective_settings()
    if not getattr(settings, "local_metrics_enabled", False):
        raise HTTPException(
            status_code=403,
            detail="Local metrics are opt-in. Enable local_metrics_enabled (LENGRVIS_LOCAL_METRICS_ENABLED=1) first.",
        )
    return collect_local_metrics(days=days)
