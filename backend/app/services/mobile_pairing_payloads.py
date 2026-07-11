from __future__ import annotations

from typing import Any

from app.core import db
from app.core.schemas import Approval, Task
from app.policy.approval_binding import redacted_preview, remote_input_binding_ref
from app.policy.redaction import contains_sensitive_key, redact_public_text, redact_value
from app.security.mobile_jwt import REMOTE_INPUT_SCOPE
from app.services.mobile_pairing_access import (
    _approval_allowed_device_ids,
    _approval_required_mobile_scopes,
    _approval_source_grant_id,
    _is_remote_input_approval,
    _mobile_approval_requires_step_up,
    _mobile_claims_have_fresh_step_up,
)
from app.services.mobile_pairing_common import _text, mobile_device_trust_metadata
from app.services.mobile_pairing_remote_input import _normalized_remote_input_grants, _safe_remote_input_grant


def _safe_mobile_device_payload(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_id": device.get("device_id") or device.get("id") or "",
        "device_name": device.get("device_name") or "Android device",
        "status": str(device.get("status") or "active").lower(),
        "created_at": device.get("created_at") or "",
        "updated_at": device.get("updated_at") or "",
        "revoked_at": device.get("revoked_at") or "",
        "device_trust": _safe_mobile_device_trust(device),
        "remote_input_grants": _safe_remote_input_grants(device),
    }


def _safe_mobile_device_trust(device: dict[str, Any]) -> dict[str, Any]:
    trust = device.get("device_trust")
    if not isinstance(trust, dict):
        return mobile_device_trust_metadata()
    return {
        "attestation_verified": False,
        "attestation_status": "not_verified",
        "attestation_provider": "none",
        "trust_basis": _text(trust.get("trust_basis")) or "pairing_code_tls",
        "hardware_backed": False,
        "message": _text(trust.get("message")) or mobile_device_trust_metadata()["message"],
    }


def _safe_remote_input_grants(device: dict[str, Any]) -> list[dict[str, Any]]:
    return [_safe_remote_input_grant(grant) for grant in _normalized_remote_input_grants(device)]


def _latest_plan(task_id: str) -> dict[str, Any] | None:
    plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
    return plans[0] if plans else None


