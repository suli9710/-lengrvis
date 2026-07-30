from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path

import pytest

from app.core import db
from app.core.content_provenance import create_content_envelope
from app.core.schemas import AgentMessage, Approval, ApprovalStatus, MessageType, Task, ToolCall, ToolResult, now_iso
from app.orchestration.direct_tool_execution import (
    execute_direct_tool_journaled,
    execute_direct_tool_journaled_async,
)
from app.orchestration.resource_state import ReadBeforeWriteError
from app.orchestration.task_phase import TaskPhase
from app.orchestration.tool_execution_journal import (
    build_tool_execution_key,
    load_persisted_observations,
    recover_interrupted_tool_executions,
    reserve_prepared_tool_call,
)
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()


def _executing_call(task: Task) -> ToolCall:
    call = ToolCall(
        task_id=task.id,
        step_id="step-1",
        tool_name="file.write_text",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status="executing",
        started_at="2026-07-11T00:00:00+00:00",
        created_at="2026-07-11T00:00:00+00:00",
        dry_run=False,
    )
    db.upsert_model("tool_calls", call)
    return call


def test_recovery_marks_missing_result_as_outcome_unknown_and_blocks_replay():
    task = Task(user_goal="write a file", status=TaskPhase.EXECUTION, phase=TaskPhase.EXECUTION)
    db.upsert_model("tasks", task)
    call = _executing_call(task)

    recovered = recover_interrupted_tool_executions()

    assert recovered == [call.id]
    stored_call = ToolCall.model_validate(db.fetch_one("tool_calls", call.id))
    assert stored_call.status == "outcome_unknown"
    assert stored_call.outcome_unknown_at
    stored_task = Task.model_validate(db.fetch_one("tasks", task.id))
    assert stored_task.status == TaskPhase.FAILED
    assert stored_task.metadata["execution_recovery"] == {
        "state": "outcome_unknown",
        "tool_call_ids": [call.id],
        "requires_user_review": True,
        "automatic_replay_blocked": True,
    }


def test_recovery_commits_call_when_result_was_already_persisted():
    task = Task(user_goal="read a file")
    db.upsert_model("tasks", task)
    call = _executing_call(task)
    db.upsert_model("tool_results", ToolResult(tool_call_id=call.id, ok=True, output={"value": 1}))

    recovered = recover_interrupted_tool_executions()

    assert recovered == []
    stored_call = ToolCall.model_validate(db.fetch_one("tool_calls", call.id))
    assert stored_call.status == "committed"
    assert stored_call.committed_at
    stored_task = Task.model_validate(db.fetch_one("tasks", task.id))
    assert "execution_recovery" not in stored_task.metadata


def test_persisted_observations_restore_all_committed_parent_results():
    task = Task(user_goal="combine two durable results")
    db.upsert_model("tasks", task)
    expected_source_ids = {"approved-parent", "read-parent"}
    for index, source_id in enumerate(sorted(expected_source_ids), start=1):
        call = ToolCall(
            id=f"tool-parent-{index}",
            task_id=task.id,
            step_id=f"parent-{index}",
            tool_name="test.parent",
            risk_level=RiskLevel.R0_READ_ONLY,
            execution_key=f"execution:parent-{index}",
            status="committed",
            approval_id="approval-parent" if source_id == "approved-parent" else "",
            committed_at=f"2026-07-11T00:00:0{index}+00:00",
            dry_run=False,
        )
        result = ToolResult(
            tool_call_id=call.id,
            ok=True,
            output={"source": source_id},
            content_envelope=create_content_envelope(
                source_id,
                source_kind="tool_result",
                source_id=source_id,
                task_scope=task.id,
            ),
        )
        db.upsert_model("tool_calls", call)
        db.upsert_model("tool_results", result)

    restored = load_persisted_observations(task.id, step_ids={"parent-1", "parent-2"})

    assert set(restored) == {"parent-1", "parent-2"}
    assert {result.content_envelope.source_id for result in restored.values() if result.content_envelope} == (
        expected_source_ids
    )


