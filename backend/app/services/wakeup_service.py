from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from app.core import db
from app.core.schemas import Wakeup, WakeupStatus, now_iso
from app.policy.redaction import redact_public_text, redact_value
from app.security.mobile_jwt import TOKEN_SCOPE, mobile_token_scopes
from app.services import notification_service
from app.services.mobile_pairing_service import is_mobile_device_active


def list_pending_wakeups() -> list[dict[str, Any]]:
    return db.fetch_many("wakeups", "status = ?", (WakeupStatus.PENDING.value,), limit=200)


def list_pending_mobile_wakeups(claims: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        safe_wakeup_payload(item, claims)
        for item in list_pending_wakeups()
        if _mobile_claims_allow_wakeup_for_read(item, claims)
    ]


def get_wakeup(wakeup_id: str) -> Wakeup:
    data = db.fetch_one("wakeups", wakeup_id)
    if not data:
        raise HTTPException(status_code=404, detail="Wakeup not found")
    return Wakeup.model_validate(data)


def create_schedule_wakeup(schedule: Any, *, due_at: str) -> Wakeup:
    wakeup, created = _create_schedule_wakeup_atomically(schedule, due_at=due_at)
    if not created:
        return wakeup
    notification_service.notify(
        "Scheduled task ready",
        schedule.goal,
        task_id=wakeup.id,
        severity="info",
    )
    return wakeup


def _create_schedule_wakeup_atomically(schedule: Any, *, due_at: str) -> tuple[Wakeup, bool]:
    wakeup = Wakeup(
        source="schedule",
        source_id=schedule.id,
        title="Scheduled task ready",
        body=schedule.goal,
        goal=schedule.goal,
        mode=schedule.mode,
        due_at=due_at,
        allowed_device_ids=_active_mobile_device_ids(),
    )
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT data FROM wakeups
            WHERE source = ? AND source_id = ? AND status = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            ("schedule", schedule.id, WakeupStatus.PENDING.value),
        ).fetchone()
        if existing is not None:
            return Wakeup.model_validate(json.loads(existing["data"])), False

        data = wakeup.model_dump(mode="json")
        conn.execute(
            """
            INSERT INTO wakeups (id, source, source_id, status, due_at, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["id"],
                data["source"],
                data["source_id"],
                data["status"],
                data["due_at"],
                json.dumps(data, ensure_ascii=False),
                data["created_at"],
                data["updated_at"],
            ),
        )
    return wakeup, True


def approve_wakeup(wakeup_id: str, claims: dict[str, Any] | None = None) -> Wakeup:
    return _decide_wakeup_atomically(wakeup_id, WakeupStatus.APPROVED, claims=claims)


def reject_wakeup(wakeup_id: str, claims: dict[str, Any] | None = None) -> Wakeup:
    return _decide_wakeup_atomically(wakeup_id, WakeupStatus.REJECTED, claims=claims)


def _decide_wakeup_atomically(
    wakeup_id: str,
    status: WakeupStatus,
    *,
    claims: dict[str, Any] | None = None,
) -> Wakeup:
    existing = db.fetch_one("wakeups", wakeup_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Wakeup not found")
    if status == WakeupStatus.REJECTED:
        _raise_if_mobile_claims_disallowed_for_wakeup(existing, claims, for_reject=True)
    else:
        _raise_if_mobile_claims_disallowed_for_wakeup(existing, claims, for_reject=False)

    timestamp = now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM wakeups WHERE id = ?", (wakeup_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Wakeup not found")

        wakeup = Wakeup.model_validate(json.loads(row["data"]))
        if wakeup.status != WakeupStatus.PENDING:
            raise HTTPException(status_code=409, detail=f"Wakeup is already {wakeup.status}.")
        if status == WakeupStatus.APPROVED:
            _raise_if_wakeup_source_inactive(conn, wakeup)

        wakeup.status = status
        wakeup.decided_at = timestamp
        wakeup.updated_at = timestamp
        data = wakeup.model_dump(mode="json")
        cursor = conn.execute(
            """
            UPDATE wakeups
            SET status = ?,
                data = ?,
                updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (status.value, json.dumps(data, ensure_ascii=False), timestamp, wakeup_id, WakeupStatus.PENDING.value),
        )
        if cursor.rowcount != 1:
            current_row = conn.execute("SELECT data FROM wakeups WHERE id = ?", (wakeup_id,)).fetchone()
            current = Wakeup.model_validate(json.loads(current_row["data"])) if current_row else wakeup
            raise HTTPException(status_code=409, detail=f"Wakeup is already {current.status}.")
    return wakeup