def _safe_approval_payload(approval: dict[str, Any], claims: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(approval)
    is_remote_input = _is_remote_input_approval(payload)
    payload["message"] = _safe_mobile_text(payload.get("message") or "")
    payload["diff_preview"] = redacted_preview(payload.get("diff_preview") or {})
    payload["model_action"] = _safe_mobile_model_action(payload.get("model_action") or {})
    payload["mobile_step_up_required"] = _mobile_approval_requires_step_up(approval)
    payload["mobile_step_up_satisfied"] = _mobile_claims_have_fresh_step_up(claims)
    for key in (
        "dry_run_summary",
        "tool_effects",
        "resource_kinds",
        "runtime_control_fields",
        "runtime_fields",
        "engineering_boundary",
    ):
        if key in payload:
            payload[key] = _safe_mobile_public_value(payload.get(key), key=key)
    if is_remote_input:
        payload["remote_input_binding"] = _remote_input_public_binding_state(approval, claims)
        for key in ("source_device_id", "source_grant_id", "allowed_device_ids"):
            payload.pop(key, None)
    return payload


def _remote_input_public_binding_state(
    approval: dict[str, Any],
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required_scopes = _approval_required_mobile_scopes(approval)
    allowed_devices = _approval_allowed_device_ids(approval)
    approval_grant_id = _approval_source_grant_id(approval)
    device_id = _text((claims or {}).get("device_id"))
    claim_grant_id = _text((claims or {}).get("grant_id"))
    state = {
        "device_bound": bool(allowed_devices),
        "grant_bound": bool(approval_grant_id),
        "requires_remote_input_scope": REMOTE_INPUT_SCOPE in required_scopes,
        "binding_ref": remote_input_binding_ref(approval_grant_id),
    }
    if device_id:
        state["matches_current_device"] = bool(allowed_devices and device_id in allowed_devices)
    if claim_grant_id:
        state["matches_current_grant"] = claim_grant_id == approval_grant_id
    return state


def _safe_mobile_model_action(model_action: Any) -> dict[str, Any]:
    if not isinstance(model_action, dict):
        return {}
    safe = dict(model_action)
    if "args" in safe:
        safe["args"] = _safe_mobile_model_action_args(safe.get("args"))
    for key in ("raw", "input", "inputs", "output", "observation", "prompt", "content", "text", "value"):
        if key in safe:
            safe[key] = _safe_mobile_public_value(safe[key], key=key)
    safe["redacted"] = True
    return _safe_mobile_public_value(safe)


def _safe_mobile_model_action_args(raw_args: Any) -> Any:
    if isinstance(raw_args, dict):
        return {"redacted": True, "field_count": len(raw_args)}
    if isinstance(raw_args, list | tuple | set):
        return {"redacted": True, "field_count": len(raw_args)}
    if raw_args in (None, ""):
        return {}
    return "[REDACTED]"


def _safe_mobile_text(value: Any) -> str:
    return redact_public_text(str(redact_value(str(value or "")) or ""))


def _safe_mobile_public_value(value: Any, *, key: str = "") -> Any:
    normalized_key = key.replace("-", "_").casefold()
    if normalized_key == "model_action":
        return _safe_mobile_model_action(value)
    if normalized_key == "risk_level" and isinstance(value, str):
        normalized_risk = _safe_mobile_risk_level(value)
        if normalized_risk:
            return normalized_risk
    if contains_sensitive_key(key):
        value = redact_value({key: value}).get(key)
    if isinstance(value, dict):
        return {item_key: _safe_mobile_public_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_safe_mobile_public_value(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_safe_mobile_public_value(item, key=key) for item in value]
    if isinstance(value, set):
        return [_safe_mobile_public_value(item, key=key) for item in sorted(value, key=str)]
    if isinstance(value, str):
        redacted = redact_value(value)
        return redact_public_text(str(redacted or ""))
    return value


def _safe_mobile_risk_level(value: str) -> str:
    normalized = value.strip()
    allowed = {
        "R0_READ_ONLY",
        "R1_OPEN_ONLY",
        "R2_REVERSIBLE_MODIFY",
        "R3_DESTRUCTIVE_OR_SYSTEM",
        "R4_FORBIDDEN_OR_HANDOFF",
    }
    if normalized.upper() not in allowed:
        return ""
    return normalized.lower().replace("_", " ")


def _safe_mobile_task(task: Task) -> dict[str, Any]:
    payload = task.model_dump(mode="json")
    for key in ("user_goal", "final_summary"):
        payload[key] = _safe_mobile_text(payload.get(key) or "")
    payload["metadata"] = _safe_mobile_task_metadata(payload.get("metadata"))
    return payload


def _safe_mobile_task_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict) or not metadata:
        return {}
    return {"redacted": True, "field_count": len(metadata)}


def _safe_mobile_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    safe = dict(plan)
    safe["goal"] = _safe_mobile_text(safe.get("goal") or "")
    safe["assumptions"] = _safe_mobile_public_value(safe.get("assumptions") or [], key="assumptions")
    safe_steps: list[dict[str, Any]] = []
    for raw_step in safe.get("steps") or []:
        if not isinstance(raw_step, dict):
            continue
        safe_steps.append(
            {
                "id": raw_step.get("id") or "",
                "order": raw_step.get("order") or 0,
                "agent_name": raw_step.get("agent_name") or "",
                "tool_name": raw_step.get("tool_name") or "",
                "description": _safe_mobile_text(raw_step.get("description") or ""),
                "status": raw_step.get("status") or "",
                "risk_level": raw_step.get("risk_level") or "",
                "requires_approval": bool(raw_step.get("requires_approval")),
                "tool_effects": _safe_mobile_public_value(raw_step.get("tool_effects") or [], key="tool_effects"),
                "resource_kinds": _safe_mobile_public_value(raw_step.get("resource_kinds") or [], key="resource_kinds"),
                "trust_tier": raw_step.get("trust_tier") or "",
                "deferred_tool": bool(raw_step.get("deferred_tool")),
                "expected_observation": _safe_mobile_text(raw_step.get("expected_observation") or ""),
            }
        )
    safe["steps"] = safe_steps
    return safe


def safe_approval_payload(approval: Approval | dict[str, Any], claims: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    return _safe_approval_payload(payload, claims)
