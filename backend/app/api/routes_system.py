from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.core import audit as audit_core
from app.core import db
from app.llm.registry import get_effective_settings
from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_ID_HEADER,
    NATIVE_CONFIRMATION_SIGNATURE_HEADER,
    NATIVE_CONFIRMATION_TIMESTAMP_HEADER,
    create_native_confirmation_challenge,
    enforce_native_confirmation_challenge_rate_limit,
    require_native_confirmation,
)
from app.services import (
    support_package_service,
    system_service,
)

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
    return support_package_service.diagnostics_response(app_version=_app_version(request))


@router.post("/system/diagnostics/export")
def export_diagnostics(request: Request):
    return support_package_service.write_diagnostics_export(app_version=_app_version(request))


ERASE_LOCAL_DATA_CONFIRM = "erase-local-data"
ERASE_LOCAL_DATA_NATIVE_ACTION = "erase_local_data"
ERASE_LOCAL_DATA_ENDPOINT = "/api/system/privacy/erase-local-data"


@router.post("/system/privacy/erase-local-data")
def erase_local_data(
    payload: dict,
    confirmation_id: str = Header("", alias=NATIVE_CONFIRMATION_ID_HEADER),
    timestamp: str = Header("", alias=NATIVE_CONFIRMATION_TIMESTAMP_HEADER),
    signature: str = Header("", alias=NATIVE_CONFIRMATION_SIGNATURE_HEADER),
):
    """One-click local personal data deletion (PIPL/GDPR deletion-right entry).

    Erases locally stored user content (tasks, chats, runs, recordings,
    approvals, pairings, memories, index data) and exported diagnostic
    packages. The tamper-evident audit chain is preserved and an erase event
    is appended so the deletion itself stays provable. Local log directories
    are reported for manual cleanup instead of being deleted at runtime.
    """
    if str(payload.get("confirm") or "") != ERASE_LOCAL_DATA_CONFIRM:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation required: pass {{"confirm": "{ERASE_LOCAL_DATA_CONFIRM}"}}.',
        )
    include_settings = _erase_include_settings(payload)
    native_confirmation = require_native_confirmation(
        action=ERASE_LOCAL_DATA_NATIVE_ACTION,
        endpoint=ERASE_LOCAL_DATA_ENDPOINT,
        approval_id="local-data",
        confirmation_id=confirmation_id,
        timestamp=timestamp,
        signature=signature,
        preview_hmac=_erase_local_data_hmac(payload),
    )
    db.require_sensitive_integrity_ok()
    settings = get_effective_settings()

    deleted_packages = 0
    export_dir = Path(settings.data_dir) / "diagnostic-packages"
    if export_dir.is_dir():
        for package in export_dir.glob("*.json"):
            try:
                package.unlink()
                deleted_packages += 1
            except OSError:
                continue

    counts = db.erase_local_user_data(include_settings=include_settings)
    total_rows = sum(counts.values())
    preserved = ["audit_events"]
    if not include_settings:
        preserved.extend(["app_settings", "permission_policies"])

    audit_core.record(
        "privacy.local_data_erased",
        "user",
        {
            "deleted_rows": total_rows,
            "deleted_diagnostic_packages": deleted_packages,
            "include_settings": include_settings,
            "preserved": preserved,
            "desktop_native_confirmation_id": native_confirmation.get("confirmation_id"),
        },
    )
    return {
        "ok": True,
        "scope": "local_only",
        "deleted": {
            "rows_by_table": counts,
            "rows_total": total_rows,
            "diagnostic_packages": deleted_packages,
        },
        "preserved": preserved,
        "manual_cleanup": {
            "log_dirs": "not_deleted_at_runtime_see_settings_system_info",
        },
        "audit": "erase_event_appended_to_local_audit_chain",
    }


@router.post("/system/privacy/erase-local-data/native-confirmation-challenge")
def erase_local_data_native_confirmation_challenge(payload: dict, request: Request):
    if str(payload.get("confirm") or "") != ERASE_LOCAL_DATA_CONFIRM:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation required: pass {{"confirm": "{ERASE_LOCAL_DATA_CONFIRM}"}}.',
        )
    _erase_include_settings(payload)
    enforce_native_confirmation_challenge_rate_limit(_client_scope(request))
    db.require_sensitive_integrity_ok()
    return create_native_confirmation_challenge(
        action=ERASE_LOCAL_DATA_NATIVE_ACTION,
        endpoint=ERASE_LOCAL_DATA_ENDPOINT,
        approval_id="local-data",
        preview_hmac=_erase_local_data_hmac(payload),
    )


def _erase_local_data_hmac(payload: dict[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _erase_include_settings(payload: dict[str, Any]) -> bool:
    value = payload.get("include_settings", False)
    if not isinstance(value, bool):
        raise HTTPException(status_code=422, detail="include_settings must be a boolean.")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _client_scope(request: Request) -> str:
    client = request.client
    host = client.host if client else "unknown"
    return (host or "unknown").strip().lower() or "unknown"


def _app_version(request: Request) -> str:
    return str(getattr(request.app, "version", "") or "")


@router.get("/system/processes")
def processes(limit: int = 25):
    return system_service.processes(limit)


@router.get("/system/startup-items")
def startup_items():
    return system_service.startup_items()


@router.post("/system/open-settings")
def open_settings(payload: dict):
    return system_service.open_settings(str(payload.get("uri", "ms-settings:")), bool(payload.get("dry_run", False)))