def test_persisted_observations_map_successful_recovery_back_to_failed_parent():
    task = Task(user_goal="resume after a recovery step")
    db.upsert_model("tasks", task)
    recovery_call = ToolCall(
        task_id=task.id,
        step_id="recovery-step",
        tool_name="test.recovery",
        risk_level=RiskLevel.R0_READ_ONLY,
        execution_key="execution:recovery-step",
        status="committed",
        committed_at="2026-07-11T00:00:01+00:00",
        dry_run=False,
    )
    recovery_result = ToolResult(
        tool_call_id=recovery_call.id,
        ok=True,
        output={"value": "recovered"},
        content_envelope=create_content_envelope(
            "recovered",
            source_kind="tool_result",
            source_id="recovery-result",
            task_scope=task.id,
        ),
    )
    db.upsert_model("tool_calls", recovery_call)
    db.upsert_model("tool_results", recovery_result)
    db.upsert_model(
        "agent_messages",
        AgentMessage(
            task_id=task.id,
            step_id="recovery-step",
            from_agent="OrchestratorAgent",
            message_type=MessageType.REVISION,
            content="Trying recovery step.",
            structured_payload={
                "failed_step_id": "failed-parent",
                "recovery_step": {"id": "recovery-step"},
            },
        ),
    )

    restored = load_persisted_observations(task.id, step_ids={"failed-parent"})

    assert restored["failed-parent"].tool_call_id == recovery_call.id
    assert restored["failed-parent"].content_envelope.source_id == "recovery-result"


def test_persisted_observations_do_not_fall_back_to_stale_success_after_newer_failure():
    task = Task(user_goal="do not restore stale success")
    db.upsert_model("tasks", task)
    older_call = ToolCall(
        id="tool-older-success",
        task_id=task.id,
        step_id="same-step",
        tool_name="test.read",
        risk_level=RiskLevel.R0_READ_ONLY,
        execution_key="execution:older-success",
        status="committed",
        committed_at="2026-07-11T00:00:01+00:00",
        created_at="2026-07-11T00:00:01+00:00",
        dry_run=False,
    )
    newer_call = older_call.model_copy(
        update={
            "id": "tool-newer-failure",
            "execution_key": "execution:newer-failure",
            "committed_at": "2026-07-11T00:00:02+00:00",
            "created_at": "2026-07-11T00:00:02+00:00",
        }
    )
    db.upsert_model("tool_calls", older_call)
    db.upsert_model("tool_results", ToolResult(tool_call_id=older_call.id, ok=True, output={"value": "stale"}))
    db.upsert_model("tool_calls", newer_call)
    db.upsert_model("tool_results", ToolResult(tool_call_id=newer_call.id, ok=False, error="latest failed"))

    restored = load_persisted_observations(task.id, step_ids={"same-step"})

    assert restored == {}


@pytest.mark.parametrize("newer_status", ["prepared", "executing", "outcome_unknown"])
def test_persisted_observations_do_not_cross_a_newer_incomplete_execution(newer_status: str):
    task = Task(user_goal="do not cross an incomplete execution")
    db.upsert_model("tasks", task)
    older_call = ToolCall(
        id="tool-prior-success",
        task_id=task.id,
        step_id="same-step",
        tool_name="test.read",
        risk_level=RiskLevel.R0_READ_ONLY,
        execution_key="execution:prior-success",
        status="committed",
        committed_at="2026-07-11T00:00:01+00:00",
        created_at="2026-07-11T00:00:01+00:00",
        dry_run=False,
    )
    newer_call = older_call.model_copy(
        update={
            "id": f"tool-newer-{newer_status}",
            "execution_key": f"execution:newer-{newer_status}",
            "status": newer_status,
            "committed_at": "",
            "created_at": "2026-07-11T00:00:02+00:00",
        }
    )
    db.upsert_model("tool_calls", older_call)
    db.upsert_model("tool_results", ToolResult(tool_call_id=older_call.id, ok=True, output={"value": "stale"}))
    db.upsert_model("tool_calls", newer_call)

    restored = load_persisted_observations(task.id, step_ids={"same-step"})

    assert restored == {}


