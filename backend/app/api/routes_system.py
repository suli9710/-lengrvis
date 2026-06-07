from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from app.config import PROJECT_ROOT
from app.core import audit as audit_core, db
from app.llm.registry import get_effective_settings
from app.services import mobile_pairing_service
from app.services import system_service


router = APIRouter()


@router.get("/system/info")
def info():
    return system_service.info()


@router.get("/system/disks")
def disks():
    return system_service.disks()


@router.get("/system/network")
def network():
    return system_service.network()


@router.get("/system/diagnostics")
def diagnostics(request: Request):
    payload = system_service.diagnostics()
    base = dict(payload) if isinstance(payload, dict) else {"diagnostics": payload}
    settings = get_effective_settings()
    metrics = db.local_product_diagnostics()
    verification = audit_core.verify_chain(limit=None)
    base.update(
        {
            "product": {
                "name": "Lengrvis",
                "version": str(getattr(request.app, "version", "") or ""),
            },
            "local_paths": {
                "data_dir": str(Path(settings.data_dir)),
                "database": str(db.db_path()),
                "log_dirs": _log_dirs(settings.data_dir),
            },
            "audit": {
                "verification": verification,
                "latest_event": metrics.get("latest_audit_event"),
            },
            "lan_transport": _lan_transport_readiness(settings),
            "recent_counts": metrics.get("recent_counts", {}),
            "recent_failure_counts": metrics.get("recent_failure_counts", {}),
            "diagnostic_hints": _diagnostic_hints(verification, metrics),
            "diagnostic_scope": "local_only",
        }
    )
    return base


@router.get("/system/processes")
def processes(limit: int = 25):
    return system_service.processes(limit)


@router.get("/system/startup-items")
def startup_items():
    return system_service.startup_items()


@router.post("/system/open-settings")
def open_settings(payload: dict):
    return system_service.open_settings(str(payload.get("uri", "ms-settings:")), bool(payload.get("dry_run", False)))


def _log_dirs(data_dir: str) -> list[str]:
    candidates = [PROJECT_ROOT / "logs", Path(data_dir) / "logs"]
    seen: set[str] = set()
    result: list[str] = []
    for path in candidates:
        text = str(path)
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _lan_transport_readiness(settings: Any) -> dict[str, Any]:
    transport = dict(mobile_pairing_service.lan_transport_security(settings))
    self_signed = _certificate_self_signed(str(getattr(settings, "lan_tls_cert_file", "") or ""))
    transport["certificate_self_signed"] = self_signed
    transport["certificate_trust"] = "requires_client_trust" if transport.get("trust_required") else "not_required"
    if transport.get("https_enabled") and self_signed is True:
        transport["certificate_trust"] = "self_signed_requires_client_trust"
    elif transport.get("https_enabled") and self_signed is None:
        transport["certificate_trust"] = "requires_client_trust_unknown_issuer"
    return transport


def _certificate_self_signed(cert_file: str) -> bool | None:
    if not cert_file:
        return None
    path = Path(cert_file).expanduser()
    if not path.exists():
        return None
    try:
        cert = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - diagnostics should report readiness, not fail.
        return None
    return cert.get("subject") == cert.get("issuer")


def _diagnostic_hints(verification: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if not verification.get("ok", False):
        reason = str(verification.get("failure_reason") or "unknown")
        index = verification.get("failure_index")
        hints.append(f"Audit chain verification failed at event {index}: {reason}.")

    failure_counts = metrics.get("recent_failure_counts") if isinstance(metrics, dict) else {}
    if isinstance(failure_counts, dict):
        for key, value in failure_counts.items():
            if int(value or 0) > 0:
                hints.append(f"Recent {key.replace('_', ' ')}: {value}.")

    if not hints:
        hints.append("No local audit integrity failure or recent product failure detected.")
    return hints
