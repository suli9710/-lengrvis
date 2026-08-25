from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def build_local_product_diagnostics(
    *,
    sample_size: int,
    database_present: bool,
    tasks: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    mobile_devices: list[dict[str, Any]],
    mobile_pairings: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    audits: list[dict[str, Any]],
) -> dict[str, Any]:
    task_states = [_effective_task_state(item) for item in tasks]
    task_success_count = sum(1 for state in task_states if _is_success_state(state))
    task_failure_count = sum(1 for state in task_states if _is_failed_state(state))
    run_success_count = sum(1 for item in runs if _is_success_state(item.get("phase")))
    run_failure_count = sum(1 for item in runs if _is_failed_state(item.get("phase")))
    tool_result_success_count = sum(1 for item in tool_results if item.get("ok") is True)
    tool_result_failure_count = sum(1 for item in tool_results if item.get("ok") is False)
    approval_status_counts = _status_counts(approvals, "status")
    mobile_device_status_counts = _status_counts(mobile_devices, "status", default="active")
    mobile_pairing_status_counts = _status_counts(mobile_pairings, "status")
    remote_input_grant_counts = _remote_input_grant_counts(mobile_devices)
    audit_failure_like_count = sum(
        1
        for item in audits
        if any(token in str(item.get("event_type") or "").casefold() for token in ("fail", "error"))
    )

    latest_audit_event = None
    if audits:
        latest = audits[0]
        latest_audit_event = {
            "id": latest.get("id"),
            "event_type": latest.get("event_type"),
            "sequence": latest.get("sequence"),
            "created_at": latest.get("created_at"),
        }

    product_metrics = {
        "schema_version": 1,
        "sample_size": sample_size,
        "paired_devices_count": int(mobile_device_status_counts.get("active", 0)),
        "active_remote_input_grants_count": int(remote_input_grant_counts.get("active", 0)),
        "paired_devices": {
            "total": len(mobile_devices),
            "active": int(mobile_device_status_counts.get("active", 0)),
            "revoked": int(mobile_device_status_counts.get("revoked", 0)),
        },
        "mobile_pairings": {
            "recent_total": len(mobile_pairings),
            "pending": int(mobile_pairing_status_counts.get("pending", 0)),
            "used": int(mobile_pairing_status_counts.get("used", 0)),
            "expired": int(mobile_pairing_status_counts.get("expired", 0)),
        },
        "remote_input_grants": remote_input_grant_counts,
        "tasks": {
            "recent_total": len(tasks),
            "recent_success": task_success_count,
            "recent_failure": task_failure_count,
            "by_status": _status_counts(tasks, "status", "phase"),
        },
        "runs": {
            "recent_total": len(runs),
            "recent_success": run_success_count,
            "recent_failure": run_failure_count,
            "by_phase": _status_counts(runs, "phase"),
        },
        "approvals": {
            "recent_total": len(approvals),
            "pending": int(approval_status_counts.get("pending", 0)),
            "approved": int(approval_status_counts.get("approved", 0)),
            "rejected": int(approval_status_counts.get("rejected", 0)),
            "expired": int(approval_status_counts.get("expired", 0)),
            "consumed": sum(1 for item in approvals if item.get("consumed_at")),
        },
        "tool_results": {
            "recent_total": len(tool_results),
            "recent_success": tool_result_success_count,
            "recent_failure": tool_result_failure_count,
        },
    }
    product_funnel = {
        "schema_version": 1,
        "first_launch": {
            "local_database_present": database_present,
            "audit_events_recent_count": len(audits),
            "latest_audit_event_type": latest_audit_event.get("event_type") if latest_audit_event else "",
        },
        "pairing": {
            "paired_devices_count": product_metrics["paired_devices_count"],
            "pairings_recent_used_count": product_metrics["mobile_pairings"]["used"],
            "pairings_recent_pending_count": product_metrics["mobile_pairings"]["pending"],
        },
        "remote_input": {
            "active_remote_input_grants_count": product_metrics["active_remote_input_grants_count"],
            "remote_input_grants_recent_total": remote_input_grant_counts["total"],
        },
        "first_task": {
            "tasks_recent_total": len(tasks),
            "tasks_recent_success_count": task_success_count,
            "tasks_recent_failure_count": task_failure_count,
            "runs_recent_total": len(runs),
            "runs_recent_success_count": run_success_count,
            "runs_recent_failure_count": run_failure_count,
        },
        "approval_response": {
            "approval_pending_count": product_metrics["approvals"]["pending"],
            "approval_approved_count": product_metrics["approvals"]["approved"],
            "approval_rejected_count": product_metrics["approvals"]["rejected"],
            "approval_expired_count": product_metrics["approvals"]["expired"],
        },
    }

    return {
        "sample_size": sample_size,
        "recent_counts": {
            "tasks": len(tasks),
            "runs": len(runs),
            "approvals": len(approvals),
            "mobile_devices": len(mobile_devices),
            "mobile_pairings": len(mobile_pairings),
            "tool_results": len(tool_results),
            "audit_events": len(audits),
        },
        "recent_success_counts": {
            "tasks_completed": task_success_count,
            "runs_completed": run_success_count,
            "tool_results_ok": tool_result_success_count,
        },
        "recent_failure_counts": {
            "tasks_failed": task_failure_count,
            "runs_failed": run_failure_count,
            "tool_results_failed": tool_result_failure_count,
            "audit_events_failure_like": audit_failure_like_count,
        },
        "product_metrics": product_metrics,
        "product_funnel": product_funnel,
        "latest_audit_event": latest_audit_event,
    }


def _status_counts(items: list[dict[str, Any]], *fields: str, default: str = "unknown") -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = _first_text(item, *fields) or default
        key = status.casefold()
        counts[key] = counts.get(key, 0) + 1
    return counts


def _remote_input_grant_counts(devices: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": 0, "active": 0, "expired": 0, "revoked": 0, "unknown": 0}
    now = datetime.now(UTC)
    for device in devices:
        grants = device.get("remote_input_grants") or []
        if not isinstance(grants, list):
            continue
        for raw_grant in grants:
            if not isinstance(raw_grant, dict):
                continue
            counts["total"] += 1
            status = _remote_input_grant_status(raw_grant, now)
            counts[status] = counts.get(status, 0) + 1
    return counts


def _remote_input_grant_status(grant: dict[str, Any], now: datetime) -> str:
    status = _first_text(grant, "status") or "active"
    if status == "active":
        expires_at = _parse_iso_datetime(grant.get("expires_at"))
        if expires_at is not None and expires_at <= now:
            return "expired"
    if status in {"active", "expired", "revoked"}:
        return status
    return "unknown"


def _first_text(item: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = item.get(field)
        text = str(getattr(value, "value", value) or "").strip()
        if text:
            return text
    return ""


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_success_state(value: Any) -> bool:
    return str(getattr(value, "value", value) or "").casefold() in {
        "completed",
        "complete",
        "success",
        "succeeded",
        "done",
        "rolled_back",
    }


def _effective_task_state(item: dict[str, Any]) -> Any:
    status = item.get("status") or item.get("phase")
    status_text = str(getattr(status, "value", status) or "").strip().lower()
    rollback = (item.get("metadata") or {}).get("rollback")
    if status_text == "failed" and isinstance(rollback, dict) and rollback:
        return "rolled_back" if str(rollback.get("state") or "").strip().lower() == "succeeded" else "repair_required"
    return status


def _is_failed_state(value: Any) -> bool:
    return str(getattr(value, "value", value) or "").casefold() in {
        "failed",
        "failure",
        "error",
        "repair_required",
    }
