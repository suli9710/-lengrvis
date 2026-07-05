from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core import db
from app.core.schemas import Approval
from app.security.mobile_jwt import REMOTE_INPUT_SCOPE, TOKEN_SCOPE, mobile_token_scopes
from app.services.mobile_pairing_common import _text, _text_list


def mobile_claims_can_access_approval(approval: Approval | dict[str, Any], claims: dict[str, Any]) -> bool:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    return _mobile_claims_allow_approval_for_read(payload, claims)


def raise_if_mobile_claims_disallowed(approval: Approval | dict[str, Any], claims: dict[str, Any] | None) -> None:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    _raise_if_mobile_claims_disallowed(payload, claims)


def _raise_if_mobile_claims_disallowed(approval: dict[str, Any], claims: dict[str, Any] | None) -> None:
    reason = _mobile_approval_approve_denial_reason(approval, claims)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


def _raise_if_mobile_claims_disallowed_for_read(approval: dict[str, Any], claims: dict[str, Any] | None) -> None:
    reason = _mobile_approval_read_denial_reason(approval, claims)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


def _raise_if_mobile_claims_disallowed_for_reject(approval: dict[str, Any], claims: dict[str, Any] | None) -> None:
    reason = _mobile_approval_reject_denial_reason(approval, claims)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


def _mobile_claims_allow_approval(approval: dict[str, Any], claims: dict[str, Any] | None) -> bool:
    return not _mobile_approval_denial_reason(approval, claims)


def _mobile_claims_allow_approval_for_read(approval: dict[str, Any], claims: dict[str, Any] | None) -> bool:
    return not _mobile_approval_read_denial_reason(approval, claims)


def _mobile_approval_read_denial_reason(approval: dict[str, Any], claims: dict[str, Any] | None) -> str:
    reason = _mobile_approval_denial_reason(approval, claims)
    if not reason:
        return ""
    if _paired_mobile_claims_can_read_remote_input_approval(approval, claims):
        return ""
    return reason


def _mobile_approval_reject_denial_reason(approval: dict[str, Any], claims: dict[str, Any] | None) -> str:
    reason = _mobile_approval_denial_reason(approval, claims)
    if not reason:
        return ""
    if _paired_mobile_claims_can_reject_remote_input_approval(approval, claims):
        return ""
    return reason


def _mobile_approval_approve_denial_reason(approval: dict[str, Any], claims: dict[str, Any] | None) -> str:
    reason = _mobile_approval_denial_reason(approval, claims)
    if reason:
        return reason
    if _remote_input_grant_cannot_self_approve(approval, claims):
        return "Remote input grant token cannot approve its own input request."
    return ""


def _remote_input_grant_cannot_self_approve(approval: dict[str, Any], claims: dict[str, Any] | None) -> bool:
    if claims is None or not _is_remote_input_approval(approval):
        return False
    if _text(claims.get("source")) != "remote_input_grant":
        return False
    claim_grant_id = _text(claims.get("grant_id"))
    if not claim_grant_id:
        return False
    return claim_grant_id == _approval_source_grant_id(approval)


def _mobile_approval_denial_reason(approval: dict[str, Any], claims: dict[str, Any] | None) -> str:
    if claims is None:
        return ""

    device_id = _text(claims.get("device_id"))
    if not device_id:
        return "Mobile token is missing a device binding."
    if not is_mobile_device_active(device_id):
        return "Mobile device has been revoked."

    scopes = mobile_token_scopes(claims)
    if not scopes:
        return "Mobile token is missing an approval scope."

    allowed_devices = _approval_allowed_device_ids(approval)
    if allowed_devices and device_id not in allowed_devices:
        return "Mobile token is not allowed to access this approval."

    approval_source = _approval_source(approval)
    claim_source = _text(claims.get("source"))
    if claim_source == "remote_input_grant" and not _is_remote_input_approval(approval):
        return "Remote input grant token is not allowed for ordinary approvals."
    if approval_source and claim_source and approval_source != claim_source:
        return "Mobile token source is not allowed for this approval."

    if _is_remote_input_approval(approval):
        if REMOTE_INPUT_SCOPE not in scopes:
            return "Remote input approvals require a remote input scope."
        claim_grant_id = _text(claims.get("grant_id"))
        if _text(claims.get("source")) != "remote_input_grant" or not claim_grant_id:
            return "Remote input approvals require a remote input grant."
        if not allowed_devices:
            return "Remote input approval is missing a device binding."
        approval_grant_id = _approval_source_grant_id(approval)
        if not approval_grant_id:
            return "Remote input approval is missing a grant binding."
        if claim_grant_id != approval_grant_id:
            return "Remote input grant is not allowed to access this approval."
        return ""

    required_scopes = _approval_required_mobile_scopes(approval) or {TOKEN_SCOPE}
    if not scopes.intersection(required_scopes):
        return "Mobile token scope is not allowed for this approval."
    return ""