def test_recovery_does_not_miss_old_executing_call_behind_more_than_5000_rows():
    task = Task(user_goal="write a file", status=TaskPhase.EXECUTION, phase=TaskPhase.EXECUTION)
    db.upsert_model("tasks", task)
    call = _executing_call(task)
    with db.connect() as conn:
        filler = call.model_copy(
            update={
                "task_id": "task-filler",
                "status": "committed",
                "committed_at": "2026-07-11T01:00:00+00:00",
                "created_at": "2026-07-11T01:00:00+00:00",
            }
        )
        rows = []
        for index in range(5001):
            item = filler.model_copy(
                update={
                    "id": f"tool-filler-{index:04d}",
                    "execution_key": f"execution:filler-{index:04d}",
                }
            )
            data = item.model_dump(mode="json")
            rows.append(
                (
                    item.id,
                    item.task_id,
                    item.step_id,
                    item.execution_key,
                    item.status,
                    db._json(data),
                    item.created_at,
                )
            )
        conn.executemany(
            """
            INSERT INTO tool_calls (id, task_id, step_id, execution_key, status, data, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    recovered = recover_interrupted_tool_executions()

    assert recovered == [call.id]
    assert ToolCall.model_validate(db.fetch_one("tool_calls", call.id)).status == "outcome_unknown"


def test_recovery_uses_physical_status_and_repairs_stale_json_status():
    task = Task(user_goal="write a file", status=TaskPhase.EXECUTION, phase=TaskPhase.EXECUTION)
    db.upsert_model("tasks", task)
    call = _executing_call(task)
    stale = call.model_copy(update={"status": "prepared", "started_at": ""})
    with db.connect() as conn:
        conn.execute(
            "UPDATE tool_calls SET data = ? WHERE id = ?",
            (db._json(stale.model_dump(mode="json")), call.id),
        )

    recovered = recover_interrupted_tool_executions()

    assert recovered == [call.id]
    stored_call = ToolCall.model_validate(db.fetch_one("tool_calls", call.id))
    assert stored_call.status == "outcome_unknown"
    assert stored_call.outcome_unknown_at


def test_execution_key_is_deterministic_and_bound_to_plan_args_and_approval():
    task = Task(id="task-key", user_goal="Write the report")
    base = build_tool_execution_key(
        task=task,
        step_id="step-1",
        tool_name="file.write_text",
        tool_version="1",
        args={"path": "C:/work/report.txt", "content": "done", "dry_run": False},
        plan_revision=3,
        approval_id="approval-1",
    )

    assert base == build_tool_execution_key(
        task=task,
        step_id="step-1",
        tool_name="file.write_text",
        tool_version="1",
        args={"content": "done", "path": "C:/work/report.txt", "approved": True},
        plan_revision=3,
        approval_id="approval-1",
    )
    assert base != build_tool_execution_key(
        task=task,
        step_id="step-1",
        tool_name="file.write_text",
        tool_version="1",
        args={"path": "C:/work/report.txt", "content": "changed"},
        plan_revision=3,
        approval_id="approval-1",
    )
    assert base != build_tool_execution_key(
        task=task,
        step_id="step-1",
        tool_name="file.write_text",
        tool_version="1",
        args={"path": "C:/work/report.txt", "content": "done"},
        plan_revision=4,
        approval_id="approval-1",
    )


def test_reservation_reuses_the_single_row_for_the_same_execution_key():
    task = Task(id="task-reserve", user_goal="write")
    key = build_tool_execution_key(
        task=task,
        step_id="step-1",
        tool_name="file.write_text",
        tool_version="1",
        args={"path": "C:/work/report.txt"},
        plan_revision=1,
        approval_id=None,
    )
    first = ToolCall(
        task_id=task.id,
        step_id="step-1",
        tool_name="file.write_text",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        execution_key=key,
        status="prepared",
        dry_run=False,
    )
    second = first.model_copy(update={"id": "tool-second"})

    reserved, created = reserve_prepared_tool_call(first)
    duplicate, duplicate_created = reserve_prepared_tool_call(second)

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == reserved.id
    assert len(db.fetch_many("tool_calls", "execution_key = ?", (key,), limit=10)) == 1


def test_schema_upgrade_backfills_execution_key_and_status_before_index_creation():
    task = Task(id="task-legacy", user_goal="legacy")
    call = ToolCall(
        id="tool-legacy",
        task_id=task.id,
        step_id="step-legacy",
        tool_name="file.write_text",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        execution_key="execution:legacy",
        status="executing",
        dry_run=False,
    )
    with db.connect() as conn:
        conn.execute("DROP INDEX IF EXISTS idx_tool_calls_execution_key")
        conn.execute("DROP TABLE tool_calls")
        conn.execute(
            """
            CREATE TABLE tool_calls (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO tool_calls (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (call.id, call.task_id, call.step_id, db._json(call.model_dump(mode="json")), call.created_at),
        )

    db.reset_init_db_cache()
    db.init_db(force=True)

    with db.connect() as conn:
        row = conn.execute(
            "SELECT execution_key, status FROM tool_calls WHERE id = ?",
            (call.id,),
        ).fetchone()
        indexes = {item["name"] for item in conn.execute("PRAGMA index_list(tool_calls)").fetchall()}
    assert dict(row) == {"execution_key": "execution:legacy", "status": "executing"}
    assert "idx_tool_calls_execution_key" in indexes
    assert "idx_tool_calls_status_created" in indexes


def test_concurrent_reservations_create_only_one_execution_row():
    task = Task(id="task-concurrent", user_goal="write once")
    key = build_tool_execution_key(
        task=task,
        step_id="step-1",
        tool_name="file.write_text",
        tool_version="1",
        args={"path": "C:/work/report.txt", "content": "once"},
        plan_revision=1,
        approval_id=None,
    )

    def reserve(index: int) -> tuple[str, bool]:
        call = ToolCall(
            id=f"tool-concurrent-{index}",
            task_id=task.id,
            step_id="step-1",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            execution_key=key,
            status="prepared",
            dry_run=False,
        )
        stored, created = reserve_prepared_tool_call(call)
        return stored.id, created

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(reserve, range(8)))

    assert sum(1 for _call_id, created in results if created) == 1
    assert len({call_id for call_id, _created in results}) == 1
    assert len(db.fetch_many("tool_calls", "execution_key = ?", (key,), limit=10)) == 1


def _direct_tool(execute) -> ToolDefinition:
    return ToolDefinition(
        name="test.direct_write",
        description="Write through a direct API.",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="TestAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=execute,
        read_only=False,
        concurrency_safe=False,
        effects=["write"],
        resource_kinds=["test_resource"],
        trust_tier="builtin",
    )


def _direct_approval(task: Task) -> Approval:
    approval = Approval(
        task_id=task.id,
        message="Approve direct test write.",
        status=ApprovalStatus.APPROVED,
        tool_name="test.direct_write",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY.value,
        consumed_at=now_iso(),
    )
    db.upsert_model("approvals", approval, status=approval.status)
    return approval


def test_direct_execution_rejects_unconsumed_approval_without_calling_executor():
    task = Task(user_goal="do not bypass approval claim")
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)
    approval.consumed_at = None
    db.upsert_model("approvals", approval, status=approval.status)
    calls: list[dict] = []

    with pytest.raises(ValueError, match="atomically consumed"):
        execute_direct_tool_journaled(
            _direct_tool(lambda args, context: calls.append(dict(args)) or {"ok": True}),
            {"value": "blocked"},
            {},
            approval_id=approval.id,
        )

    assert calls == []
    assert db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=10) == []