def _raise_if_wakeup_source_inactive(conn: Any, wakeup: Wakeup) -> None:
    if wakeup.source != "schedule" or not wakeup.source_id:
        return
    schedule = conn.execute("SELECT enabled FROM scheduled_tasks WHERE id = ?", (wakeup.source_id,)).fetchone()
    if not schedule:
        raise HTTPException(status_code=409, detail="Wakeup source schedule is no longer available.")
    if not bool(schedule["enabled"]):
        raise HTTPException(status_code=409, detail="Wakeup source schedule is disabled.")


def complete_wakeup(wakeup: Wakeup, *, run_id: str = "", error: str = "") -> Wakeup:
    wakeup.status = WakeupStatus.FAILED if error else WakeupStatus.COMPLETED
    wakeup.run_id = run_id
    wakeup.error = str(redact_value(error)) if error else ""
    wakeup.updated_at = now_iso()
    db.upsert_model("wakeups", wakeup)
    return wakeup


def safe_wakeup_payload(
    wakeup: Wakeup | dict[str, Any],
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = wakeup.model_dump(mode="json") if isinstance(wakeup, Wakeup) else dict(wakeup)
    for key in ("title", "body", "goal", "error"):
        payload[key] = _safe_wakeup_text(payload.get(key) or "")
    if claims is not None:
        for key in ("source_device_id", "source_grant_id", "allowed_device_ids"):
            payload.pop(key, None)
    return payload


def _safe_wakeup_text(value: Any) -> str:
    return redact_public_text(str(redact_value(str(value or "")) or ""))


def _active_mobile_device_ids() -> list[str]:
    device_ids: list[str] = []
    for row in db.fetch_many("mobile_devices", limit=100):
        if str(row.get("status") or "active").lower() != "active":
            continue
        device_id = _text(row.get("device_id") or row.get("id"))
        if device_id:
            device_ids.append(device_id)
    return device_ids


def _mobile_claims_allow_wakeup_for_read(wakeup: dict[str, Any], claims: dict[str, Any] | None) -> bool:
    return not _mobile_wakeup_denial_reason(wakeup, claims)


def _raise_if_mobile_claims_disallowed_for_wakeup(
    wakeup: dict[str, Any],
    claims: dict[str, Any] | None,
    *,
    for_reject: bool,
) -> None:
    reason = _mobile_wakeup_denial_reason(wakeup, claims)
    if reason:
        raise HTTPException(status_code=403, detail=reason)
    if for_reject and _text((claims or {}).get("source")) == "remote_input_grant":
        raise HTTPException(status_code=403, detail="Remote input grant token cannot reject wakeups.")


def _mobile_wakeup_denial_reason(wakeup: dict[str, Any], claims: dict[str, Any] | None) -> str:
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

    allowed_devices = _wakeup_allowed_device_ids(wakeup)
    if not allowed_devices:
        return "Wakeup is missing a device binding."
    if device_id not in allowed_devices:
        return "Mobile token is not allowed to access this wakeup."

    if _text(claims.get("source")) == "remote_input_grant":
        return "Remote input grant token is not allowed for wakeups."

    if TOKEN_SCOPE not in scopes:
        return "Mobile token scope is not allowed for this wakeup."
    return ""


def _wakeup_allowed_device_ids(wakeup: dict[str, Any]) -> set[str]:
    device_ids = set(_text_list(wakeup.get("allowed_device_ids")))
    source_device_id = _text(wakeup.get("source_device_id"))
    if source_device_id:
        device_ids.add(source_device_id)
    if not device_ids and _text(wakeup.get("source")) == "schedule":
        device_ids.update(_active_mobile_device_ids())
    return device_ids


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]