def _paired_mobile_claims_can_read_remote_input_approval(
    approval: dict[str, Any],
    claims: dict[str, Any] | None,
) -> bool:
    if claims is None or not _is_remote_input_approval(approval):
        return False
    device_id = _text(claims.get("device_id"))
    if not device_id or not is_mobile_device_active(device_id):
        return False
    scopes = mobile_token_scopes(claims)
    if TOKEN_SCOPE not in scopes:
        return False
    allowed_devices = _approval_allowed_device_ids(approval)
    if not allowed_devices or device_id not in allowed_devices:
        return False
    if not _approval_source_grant_id(approval):
        return False
    approval_source = _approval_source(approval)
    claim_source = _text(claims.get("source"))
    return not approval_source or not claim_source or approval_source == claim_source


def _paired_mobile_claims_can_reject_remote_input_approval(
    approval: dict[str, Any],
    claims: dict[str, Any] | None,
) -> bool:
    if _text((claims or {}).get("source")) == "remote_input_grant":
        return False
    return _paired_mobile_claims_can_read_remote_input_approval(approval, claims)


def _is_remote_input_approval(approval: dict[str, Any]) -> bool:
    approval_type = _text(approval.get("approval_type")).lower()
    source = _text(approval.get("source")).lower()
    return approval_type == "remote_input" or source == "remote_input"


def _approval_source(approval: dict[str, Any]) -> str:
    source = _text(approval.get("source")).lower()
    return "" if source in {"", "tool_call", "remote_input"} else source


def _approval_required_mobile_scopes(approval: dict[str, Any]) -> set[str]:
    return set(_text_list(approval.get("required_mobile_scopes")))


def _approval_allowed_device_ids(approval: dict[str, Any]) -> set[str]:
    device_ids = set(_text_list(approval.get("allowed_device_ids")))
    source_device_id = _text(approval.get("source_device_id"))
    if source_device_id:
        device_ids.add(source_device_id)
    if _is_remote_input_approval(approval):
        audit_device_id = _remote_input_source_device_id_from_audit(approval)
        if audit_device_id:
            device_ids.add(audit_device_id)
    return device_ids


def _approval_source_grant_id(approval: dict[str, Any]) -> str:
    grant_id = _text(approval.get("source_grant_id"))
    if grant_id:
        return grant_id
    return _remote_input_source_grant_id_from_audit(approval)


def _remote_input_source_device_id_from_audit(approval: dict[str, Any]) -> str:
    payload = _remote_input_approval_audit_payload(approval)
    return _text(payload.get("device_id")) if payload else ""


def _remote_input_source_grant_id_from_audit(approval: dict[str, Any]) -> str:
    payload = _remote_input_approval_audit_payload(approval)
    return _text(payload.get("grant_id")) if payload else ""


def _remote_input_approval_audit_payload(approval: dict[str, Any]) -> dict[str, Any] | None:
    approval_id = _text(approval.get("id"))
    task_id = _text(approval.get("task_id"))
    if not approval_id or not task_id:
        return None
    for event in db.fetch_many("audit_events", "task_id = ?", (task_id,), limit=100):
        if event.get("event_type") != "remote.input.approval_requested":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict) or _text(payload.get("approval_id")) != approval_id:
            continue
        return payload
    return None


def is_mobile_device_active(device_id: str) -> bool:
    normalized_id = _text(device_id)
    if not normalized_id:
        return False
    device = db.fetch_one("mobile_devices", normalized_id)
    if not device:
        return False
    return str(device.get("status") or "active").lower() == "active"
