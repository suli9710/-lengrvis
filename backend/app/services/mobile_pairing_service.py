from __future__ import annotations

import json
import secrets
import socket
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.core import db
from app.core.schemas import Approval, ApprovalStatus, Task, now_iso
from app.orchestration.execution_stage import ExecutionStage
from app.policy.approval_binding import redacted_preview
from app.policy.redaction import redact_value
from app.security.mobile_jwt import decode_mobile_token, issue_mobile_token, new_device_id

PAIR_CODE_TTL_SECONDS = 300
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30


def create_pairing_request() -> dict[str, Any]:
    db.init_db()
    _expire_stale_pairings()

    now = time.time()
    code = _unique_code()
    record = {
        "id": code,
        "code": code,
        "status": "pending",
        "device_id": "",
        "device_name": "",
        "created_at": _iso(now),
        "expires_at": _iso(now + PAIR_CODE_TTL_SECONDS),
        "used_at": None,
        "updated_at": _iso(now),
        "server": _server_info(),
    }
    _write_pairing_record(record)
    return {
        "code": code,
        "expires_at": record["expires_at"],
        "expires_in": PAIR_CODE_TTL_SECONDS,
        "server": record["server"],
    }


def confirm_pairing(*, code: str, device_name: str) -> dict[str, Any]:
    db.init_db()
    _expire_stale_pairings()

    normalized = _normalize_code(code)
    if len(normalized) != 6:
        raise HTTPException(status_code=422, detail="Pairing code must be 6 characters")

    record = _load_pairing_record(normalized)
    if record is None or record.get("status") != "pending":
        raise HTTPException(status_code=401, detail="Pairing code is invalid or expired")
    if _parse_iso(str(record["expires_at"])) <= time.time():
        _expire_pairing_record(record)
        raise HTTPException(status_code=401, detail="Pairing code is invalid or expired")

    device_id = new_device_id()
    device_name = device_name or "Android device"
    token = issue_mobile_token(device_id=device_id, device_name=device_name, expires_in_seconds=TOKEN_TTL_SECONDS)
    updated = dict(record)
    updated.update(
        {
            "status": "used",
            "device_id": device_id,
            "device_name": device_name,
            "used_at": now_iso(),
            "updated_at": now_iso(),
        }
    )
    _write_pairing_record(updated)
    _upsert_mobile_device(device_id=device_id, device_name=device_name)
    return {
        "token": token,
        "token_type": "Bearer",
        "device_id": device_id,
        "expires_in": TOKEN_TTL_SECONDS,
        "server": _server_info(),
    }


def list_pending_approvals() -> list[dict[str, Any]]:
    return [_safe_approval_payload(row) for row in db.fetch_many("approvals", "status = ?", ("pending",))]


def get_approval_detail(approval_id: str) -> dict[str, Any]:
    approval_data = db.fetch_one("approvals", approval_id)
    if not approval_data:
        raise HTTPException(status_code=404, detail="Approval not found")

    approval = Approval.model_validate(approval_data)
    task_data = db.fetch_one("tasks", approval.task_id)
    task = Task.model_validate(task_data) if task_data else None
    plan = _latest_plan(task.id if task else approval.task_id)
    approval_payload = _safe_approval_payload(approval.model_dump(mode="json"))
    return {
        "approval": approval_payload,
        "task": _safe_mobile_task(task) if task else None,
        "plan": _safe_mobile_plan(plan),
        "preview": approval_payload.get("diff_preview", {}),
    }


def list_mobile_devices() -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for row in db.fetch_many("mobile_devices", limit=100):
        devices.append(
            {
                "device_id": row.get("device_id") or row.get("id") or "",
                "device_name": row.get("device_name") or "Android device",
                "created_at": row.get("created_at") or "",
                "updated_at": row.get("updated_at") or "",
            }
        )
    return devices


def approve_approval(approval_id: str) -> Approval:
    return _decide_approval(approval_id, ApprovalStatus.APPROVED)


def reject_approval(approval_id: str) -> Approval:
    return _decide_approval(approval_id, ApprovalStatus.REJECTED)


def validate_mobile_token(token: str) -> dict[str, Any]:
    return decode_mobile_token(token)


