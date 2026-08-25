"""Shared durable execution path for native and runtime task rollback."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from hmac import compare_digest
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.db_task_rollback import (
    ROLLBACK_CONFIRMATION_NATIVE,
    ROLLBACK_CONFIRMATION_RUNTIME_RECOVERY,
    claim_task_rollback,
    fail_task_rollback,
    finish_task_rollback,
    interrupt_task_rollback,
    maintain_task_rollback_lease,
    mark_task_rollback_running,
)
from app.core.errors import AppError
from app.core.schemas import Task
from app.orchestration.state_machine import ensure_transition_allowed, safe_transition
from app.orchestration.task_phase import TaskPhase
from app.orchestration.tool_runtime_execution import hold_shared_path_locks
from app.tools import rollback_tools
from app.tools.rollback_locking import rollback_write_lock_keys


class RollbackSource(StrEnum):
    NATIVE_CONFIRMATION = ROLLBACK_CONFIRMATION_NATIVE
    RUNTIME_RECOVERY = ROLLBACK_CONFIRMATION_RUNTIME_RECOVERY


@dataclass(frozen=True)
class TaskRollbackRequest:
    task_id: str
    source: RollbackSource
    confirmation_id: str
    expected_inventory_hmac: str = ""
    actor: str = "TaskRollbackWorkflow"


@dataclass(frozen=True)
class TaskRollbackRun:
    task: Task
    outcome: dict[str, Any]
    claim_id: str


async def execute_task_rollback(request: TaskRollbackRequest) -> TaskRollbackRun:
    """Execute one task rollback under a durable claim and shared path locks."""

    db.require_sensitive_integrity_ok()
    expected_hmac = str(request.expected_inventory_hmac or "").strip()
    if not expected_hmac:
        initial_snapshot = await asyncio.to_thread(rollback_tools.load_rollback_snapshot, request.task_id)
        expected_hmac = rollback_tools.rollback_snapshot_hmac(initial_snapshot)

    claim, claim_error = claim_task_rollback(
        request.task_id,
        request.confirmation_id,
        preview_hmac=expected_hmac,
        confirmation_source=request.source.value,
    )
    if claim is None:
        raise AppError(
            code="rollback_in_progress",
            message=claim_error or "Rollback is already in progress.",
            status_code=409,
        )
    record(
        "task.rollback_claimed",
        request.actor,
        {
            "rollback_claim_id": claim.claim_id,
            "confirmation_id": request.confirmation_id,
            "confirmation_source": request.source.value,
        },
        task_id=request.task_id,
    )

    running = False
    try:
        snapshot = await asyncio.to_thread(rollback_tools.load_rollback_snapshot, request.task_id)
        snapshot_hmac = rollback_tools.rollback_snapshot_hmac(snapshot)
        if not compare_digest(snapshot_hmac, expected_hmac):
            _fail_before_execution(
                request,
                claim.claim_id,
                claim.owner_id,
                "Rollback inventory changed after confirmation.",
            )
            raise _preview_changed(request.source, "after confirmation")

        if not mark_task_rollback_running(request.task_id, claim.claim_id, owner_id=claim.owner_id):
            fail_task_rollback(
                request.task_id,
                claim.claim_id,
                owner_id=claim.owner_id,
                detail="Rollback claim could not start.",
            )
            raise AppError(
                code="rollback_in_progress",
                message="Rollback claim could not be started.",
                status_code=409,
            )
        running = True

        with maintain_task_rollback_lease(request.task_id, claim.claim_id, owner_id=claim.owner_id) as lease_alive:
            async with hold_shared_path_locks(rollback_write_lock_keys(snapshot)):
                if not lease_alive():
                    raise RuntimeError("Rollback claim lease was lost while waiting for path locks.")
                locked_snapshot = await asyncio.to_thread(rollback_tools.load_rollback_snapshot, request.task_id)
                locked_hmac = rollback_tools.rollback_snapshot_hmac(locked_snapshot)
                if not compare_digest(locked_hmac, snapshot_hmac):
                    fail_task_rollback(
                        request.task_id,
                        claim.claim_id,
                        owner_id=claim.owner_id,
                        detail="Rollback inventory changed while waiting for path locks.",
                    )
                    running = False
                    if request.source == RollbackSource.RUNTIME_RECOVERY:
                        _mark_repair_required(
                            request.task_id,
                            "Rollback evidence changed before automatic recovery could execute; "
                            "inspect the task manually.",
                            actor=request.actor,
                        )
                    raise _preview_changed(request.source, "while waiting for another task")
                journal_hmac = str(locked_snapshot.journal_hmac or "").strip()
                if not journal_hmac:
                    raise RuntimeError("Rollback snapshot is missing its durable journal binding.")
                outcome = await asyncio.to_thread(
                    rollback_tools.execute_rollback,
                    request.task_id,
                    snapshot=locked_snapshot,
                    heartbeat=lease_alive,
                )
                if not isinstance(outcome, dict):
                    outcome = _invalid_rollback_outcome()
                rollback_metadata = _rollback_metadata(outcome)
                verified_success = _is_verified_success(outcome, rollback_metadata)
                if not verified_success and rollback_metadata["state"] == "succeeded":
                    _downgrade_unverified_success(outcome, rollback_metadata)
                if verified_success:
                    rollback_phase = (
                        TaskPhase.FAILED if request.source == RollbackSource.RUNTIME_RECOVERY else TaskPhase.ROLLED_BACK
                    )
                else:
                    rollback_phase = TaskPhase.REPAIR_REQUIRED
                ensure_transition_allowed(claim.task, rollback_phase, strict=True)
                finished = finish_task_rollback(
                    request.task_id,
                    claim.claim_id,
                    phase=rollback_phase,
                    rollback_metadata=rollback_metadata,
                    final_summary=rollback_final_summary(rollback_metadata),
                    owner_id=claim.owner_id,
                    expected_inventory_hmac=journal_hmac,
                    inventory_hmac_loader=rollback_journal_hmac,
                )
                if finished is None:
                    interrupt_task_rollback(
                        request.task_id,
                        claim.claim_id,
                        owner_id=claim.owner_id,
                        detail="Rollback claim was lost before finalization.",
                    )
                    running = False
                    raise AppError(
                        code="rollback_claim_lost",
                        message=(
                            "Rollback completed but its task state could not be finalized; manual review is required."
                        ),
                        status_code=409,
                    )
        running = False
        _merge_persisted_outcome(outcome, finished)
        record(
            "task.status_changed",
            request.actor,
            {
                "from": claim.task.status,
                "to": finished.status,
                "execution_stage_from": claim.task.execution_stage,
                "execution_stage_to": finished.execution_stage,
                "rollback_claim_id": claim.claim_id,
                "confirmation_source": request.source.value,
            },
            task_id=request.task_id,
        )
        outcome["task_status"] = finished.status.value
        return TaskRollbackRun(task=finished, outcome=outcome, claim_id=claim.claim_id)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: rollback claims must fail closed
        if running:
            interrupt_task_rollback(
                request.task_id,
                claim.claim_id,
                owner_id=claim.owner_id,
                detail=str(exc),
            )
        else:
            fail_task_rollback(
                request.task_id,
                claim.claim_id,
                owner_id=claim.owner_id,
                detail=str(exc),
            )
        raise


def rollback_inventory_hmac(task_id: str) -> str:
    return rollback_tools.rollback_snapshot_hmac(rollback_tools.load_rollback_snapshot(task_id))


def rollback_journal_hmac(task_id: str) -> str:
    return rollback_tools.rollback_journal_hmac(task_id)


def rollback_final_summary(summary: dict[str, Any]) -> str:
    state = str(summary.get("state") or "failed")
    attempted = int(summary.get("attempted") or 0)
    succeeded = int(summary.get("succeeded") or 0)
    if state == "succeeded":
        return f"Rollback completed successfully: {succeeded} of {attempted} actions restored."
    if state == "manual_required":
        return "Rollback requires manual repair: one or more actions must be restored by the user."
    if state == "unrecoverable":
        return "Rollback could not fully restore the task because one or more actions are unrecoverable."
    if state == "partial":
        return f"Rollback was only partially completed: {succeeded} of {attempted} actions restored."
    return "Rollback failed before any action could be restored."


def _fail_before_execution(
    request: TaskRollbackRequest,
    claim_id: str,
    owner_id: str,
    detail: str,
) -> None:
    fail_task_rollback(request.task_id, claim_id, owner_id=owner_id, detail=detail)
    if request.source == RollbackSource.RUNTIME_RECOVERY:
        _mark_repair_required(
            request.task_id,
            "Rollback evidence changed before automatic recovery could execute; inspect the task manually.",
            actor=request.actor,
        )


def _mark_repair_required(task_id: str, summary: str, *, actor: str) -> Task | None:
    raw = db.fetch_one("tasks", task_id)
    if not raw:
        return None
    task = Task.model_validate(raw)
    if task.status is not TaskPhase.REPAIR_REQUIRED:
        ensure_transition_allowed(task, TaskPhase.REPAIR_REQUIRED, strict=True)
        task.final_summary = summary
        return safe_transition(task, TaskPhase.REPAIR_REQUIRED, actor=actor, strict=True)
    task.final_summary = summary
    db.upsert_model("tasks", task)
    return task


def _preview_changed(source: RollbackSource, stage: str) -> AppError:
    if source == RollbackSource.RUNTIME_RECOVERY:
        return AppError(
            code="rollback_recovery_evidence_changed",
            message="Rollback evidence changed before automatic recovery; manual review is required.",
            status_code=409,
        )
    return AppError(
        code="rollback_preview_changed",
        message=f"Rollback evidence changed {stage}. Review the updated preview and confirm again.",
        status_code=409,
    )


def _rollback_metadata(outcome: Any) -> dict[str, Any]:
    raw = outcome if isinstance(outcome, dict) else {}
    return {
        "state": str(raw.get("state") or "failed"),
        "attempted": _count(raw, "attempted"),
        "succeeded": _count(raw, "succeeded"),
        "verified": _count(raw, "verified"),
        "verification_failed": _count(raw, "verification_failed"),
        "failed": _count(raw, "failed"),
        "manual_required": _count(raw, "manual_required"),
        "unrecoverable": _count(raw, "unrecoverable"),
    }


def _invalid_rollback_outcome() -> dict[str, Any]:
    return {
        "executed": [],
        "count": 0,
        "state": "failed",
        "attempted": 0,
        "succeeded": 0,
        "verified": 0,
        "verification_failed": 0,
        "failed": 1,
        "manual_required": 1,
        "unrecoverable": 0,
    }


def _count(outcome: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(outcome.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _is_verified_success(outcome: Any, summary: dict[str, Any]) -> bool:
    if not isinstance(outcome, dict) or summary["state"] != "succeeded":
        return False
    attempted = summary["attempted"]
    if any(summary[key] for key in ("verification_failed", "failed", "manual_required", "unrecoverable")):
        return False
    if summary["succeeded"] != attempted or summary["verified"] != attempted:
        return False
    executed = outcome.get("executed")
    if not isinstance(executed, list) or len(executed) != attempted:
        return False
    return all(
        isinstance(item, dict)
        and item.get("ok") is True
        and item.get("verified") is True
        and isinstance(item.get("verification"), dict)
        and item["verification"].get("status") == "passed"
        for item in executed
    )


def _downgrade_unverified_success(outcome: dict[str, Any], summary: dict[str, Any]) -> None:
    summary["state"] = "manual_required"
    summary["manual_required"] = max(1, int(summary.get("manual_required") or 0))
    outcome["state"] = summary["state"]
    outcome["manual_required"] = summary["manual_required"]


def _merge_persisted_outcome(outcome: dict[str, Any], task: Task) -> None:
    persisted = dict(task.metadata.get("rollback") or {})
    if not persisted.get("inventory_changed_after_snapshot"):
        return
    executed = outcome.setdefault("executed", [])
    if not isinstance(executed, list):
        executed = []
        outcome["executed"] = executed
    executed.append(
        {
            "tool_call_id": "rollback-inventory",
            "ok": False,
            "action": "manual_review",
            "requires_user_action": True,
            "verified": False,
            "verification": {"status": "manual_required", "method": "rollback_inventory"},
            "reason": "inventory_changed_after_snapshot",
            "detail": "Rollback evidence changed during execution; unconfirmed entries were not executed.",
        }
    )
    for key, value in persisted.items():
        if key in outcome:
            outcome[key] = value
    outcome["count"] = int(persisted.get("attempted") or len(executed))