def test_direct_execution_rejects_missing_task_without_recreating_it_or_calling_executor():
    task = Task(user_goal="do not recreate missing execution provenance")
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)
    with db.connect() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task.id,))
    calls: list[dict] = []

    with pytest.raises(ValueError, match="original task record"):
        execute_direct_tool_journaled(
            _direct_tool(lambda args, context: calls.append(dict(args)) or {"ok": True}),
            {"value": "blocked"},
            {},
            approval_id=approval.id,
        )

    assert calls == []
    assert db.fetch_one("tasks", task.id) is None
    assert db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=10) == []


def test_direct_execution_rejects_tool_binding_mismatch_without_calling_executor():
    task = Task(user_goal="do not cross approval tool bindings")
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)
    approval.tool_name = "test.other_write"
    db.upsert_model("approvals", approval, status=approval.status)
    calls: list[dict] = []

    with pytest.raises(ValueError, match="different tool"):
        execute_direct_tool_journaled(
            _direct_tool(lambda args, context: calls.append(dict(args)) or {"ok": True}),
            {"value": "blocked"},
            {},
            approval_id=approval.id,
        )

    assert calls == []
    assert db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=10) == []


def test_direct_execution_commits_result_and_reuses_it_without_reexecution():
    task = Task(user_goal="direct write")
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)
    calls: list[dict] = []

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        calls.append(dict(args))
        return {"ok": True, "value": "written"}

    tool = _direct_tool(execute)
    args = {"value": "written"}

    first = execute_direct_tool_journaled(tool, args, {}, approval_id=approval.id)
    second = execute_direct_tool_journaled(tool, args, {}, approval_id=approval.id)

    assert first["ok"] is True
    assert second == first
    assert calls == [args]
    stored_call = ToolCall.model_validate(db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=1)[0])
    assert stored_call.status == "committed"
    assert ToolResult.model_validate(
        db.fetch_many("tool_results", "tool_call_id = ?", (stored_call.id,), limit=1)[0]
    ).ok


