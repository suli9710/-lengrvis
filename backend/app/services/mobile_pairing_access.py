from __future__ import annotations

import hmac
import json
import re
from typing import Any

from fastapi import HTTPException

from app.core import db
from app.core.schemas import Approval
from app.security.mobile_jwt import (
    REMOTE_INPUT_SCOPE,
    TOKEN_SCOPE,
    mobile_token_has_fresh_step_up,
    mobile_token_scopes,
)
from app.services.mobile_pairing_common import _text, _text_list

_HIGH_IMPACT_MOBILE_EFFECTS = {
    "account_security",
    "credential",
    "delete",
    "destructive",
    "execute",
    "execute_process",
    "execute_subprocess",
    "external_post",
    "install",
    "payment",
    "permission_expansion",
    "privileged",
    "process",
    "purchase",
    "registry",
    "send",
    "subprocess",
    "submit",
    "system_write",
    "trash",
    "uninstall",
    "upload",
}
_BROWSER_ACTION_TOOLS = {"browser.act", "browser.cua_run"}
_BROWSER_HIGH_IMPACT_MARKERS = {
    "account_security",
    "change_password",
    "checkout",
    "order",
    "payment",
    "purchase",
    "reset_password",
    "send",
    "submit",
    "upload",
}
_BROWSER_SEMANTIC_KEYS = {"action", "action_type", "kind", "selector", "url"}


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
    if (
        claims is not None
        and _mobile_approval_requires_step_up(approval)
        and not _mobile_claims_have_fresh_step_up(claims)
    ):
        return "High-impact mobile approval requires a fresh biometric step-up."
    return ""


def _mobile_claims_have_fresh_step_up(claims: dict[str, Any] | None) -> bool:
    if not mobile_token_has_fresh_step_up(claims, required_method="biometric"):
        return False
    payload = claims or {}
    credential_id = _text(payload.get("credential_id"))
    device_id = _text(payload.get("device_id"))
    confirmation = payload.get("cnf")
    proof_thumbprint = _text(confirmation.get("jkt")) if isinstance(confirmation, dict) else ""
    if not credential_id or not device_id or not proof_thumbprint:
        return False
    with db.connect() as conn:
        row = conn.execute(
            "SELECT device_id, status, data FROM device_credentials WHERE id = ?",
            (credential_id,),
        ).fetchone()
    if not row:
        return False
    try:
        credential = json.loads(row["data"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(credential, dict):
        return False
    credential.update({"device_id": row["device_id"], "status": row["status"]})
    return bool(
        _text(credential.get("device_id")) == device_id
        and _text(credential.get("status")).casefold() == "active"
        and credential.get("hardware_backed") is True
        and credential.get("attestation_verified") is True
        and hmac.compare_digest(
            _text(credential.get("public_key_thumbprint")).encode("utf-8"),
            proof_thumbprint.encode("utf-8"),
        )
    )


def _mobile_approval_requires_step_up(approval: dict[str, Any]) -> bool:
    if approval.get("mobile_step_up_required") is True:
        return True
    boundary = approval.get("engineering_boundary")
    if isinstance(boundary, dict) and boundary.get("mobile_step_up_required") is True:
        return True
    risk_provenance = boundary.get("risk_provenance") if isinstance(boundary, dict) else None
    risk_provenance = risk_provenance if isinstance(risk_provenance, dict) else {}
    boundary_tool = boundary.get("tool") if isinstance(boundary, dict) else None
    boundary_tool = boundary_tool if isinstance(boundary_tool, dict) else {}
    risk_text = " ".join(
        (
            _text(approval.get("risk_level")),
            _text(risk_provenance.get("effective_risk_level")),
            _text(boundary_tool.get("risk_level")),
        )
    ).casefold()
    if any(marker in risk_text for marker in ("r3", "destructive", "system", "critical")):
        return True
    if boundary_tool.get("destructive") is True:
        return True
    effects = {
        effect.lower()
        for effect in [
            *_text_list(approval.get("tool_effects")),
            *_text_list(boundary_tool.get("effects")),
        ]
    }
    if effects.intersection(_HIGH_IMPACT_MOBILE_EFFECTS):
        return True
    if any(
        marker in effect
        for effect in effects
        for marker in (
            "credential",
            "delete",
            "destructive",
            "execute",
            "external_send",
            "external_post",
            "install",
            "payment",
            "permission",
            "privileged",
            "process",
            "purchase",
            "registry",
            "send",
            "submit",
            "system_write",
            "trash",
            "uninstall",
            "upload",
        )
    ):
        return True
    permission_text = " ".join(
        _text(value)
        for value in (
            approval.get("permission_mode"),
            approval.get("policy_mode"),
            boundary.get("permission_mode") if isinstance(boundary, dict) else "",
            boundary.get("policy_mode") if isinstance(boundary, dict) else "",
        )
    ).casefold()
    if re.search(r"full.?access|unrestricted|bypass|override|admin|root|privileged", permission_text):
        return True
    if _browser_action_parameters_require_step_up(approval):
        return True
    action = f"{_text(approval.get('approval_type'))} {_text(approval.get('tool_name'))}".lower()
    return any(
        marker in action
        for marker in (
            "account_security",
            "browser.submit",
            "credential",
            "delete",
            "execute",
            "external_send",
            "install",
            "payment",
            "permission",
            "privileged",
            "purchase",
            "registry",
            "send_message",
            "subprocess",
            "submit_form",
            "system",
            "trash",
            "uninstall",
            "upload",
        )
    )


def _browser_action_parameters_require_step_up(approval: dict[str, Any]) -> bool:
    tool_name = _text(approval.get("tool_name")).casefold()
    if tool_name not in _BROWSER_ACTION_TOOLS:
        return False
    return any(
        _contains_high_impact_browser_marker(value)
        for value in _browser_semantic_values(
            approval,
            include_instructions=tool_name == "browser.cua_run",
        )
    )


def _browser_semantic_values(value: Any, *, include_instructions: bool) -> list[str]:
    values: list[str] = []

    def visit(item: Any, *, key: str = "") -> None:
        normalized_key = key.strip().replace("-", "_").casefold()
        semantic_key = normalized_key in _BROWSER_SEMANTIC_KEYS or normalized_key.endswith(
            ("_action", "_kind", "_selector", "_url")
        )
        if include_instructions and normalized_key in {"command", "instruction", "text"}:
            semantic_key = True
        if isinstance(item, dict):
            for child_key, child in item.items():
                visit(child, key=str(child_key))
            return
        if isinstance(item, list | tuple | set):
            for child in item:
                visit(child, key=key)
            return
        if semantic_key and item not in (None, ""):
            values.append(str(item))

    visit(value)
    return values


def _contains_high_impact_browser_marker(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
    if not normalized:
        return False
    if any(marker in normalized for marker in {"account_security", "change_password", "reset_password"}):
        return True
    tokens = {token for token in normalized.split("_") if token}
    simple_markers = _BROWSER_HIGH_IMPACT_MARKERS.difference({"account_security", "change_password", "reset_password"})
    return any(token == marker or token.startswith(marker) for token in tokens for marker in simple_markers)


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