def _write_pairing_record(record: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO mobile_pairings (id, data, status, created_at, expires_at, used_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                data=excluded.data,
                status=excluded.status,
                created_at=excluded.created_at,
                expires_at=excluded.expires_at,
                used_at=excluded.used_at,
                updated_at=excluded.updated_at
            """,
            (
                record["id"],
                json.dumps(record, ensure_ascii=False),
                record["status"],
                record["created_at"],
                record["expires_at"],
                record["used_at"],
                record["updated_at"],
            ),
        )


def _load_pairing_record(code: str) -> dict[str, Any] | None:
    return db.fetch_one("mobile_pairings", code)


def _expire_pairing_record(record: dict[str, Any]) -> None:
    updated = dict(record)
    updated["status"] = "expired"
    updated["updated_at"] = now_iso()
    _write_pairing_record(updated)


def _expire_stale_pairings() -> None:
    now = time.time()
    for record in db.fetch_many("mobile_pairings", limit=500):
        if record.get("status") != "pending":
            continue
        expires_at = _parse_iso(str(record.get("expires_at") or ""))
        if expires_at <= now:
            _expire_pairing_record(record)


def _upsert_mobile_device(*, device_id: str, device_name: str) -> None:
    body = {
        "id": device_id,
        "device_id": device_id,
        "device_name": device_name,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO mobile_devices (id, data, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
            """,
            (device_id, json.dumps(body, ensure_ascii=False), body["created_at"], body["updated_at"]),
        )


def _decide_approval(approval_id: str, status: ApprovalStatus) -> Approval:
    existing = db.fetch_one("approvals", approval_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Approval not found")
    existing_approval = Approval.model_validate(existing)
    if existing_approval.consumed_at:
        raise HTTPException(status_code=409, detail="Approval has already been consumed.")
    if existing_approval.status != ApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Approval is already {existing_approval.status}.")

    if status == ApprovalStatus.APPROVED:
        state_error = _approval_state_error(approval_id)
        if state_error:
            expired = db.expire_approval_if_pending(approval_id, now_iso(), state_error)
            if expired:
                from app.services.approval_event_service import publish_approval_decided

                publish_approval_decided(Approval.model_validate(expired))
            raise HTTPException(status_code=409, detail="Approval is no longer executable.")
    data = db.decide_approval_atomically(approval_id, status.value, now_iso())
    if not data:
        existing_approval = Approval.model_validate(db.fetch_one("approvals", approval_id) or existing)
        raise HTTPException(status_code=409, detail=f"Approval is already {existing_approval.status}.")

    from app.services.approval_event_service import publish_approval_decided

    approval = Approval.model_validate(data)
    publish_approval_decided(approval)
    return approval


def _approval_state_error(approval_id: str) -> str:
    data = db.fetch_one("approvals", approval_id)
    if not data:
        return ""
    approval = Approval.model_validate(data)
    task_data = db.fetch_one("tasks", approval.task_id)
    if not task_data:
        return f"Task not found: {approval.task_id}"
    task = Task.model_validate(task_data)
    if task.execution_stage != ExecutionStage.AWAITING_APPROVAL:
        return f"Task execution stage is {task.execution_stage}; expected awaiting_approval."
    plan = _latest_plan(task.id)
    if not plan:
        return f"Plan not found for task: {task.id}"
    step = next((item for item in plan.get("steps", []) if item.get("id") == approval.step_id), None)
    if not step:
        return f"Step not found for approval: {approval.step_id}"
    if step.get("status") != "waiting_user_approval":
        return f"Step status is {step.get('status')}; expected waiting_user_approval."
    return ""


def _latest_plan(task_id: str) -> dict[str, Any] | None:
    plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
    return plans[0] if plans else None


def _safe_approval_payload(approval: dict[str, Any]) -> dict[str, Any]:
    payload = dict(approval)
    payload["message"] = redact_value(payload.get("message") or "")
    payload["diff_preview"] = redacted_preview(payload.get("diff_preview") or {})
    return payload


def _safe_mobile_task(task: Task) -> dict[str, Any]:
    payload = task.model_dump(mode="json")
    for key in ("user_goal", "final_summary"):
        payload[key] = redact_value(payload.get(key) or "")
    return payload


def _safe_mobile_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    safe = dict(plan)
    safe["goal"] = redact_value(safe.get("goal") or "")
    safe["assumptions"] = redact_value(safe.get("assumptions") or [])
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
                "description": redact_value(raw_step.get("description") or ""),
                "status": raw_step.get("status") or "",
                "requires_approval": bool(raw_step.get("requires_approval")),
                "expected_observation": redact_value(raw_step.get("expected_observation") or ""),
            }
        )
    safe["steps"] = safe_steps
    return safe


def safe_approval_payload(approval: Approval | dict[str, Any]) -> dict[str, Any]:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    return _safe_approval_payload(payload)


def _unique_code() -> str:
    for _ in range(100):
        code = secrets.token_hex(3)
        if not db.fetch_one("mobile_pairings", code):
            return code
    raise HTTPException(status_code=503, detail="Unable to allocate a pairing code")


def _normalize_code(code: str) -> str:
    return "".join(character for character in code if character.isalnum()).lower()


def _server_info() -> dict[str, Any]:
    return {
        "host": _lan_ip(),
        "port": _backend_port(),
    }


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def _backend_port() -> int:
    import os

    return int(os.environ.get("MAVRIS_BACKEND_PORT") or os.environ.get("MARVIS_BACKEND_PORT") or "8000")


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _parse_iso(value: str) -> float:
    if not value:
        return 0.0
    return datetime.fromisoformat(value).timestamp()