def test_direct_execution_with_missing_committed_result_becomes_outcome_unknown():
    task = Task(user_goal="direct write with lost result")
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)
    tool = _direct_tool(lambda args, context: {"ok": True, "value": "written"})
    args = {"value": "written"}
    first = execute_direct_tool_journaled(tool, args, {}, approval_id=approval.id)
    with db.connect() as conn:
        conn.execute("DELETE FROM tool_results WHERE tool_call_id = ?", (first["tool_call_id"],))

    replay = execute_direct_tool_journaled(tool, args, {}, approval_id=approval.id)

    assert replay["status"] == "outcome_unknown"
    assert replay["automatic_replay_blocked"] is True
    stored_call = ToolCall.model_validate(db.fetch_one("tool_calls", first["tool_call_id"]))
    assert stored_call.status == "outcome_unknown"
    assert stored_call.outcome_unknown_at


def test_direct_async_execution_commits_result():
    task = Task(user_goal="direct async write")
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)

    async def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        return {"ok": True, "value": args["value"]}

    result = asyncio.run(
        execute_direct_tool_journaled_async(
            _direct_tool(lambda args, context: {"ok": False}),
            {"value": "async-written"},
            {},
            approval_id=approval.id,
            executor=execute,
        )
    )

    assert result["ok"] is True
    stored_call = ToolCall.model_validate(db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=1)[0])
    assert stored_call.status == "committed"


