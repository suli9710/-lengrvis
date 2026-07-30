from __future__ import annotations

import json
import sqlite3

from app.api.routes_mobile import _mobile_task_status_detail, _mobile_task_status_label
from app.api.task_public_views import empty_completion_evidence, task_next_step, task_status_summary
from app.core.db_migration_task_denied import task_denied_phase_backfill
from app.core.schemas import Plan, PlanStep, RunPhase, StepStatus, Task
from app.orchestration.developer_engine import _developer_terminal_run_phase
from app.orchestration.os_execution_state import phase_for_task_plan
from app.orchestration.task_phase import TaskPhase
from app.services.run_service import _phase_for_task, _phase_for_task_plan
from app.services.task_service import _task_phase_from_run_phase


def test_denied_is_distinct_across_task_and_run_phase_mappings() -> None:
    denied = Task(user_goal="blocked", status=TaskPhase.DENIED, phase=TaskPhase.DENIED)
    cancelled = Task(user_goal="stopped", status=TaskPhase.CANCELLED, phase=TaskPhase.CANCELLED)

    assert TaskPhase.DENIED != TaskPhase.CANCELLED
    assert denied.model_dump(mode="json")["status"] == "denied"
    assert _phase_for_task(denied) == RunPhase.DENIED
    assert _phase_for_task(cancelled) == RunPhase.CANCELLED
    assert _developer_terminal_run_phase(denied) == RunPhase.DENIED
    assert _developer_terminal_run_phase(cancelled) == RunPhase.CANCELLED
    assert _task_phase_from_run_phase(RunPhase.DENIED) == TaskPhase.DENIED
    assert _task_phase_from_run_phase(RunPhase.CANCELLED) == TaskPhase.CANCELLED


def test_explicit_cancellation_is_not_reclassified_from_summary_or_plan_history() -> None:
    task = Task(
        user_goal="stop after denial",
        status=TaskPhase.CANCELLED,
        phase=TaskPhase.CANCELLED,
        final_summary="Denied earlier, then cancelled by the user.",
    )
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        steps=[
            PlanStep(
                task_id=task.id,
                agent_name="SafetyReviewAgent",
                tool_name="blocked_tool",
                description="historically denied step",
                status=StepStatus.DENIED,
            )
        ],
    )

    assert phase_for_task_plan(task, plan) == RunPhase.CANCELLED
    assert _phase_for_task_plan(task, plan) == RunPhase.CANCELLED


def test_legacy_denied_status_remains_readable_as_denied() -> None:
    task = Task.model_validate(
        {
            "user_goal": "legacy blocked task",
            "status": "denied",
            "phase": "denied",
        }
    )

    assert task.status == TaskPhase.DENIED
    assert task.phase == TaskPhase.DENIED


def test_denied_has_distinct_public_and_mobile_copy() -> None:
    task = Task(user_goal="blocked", status=TaskPhase.DENIED, phase=TaskPhase.DENIED)
    evidence = empty_completion_evidence()

    assert task_status_summary(task, evidence) == "Denied by a safety or permission boundary before completion."
    assert "revise the goal or permissions" in task_next_step(task, {"items_needing_attention": 0}, evidence)
    assert _mobile_task_status_label("denied") == "已拒绝"
    assert "安全或权限边界" in _mobile_task_status_detail("denied")
    assert _mobile_task_status_label("cancelled") == "已取消"


def test_denied_phase_migration_is_conservative_and_aligns_runs() -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            engine TEXT NOT NULL,
            phase TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    _insert_legacy_task(conn, "raw-denied", status="denied", summary="")
    _insert_legacy_task(
        conn,
        "summary-denied",
        status="cancelled",
        summary="SafetyReviewAgent stopped the task before executing a tool call.",
    )
    _insert_legacy_task(
        conn,
        "run-denied",
        status="cancelled",
        summary="I can help with a safe alternative.",
    )
    _insert_legacy_task(conn, "user-cancelled", status="cancelled", summary="Cancelled by user.")
    _insert_legacy_task(
        conn,
        "approval-rejected",
        status="cancelled",
        summary="Approval was rejected by the user.",
    )
    _insert_legacy_run(
        conn,
        "run-raw-older-cancel",
        "raw-denied",
        "cancelled",
        updated_at="2025-01-01T00:00:00Z",
    )
    _insert_legacy_run(conn, "run-raw", "raw-denied", "cancelled")
    _insert_legacy_run(conn, "run-summary", "summary-denied", "cancelled")
    _insert_legacy_run(conn, "run-proof", "run-denied", "denied")
    _insert_legacy_run(conn, "run-user", "user-cancelled", "cancelled")
    _insert_legacy_run(conn, "run-rejected", "approval-rejected", "cancelled")

    task_denied_phase_backfill(conn)

    tasks = {
        row["id"]: json.loads(row["data"]) for row in conn.execute("SELECT id, data FROM tasks ORDER BY id").fetchall()
    }
    runs = {
        row["id"]: (row["phase"], json.loads(row["data"])["phase"])
        for row in conn.execute("SELECT id, phase, data FROM runs ORDER BY id").fetchall()
    }
    conn.close()

    for task_id in ("raw-denied", "summary-denied", "run-denied"):
        assert tasks[task_id]["status"] == "denied"
        assert tasks[task_id]["phase"] == "denied"
        assert tasks[task_id]["execution_stage"] == "idle"
    assert tasks["user-cancelled"]["status"] == "cancelled"
    assert tasks["approval-rejected"]["status"] == "cancelled"
    assert runs["run-raw"] == ("denied", "denied")
    assert runs["run-summary"] == ("denied", "denied")
    assert runs["run-proof"] == ("denied", "denied")
    assert runs["run-raw-older-cancel"] == ("cancelled", "cancelled")
    assert runs["run-user"] == ("cancelled", "cancelled")
    assert runs["run-rejected"] == ("cancelled", "cancelled")


def _insert_legacy_task(conn: sqlite3.Connection, task_id: str, *, status: str, summary: str) -> None:
    payload = {
        "id": task_id,
        "user_goal": "legacy task",
        "status": status,
        "phase": status,
        "execution_stage": "step_running",
        "final_summary": summary,
    }
    conn.execute(
        "INSERT INTO tasks (id, data, created_at, updated_at) VALUES (?, ?, 'now', 'now')",
        (task_id, json.dumps(payload)),
    )


def _insert_legacy_run(
    conn: sqlite3.Connection,
    run_id: str,
    task_id: str,
    phase: str,
    *,
    updated_at: str = "9999-12-31T23:59:59Z",
) -> None:
    payload = {"id": run_id, "message": "legacy run", "task_id": task_id, "phase": phase}
    conn.execute(
        """
        INSERT INTO runs (id, task_id, engine, phase, data, created_at, updated_at)
        VALUES (?, ?, 'os', ?, ?, 'now', ?)
        """,
        (run_id, task_id, phase, json.dumps(payload), updated_at),
    )
