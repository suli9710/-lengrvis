"""Durable task-level claims for destructive rollback requests.

Native confirmation proves that a caller was authorized, but two separately
confirmed requests can still arrive at the same time.  These helpers provide
the SQLite linearization point that lets only one request execute a task's
rollback side effects at a time, including across worker processes.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from threading import Event, Thread
from typing import Any

from app.core import db
from app.core.schemas import ExecutionStage, Task, TaskPhase, now_iso

_ROLLBACK_ELIGIBLE_PHASES = frozenset({TaskPhase.COMPLETED.value, TaskPhase.FAILED.value, TaskPhase.DENIED.value})
_ACTIVE_CLAIM_STATES = frozenset({"claimed", "running"})
_PROCESS_OWNER_ID = f"process-{os.getpid()}-{uuid.uuid4().hex}"
ROLLBACK_CLAIM_LEASE_SECONDS = 15 * 60
ROLLBACK_CONFIRMATION_NATIVE = "native_confirmation"
ROLLBACK_CONFIRMATION_RUNTIME_RECOVERY = "runtime_recovery"
_ROLLBACK_CONFIRMATION_SOURCES = frozenset({ROLLBACK_CONFIRMATION_NATIVE, ROLLBACK_CONFIRMATION_RUNTIME_RECOVERY})


@dataclass(frozen=True)
class TaskRollbackClaim:
    task: Task
    claim_id: str
    owner_id: str


@dataclass(frozen=True)
class TaskRollbackLease:
    _check: Callable[[], bool]
    lost_event: Event

    def __call__(self) -> bool:
        return self._check()


def claim_task_rollback(
    task_id: str,
    confirmation_id: str,
    *,
    preview_hmac: str = "",
    owner_id: str = "",
    lease_seconds: int = ROLLBACK_CLAIM_LEASE_SECONDS,
    confirmation_source: str = ROLLBACK_CONFIRMATION_NATIVE,
) -> tuple[TaskRollbackClaim | None, str]:
    """Atomically reserve rollback execution for ``task_id``.

    The returned task contains the claim metadata and is the snapshot callers
    should use for transition validation.  A failed claim never performs a
    filesystem operation and returns a user-safe reason suitable for a 409.
    """

    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return None, "Task id is required."
    normalized_confirmation_id = str(confirmation_id or "").strip()
    normalized_confirmation_source = str(confirmation_source or "").strip()
    if not normalized_confirmation_id:
        return None, "Rollback confirmation id is required."
    if normalized_confirmation_source not in _ROLLBACK_CONFIRMATION_SOURCES:
        return None, "Rollback confirmation source is invalid."
    if (
        normalized_confirmation_source == ROLLBACK_CONFIRMATION_RUNTIME_RECOVERY
        and not normalized_confirmation_id.startswith("runtime-recovery:")
    ):
        return None, "Runtime rollback confirmation id is invalid."
    now = now_iso()
    claim_id = f"rollback-{uuid.uuid4().hex}"
    normalized_owner_id = _owner_id(owner_id)
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        row = conn.execute("SELECT data FROM tasks WHERE id = ?", (normalized_task_id,)).fetchone()
        if row is None:
            return None, "Task not found."
        payload = _payload(row[0])
        if not payload:
            return None, "Task record is invalid."
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        previous = metadata.get("rollback_claim")
        if isinstance(previous, dict) and str(previous.get("state") or "") == "interrupted":
            return None, (
                "A previous rollback was interrupted. Automatic retry is permanently blocked; "
                "inspect the affected paths and repair them manually."
            )
        phase = _phase_text(payload)
        if phase not in _ROLLBACK_ELIGIBLE_PHASES:
            return None, f"Task phase {phase or 'unknown'} is not eligible for rollback."
        if isinstance(previous, dict) and str(previous.get("state") or "") in _ACTIVE_CLAIM_STATES:
            if _lease_expired(previous, now):
                interrupted = _interrupted_task_payload(payload, previous, now)
                if interrupted is None:
                    return None, "Task record is invalid."
                conn.execute(
                    "UPDATE tasks SET data = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(interrupted, ensure_ascii=False), now, normalized_task_id),
                )
                return None, (
                    "The previous rollback lease expired. Automatic retry is blocked; "
                    "inspect the affected paths and repair them manually."
                )
            return None, "Rollback is already in progress for this task."

        metadata = dict(metadata)
        metadata["rollback_claim"] = {
            "claim_id": claim_id,
            "confirmation_id": normalized_confirmation_id,
            "confirmation_source": normalized_confirmation_source,
            "preview_hmac": str(preview_hmac or "").strip(),
            "state": "claimed",
            "owner_id": normalized_owner_id,
            "claimed_at": now,
            "heartbeat_at": now,
            "lease_expires_at": _lease_expires_at(now, lease_seconds),
        }
        payload["metadata"] = metadata
        payload["updated_at"] = now
        try:
            task = Task.model_validate(payload)
        except Exception:  # noqa: BLE001 - broad-exception-boundary: malformed legacy rows fail closed
            return None, "Task record is invalid."
        conn.execute(
            "UPDATE tasks SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), now, normalized_task_id),
        )
    return TaskRollbackClaim(task=task, claim_id=claim_id, owner_id=normalized_owner_id), ""


def mark_task_rollback_running(
    task_id: str,
    claim_id: str,
    *,
    owner_id: str = "",
    lease_seconds: int = ROLLBACK_CLAIM_LEASE_SECONDS,
) -> bool:
    return _update_claim_state(
        task_id,
        claim_id,
        "running",
        owner_id=owner_id,
        expected_states=frozenset({"claimed"}),
        renew_lease_seconds=lease_seconds,
        require_live_lease=True,
    )


def heartbeat_task_rollback(
    task_id: str,
    claim_id: str,
    *,
    owner_id: str = "",
    lease_seconds: int = ROLLBACK_CLAIM_LEASE_SECONDS,
) -> bool:
    """Renew a running rollback lease without changing its state."""

    return _update_claim_state(
        task_id,
        claim_id,
        "running",
        owner_id=owner_id,
        expected_states=frozenset({"running"}),
        renew_lease_seconds=lease_seconds,
        require_live_lease=True,
    )


@contextmanager
def maintain_task_rollback_lease(
    task_id: str,
    claim_id: str,
    *,
    owner_id: str = "",
    lease_seconds: int = ROLLBACK_CLAIM_LEASE_SECONDS,
    interval_seconds: float | None = None,
) -> Iterator[TaskRollbackLease]:
    """Keep a running rollback lease alive while a filesystem action blocks."""

    stop = Event()
    lost = Event()
    interval = max(0.01, float(interval_seconds if interval_seconds is not None else min(30, lease_seconds / 3)))

    def lease_alive() -> bool:
        if lost.is_set():
            return False
        try:
            alive = heartbeat_task_rollback(
                task_id,
                claim_id,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
            )
        except Exception:  # noqa: BLE001 - broad-exception-boundary: a lost lease fails closed
            alive = False
        if not alive:
            lost.set()
        return alive

    def renew() -> None:
        try:
            while not stop.wait(interval):
                if not lease_alive():
                    return
        finally:
            db.close_thread_connection()

    lease_alive()
    worker = Thread(target=renew, name=f"rollback-lease-{claim_id[-12:]}", daemon=True)
    if not lost.is_set():
        worker.start()
    try:
        yield TaskRollbackLease(lease_alive, lost)
    finally:
        stop.set()
        if worker.is_alive():
            worker.join(timeout=interval + 1)


def finish_task_rollback(
    task_id: str,
    claim_id: str,
    *,
    phase: TaskPhase,
    rollback_metadata: dict[str, Any],
    final_summary: str,
    owner_id: str = "",
    expected_inventory_hmac: str = "",
    inventory_hmac_loader: Callable[[str], str] | None = None,
) -> Task | None:
    """Persist the terminal rollback state only for the active claim."""

    normalized_task_id = str(task_id or "").strip()
    now = now_iso()
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        row = conn.execute("SELECT data FROM tasks WHERE id = ?", (normalized_task_id,)).fetchone()
        if row is None:
            return None
        payload = _payload(row[0])
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        claim = metadata.get("rollback_claim") if isinstance(metadata.get("rollback_claim"), dict) else {}
        if not _claim_matches(
            claim,
            claim_id,
            owner_id=owner_id,
            expected_states=frozenset({"running"}),
            require_live_lease=True,
            now=now,
        ):
            return None
        inventory_changed = False
        if expected_inventory_hmac:
            try:
                current_inventory_hmac = (
                    inventory_hmac_loader(normalized_task_id) if inventory_hmac_loader is not None else ""
                )
            except Exception:  # noqa: BLE001 - broad-exception-boundary: final inventory verification fails closed
                current_inventory_hmac = ""
            inventory_changed = not compare_digest(str(current_inventory_hmac), str(expected_inventory_hmac))
        effective_phase = TaskPhase.REPAIR_REQUIRED if inventory_changed else phase
        effective_rollback_metadata = (
            _inventory_changed_metadata(rollback_metadata) if inventory_changed else dict(rollback_metadata)
        )
        effective_summary = (
            "Rollback evidence changed during execution. Unconfirmed side effects were not rolled back; "
            "inspect the task manually."
            if inventory_changed
            else str(final_summary or "")
        )
        metadata = dict(metadata)
        metadata["rollback"] = effective_rollback_metadata
        metadata["rollback_claim"] = {
            **claim,
            "state": "completed",
            "completed_at": now,
            **({"inventory_changed_after_snapshot": True} if inventory_changed else {}),
        }
        payload.update(
            {
                "status": effective_phase.value,
                "phase": effective_phase.value,
                "execution_stage": ExecutionStage.IDLE.value,
                "metadata": metadata,
                "final_summary": effective_summary,
                "updated_at": now,
            }
        )
        try:
            task = Task.model_validate(payload)
        except Exception:  # noqa: BLE001 - broad-exception-boundary: malformed legacy rows fail closed before UPDATE
            return None
        conn.execute(
            "UPDATE tasks SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), now, normalized_task_id),
        )
    return task


def fail_task_rollback(task_id: str, claim_id: str, *, detail: str = "", owner_id: str = "") -> bool:
    """Release a claim after an executor failure without reopening it silently."""

    return _update_claim_state(
        task_id,
        claim_id,
        "failed",
        detail=detail,
        owner_id=owner_id,
        expected_states=_ACTIVE_CLAIM_STATES,
        interrupt_expired=True,
    )


def interrupt_task_rollback(
    task_id: str,
    claim_id: str,
    *,
    detail: str = "",
    owner_id: str = "",
) -> Task | None:
    """Permanently block retry after a running rollback may have mutated files."""

    normalized_task_id = str(task_id or "").strip()
    now = now_iso()
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        row = conn.execute("SELECT data FROM tasks WHERE id = ?", (normalized_task_id,)).fetchone()
        if row is None:
            return None
        payload = _payload(row[0])
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        claim = metadata.get("rollback_claim") if isinstance(metadata.get("rollback_claim"), dict) else {}
        if not _claim_matches(
            claim,
            claim_id,
            owner_id=owner_id,
            expected_states=frozenset({"running"}),
        ):
            return None
        interrupted = _interrupted_task_payload(payload, claim, now, detail=detail)
        if interrupted is None:
            return None
        conn.execute(
            "UPDATE tasks SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(interrupted, ensure_ascii=False), now, normalized_task_id),
        )
    return Task.model_validate(interrupted)


def recover_interrupted_task_rollbacks() -> list[str]:
    """Fail closed any rollback claim left active by a terminated process."""

    recovered: list[str] = []
    now = now_iso()
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        rows = conn.execute("SELECT id, data FROM tasks ORDER BY id").fetchall()
        for row in rows:
            payload = _payload(row[1])
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            claim = metadata.get("rollback_claim") if isinstance(metadata.get("rollback_claim"), dict) else {}
            if str(claim.get("state") or "") not in _ACTIVE_CLAIM_STATES or not _lease_expired(claim, now):
                continue
            task_id = str(row[0])
            interrupted = _interrupted_task_payload(payload, claim, now)
            if interrupted is None:
                continue
            conn.execute(
                "UPDATE tasks SET data = ?, updated_at = ? WHERE id = ?",
                (json.dumps(interrupted, ensure_ascii=False), now, task_id),
            )
            recovered.append(task_id)
    return recovered


def _update_claim_state(
    task_id: str,
    claim_id: str,
    state: str,
    *,
    detail: str = "",
    owner_id: str = "",
    expected_states: frozenset[str],
    renew_lease_seconds: int | None = None,
    require_live_lease: bool = False,
    interrupt_expired: bool = False,
) -> bool:
    normalized_task_id = str(task_id or "").strip()
    now = now_iso()
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        row = conn.execute("SELECT data FROM tasks WHERE id = ?", (normalized_task_id,)).fetchone()
        if row is None:
            return False
        payload = _payload(row[0])
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        claim = metadata.get("rollback_claim") if isinstance(metadata.get("rollback_claim"), dict) else {}
        if not _claim_matches(claim, claim_id, owner_id=owner_id, expected_states=expected_states):
            return False
        if _lease_expired(claim, now):
            if interrupt_expired:
                interrupted = _interrupted_task_payload(payload, claim, now)
                if interrupted is None:
                    return False
                conn.execute(
                    "UPDATE tasks SET data = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(interrupted, ensure_ascii=False), now, normalized_task_id),
                )
                return True
            if require_live_lease:
                return False
        lease_fields: dict[str, str] = {}
        if renew_lease_seconds is not None:
            lease_fields = {
                "heartbeat_at": now,
                "lease_expires_at": _lease_expires_at(now, renew_lease_seconds),
            }
        metadata = dict(metadata)
        metadata["rollback_claim"] = {
            **claim,
            "state": state,
            **lease_fields,
            **({"detail": str(detail)[:500]} if detail else {}),
            "updated_at": now,
        }
        payload["metadata"] = metadata
        payload["updated_at"] = now
        try:
            Task.model_validate(payload)
        except Exception:  # noqa: BLE001 - broad-exception-boundary: malformed task rows fail closed before UPDATE
            return False
        conn.execute(
            "UPDATE tasks SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), now, normalized_task_id),
        )
    return True


def _payload(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _phase_text(payload: dict[str, Any]) -> str:
    value = payload.get("status") or payload.get("phase")
    return str(getattr(value, "value", value) or "").strip().casefold()


def _inventory_changed_metadata(rollback_metadata: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(rollback_metadata)
    metadata.update(
        {
            "state": "manual_required",
            "attempted": int(metadata.get("attempted") or 0) + 1,
            "manual_required": int(metadata.get("manual_required") or 0) + 1,
            "inventory_changed_after_snapshot": True,
        }
    )
    for key in ("succeeded", "verified", "verification_failed", "failed", "unrecoverable"):
        metadata[key] = int(metadata.get(key) or 0)
    return metadata


def _owner_id(value: str) -> str:
    return str(value or _PROCESS_OWNER_ID).strip() or _PROCESS_OWNER_ID


def _claim_matches(
    claim: dict[str, Any],
    claim_id: str,
    *,
    owner_id: str,
    expected_states: frozenset[str],
    require_live_lease: bool = False,
    now: str = "",
) -> bool:
    matches = (
        str(claim.get("claim_id") or "") == str(claim_id)
        and str(claim.get("owner_id") or "") == _owner_id(owner_id)
        and str(claim.get("state") or "") in expected_states
    )
    if not matches or not require_live_lease:
        return matches
    return not _lease_expired(claim, now or now_iso())


def _interrupted_task_payload(
    payload: dict[str, Any],
    claim: dict[str, Any],
    now: str,
    *,
    detail: str = "",
) -> dict[str, Any] | None:
    rollback_metadata = {
        "state": "interrupted",
        "attempted": 0,
        "succeeded": 0,
        "verified": 0,
        "verification_failed": 0,
        "failed": 1,
        "manual_required": 1,
        "unrecoverable": 0,
    }
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata = dict(metadata)
    metadata["rollback"] = rollback_metadata
    metadata["rollback_claim"] = {
        **claim,
        "state": "interrupted",
        "recovered_at": now,
        **({"detail": str(detail)[:500]} if detail else {}),
    }
    interrupted = {
        **payload,
        "status": TaskPhase.REPAIR_REQUIRED.value,
        "phase": TaskPhase.REPAIR_REQUIRED.value,
        "execution_stage": ExecutionStage.IDLE.value,
        "metadata": metadata,
        "final_summary": (
            "Rollback was interrupted before completion. Automatic retry is blocked; "
            "inspect the affected paths and repair them manually."
        ),
        "updated_at": now,
    }
    try:
        Task.model_validate(interrupted)
    except Exception:  # noqa: BLE001 - broad-exception-boundary: never persist a malformed interrupted task
        return None
    return interrupted


def _lease_expires_at(now: str, lease_seconds: int) -> str:
    parsed = _parse_timestamp(now) or datetime.now(UTC)
    seconds = max(30, int(lease_seconds))
    return (parsed + timedelta(seconds=seconds)).isoformat()


def _lease_expired(claim: dict[str, Any], now: str) -> bool:
    expires_at = _parse_timestamp(str(claim.get("lease_expires_at") or ""))
    current = _parse_timestamp(now)
    if expires_at is None or current is None:
        return True
    return expires_at <= current


def _parse_timestamp(value: str) -> datetime | None:
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
