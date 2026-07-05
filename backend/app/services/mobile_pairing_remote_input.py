from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.policy.approval_binding import remote_input_binding_ref
from app.security.mobile_jwt import REMOTE_INPUT_SCOPE
from app.services.mobile_pairing_common import _text

REMOTE_INPUT_GRANT_TTL_SECONDS = 5 * 60


def _remote_input_grant_ttl(value: int) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = REMOTE_INPUT_GRANT_TTL_SECONDS
    return max(1, min(requested, REMOTE_INPUT_GRANT_TTL_SECONDS))


def _normalized_remote_input_grants(device: dict[str, Any]) -> list[dict[str, Any]]:
    raw_grants = device.get("remote_input_grants") or []
    if not isinstance(raw_grants, list):
        return []
    now = datetime.now(UTC)
    grants: list[dict[str, Any]] = []
    for raw in raw_grants:
        if not isinstance(raw, dict):
            continue
        grant = {
            "id": _text(raw.get("id")),
            "status": _text(raw.get("status")) or "active",
            "scope": _text(raw.get("scope")) or REMOTE_INPUT_SCOPE,
            "created_at": _text(raw.get("created_at")),
            "expires_at": _text(raw.get("expires_at")),
            "revoked_at": _text(raw.get("revoked_at")),
            # Internal anti-replay binding; never surfaced in safe payloads.
            "token_id": _text(raw.get("token_id")),
        }
        if not grant["id"]:
            continue
        if grant["status"] == "active" and _grant_expires_at(grant) < now:
            grant["status"] = "expired"
        grants.append(grant)
    return grants


def _revoked_remote_input_grants(device: dict[str, Any], timestamp: str) -> list[dict[str, Any]]:
    grants = _normalized_remote_input_grants(device)
    for grant in grants:
        if grant["status"] == "active":
            grant["status"] = "revoked"
            grant["revoked_at"] = timestamp
    return grants


def _safe_remote_input_grant(grant: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": grant.get("id") or "",
        "status": grant.get("status") or "",
        "scope": grant.get("scope") or REMOTE_INPUT_SCOPE,
        "created_at": grant.get("created_at") or "",
        "expires_at": grant.get("expires_at") or "",
        "revoked_at": grant.get("revoked_at") or "",
        "binding_ref": remote_input_binding_ref(grant.get("id")),
    }


def _grant_expires_at(grant: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(grant.get("expires_at") or ""))
    except ValueError:
        return datetime.fromtimestamp(0, UTC)