def test_direct_execution_exception_marks_write_outcome_unknown_and_redacts_failure():
    task = Task(user_goal="direct failed write")
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)
    local_path = r"\\fileserver\Private Share\Secret Project\write-result.json"

    def fail(args, context):  # noqa: ANN001, ANN202, ARG001
        raise RuntimeError(f"write failed at {local_path} with token=secret-token-1234567890")

    result = execute_direct_tool_journaled(
        _direct_tool(fail),
        {"value": "failed"},
        {},
        approval_id=approval.id,
    )

    assert result["ok"] is False
    assert result["error_type"] == "RuntimeError"
    assert local_path not in result["error"]
    assert "Private Share" not in result["error"]
    assert "Secret Project" not in result["error"]
    assert "[REDACTED_LOCAL_PATH]" in result["error"]
    assert "secret-token-1234567890" not in result["error"]
    assert "[REDACTED]" in result["error"]
    assert result["status"] == "outcome_unknown"
    assert result["outcome_unknown"] is True
    assert result["automatic_replay_blocked"] is True
    stored_call = ToolCall.model_validate(db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=1)[0])
    stored_result = ToolResult.model_validate(
        db.fetch_many("tool_results", "tool_call_id = ?", (stored_call.id,), limit=1)[0]
    )
    assert stored_call.status == "outcome_unknown"
    assert stored_call.outcome_unknown_at
    assert stored_result.ok is False
    assert stored_result.error == result["error"]


def test_direct_async_execution_exception_marks_write_outcome_unknown():
    task = Task(user_goal="direct async failed write")
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)

    async def fail(args, context):  # noqa: ANN001, ANN202, ARG001
        raise RuntimeError("async write failed with token=secret-token-1234567890")

    result = asyncio.run(
        execute_direct_tool_journaled_async(
            _direct_tool(lambda args, context: {"ok": False}),
            {"value": "failed"},
            {},
            approval_id=approval.id,
            executor=fail,
        )
    )

    assert result["ok"] is False
    assert result["error_type"] == "RuntimeError"
    assert "secret-token-1234567890" not in result["error"]
    assert "[REDACTED]" in result["error"]
    assert result["status"] == "outcome_unknown"
    assert result["outcome_unknown"] is True
    stored_call = ToolCall.model_validate(db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=1)[0])
    stored_result = ToolResult.model_validate(
        db.fetch_many("tool_results", "tool_call_id = ?", (stored_call.id,), limit=1)[0]
    )
    assert stored_call.status == "outcome_unknown"
    assert stored_call.outcome_unknown_at
    assert stored_result.ok is False
    assert stored_result.error == result["error"]


def test_direct_typed_pre_effect_failure_remains_known_and_reusable():
    task = Task(user_goal="direct write blocked before effect")
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)
    calls = 0

    def fail_before_effect(args, context):  # noqa: ANN001, ANN202, ARG001
        nonlocal calls
        calls += 1
        raise ReadBeforeWriteError("Read the target before writing.")

    tool = _direct_tool(fail_before_effect)
    args = {"value": "blocked"}
    first = execute_direct_tool_journaled(tool, args, {}, approval_id=approval.id)
    second = execute_direct_tool_journaled(tool, args, {}, approval_id=approval.id)

    assert first["ok"] is False
    assert "outcome_unknown" not in first
    assert second == first
    assert calls == 1
    stored_call = ToolCall.model_validate(db.fetch_one("tool_calls", first["tool_call_id"]))
    assert stored_call.status == "committed"


def test_direct_execution_process_interrupt_recovers_as_outcome_unknown():
    task = Task(user_goal="direct interrupted write", status=TaskPhase.EXECUTION, phase=TaskPhase.EXECUTION)
    db.upsert_model("tasks", task)
    approval = _direct_approval(task)

    def interrupt(args, context):  # noqa: ANN001, ANN202, ARG001
        raise SystemExit("simulated process termination")

    with pytest.raises(SystemExit, match="simulated process termination"):
        execute_direct_tool_journaled(_direct_tool(interrupt), {"value": "unknown"}, {}, approval_id=approval.id)

    executing = ToolCall.model_validate(db.fetch_many("tool_calls", "task_id = ?", (task.id,), limit=1)[0])
    assert executing.status == "executing"
    assert recover_interrupted_tool_executions() == [executing.id]
    recovered = ToolCall.model_validate(db.fetch_one("tool_calls", executing.id))
    assert recovered.status == "outcome_unknown"
