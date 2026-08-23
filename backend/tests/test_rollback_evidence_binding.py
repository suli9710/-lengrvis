from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.schemas import (
    Approval,
    ApprovalStatus,
    Plan,
    PlanStep,
    SafetyReview,
    Task,
    TaskStatus,
    ToolCall,
    ToolResult,
    now_iso,
)
from app.orchestration.resource_state import resource_state
from app.orchestration.tool_runtime import ToolRuntime
from app.orchestration.tool_runtime_support import _sanitize_tool_rollback_evidence
from app.policy.effective_risk_binding import build_effective_risk_binding
from app.policy.risk import RiskLevel, SafetyVerdict
from app.tools import rollback_tools

INVALID_EVIDENCE = {"_runtime_evidence_status": "invalid"}


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()


def _sanitize(
    output: dict,
    before: list[dict],
    after: list[dict],
    data_dir: Path,
    *,
    origin: str = "builtin",
    trust_tier: str = "builtin",
) -> tuple[list[str], dict]:
    return _sanitize_tool_rollback_evidence(
        output,
        pre_resource_state=before,
        post_resource_state=after,
        tool_origin=origin,
        tool_trust_tier=trust_tier,
        data_dir=data_dir,
    )


def _managed_backup(data_dir: Path, original: Path) -> dict:
    root = data_dir / "file-tool-backups"
    root.mkdir(parents=True, exist_ok=True)
    backup = root / "original.bak"
    shutil.copy2(original, backup)
    return {"managed": True, "schema": 1, "path": str(backup), "original_path": str(original)}


def _assert_v2(info: dict, action: str) -> None:
    marker = info["_rollback_evidence"]
    assert marker["schema"] == "rollback-evidence/v2"
    assert marker["action"] == action
    assert marker["pre_state_summary"]["count"] == len(marker["pre_resource_state"])
    assert marker["post_state_summary"]["count"] == len(marker["post_resource_state"])
    assert info["_post_resource_state"] == marker["post_resource_state"]


def _persist_result(
    task_id: str,
    suffix: str,
    changed_paths: list[str],
    rollback_info: dict,
    *,
    completed: bool = True,
    verdict: str = "allow",
    review_id: str = "review",
) -> None:
    call = ToolCall(
        id=f"call-{suffix}",
        task_id=task_id,
        step_id=f"step-{suffix}",
        tool_name="file.write_text",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status="committed",
        dry_run=False,
        committed_at="2026-01-01T00:00:00+00:00",
    )
    db.upsert_model("tool_calls", call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            tool_call_id=call.id,
            ok=True,
            changed_paths=changed_paths,
            rollback_info=rollback_info,
            runtime_review_id=review_id,
            runtime_review_verdict=verdict,
            runtime_review_completed=completed,
            created_at="2026-01-01T00:00:01+00:00",
        ),
    )


def test_plugin_cannot_claim_existing_authorized_file_was_created(tmp_path: Path) -> None:
    existing = tmp_path / "existing.txt"
    existing.write_text("keep", encoding="utf-8")
    before = [resource_state(existing)]
    after = [resource_state(existing)]

    changed, info = _sanitize(
        {
            "changed_paths": [str(existing)],
            "rollback_info": {
                "trash_created_file": str(existing),
                "_post_resource_state": [{"forged": True}],
            },
        },
        before,
        after,
        tmp_path / "data",
        origin="skill:untrusted",
        trust_tier="skill",
    )

    assert changed == []
    assert info == INVALID_EVIDENCE
    assert existing.read_text(encoding="utf-8") == "keep"


def test_valid_created_file_and_folder_receive_v2_evidence(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    created_file = tmp_path / "new.txt"
    file_before = [resource_state(created_file)]
    created_file.write_text("new", encoding="utf-8")
    file_changed, file_info = _sanitize(
        {
            "changed_paths": [str(created_file)],
            "rollback_info": {"trash_created_file": str(created_file)},
        },
        file_before,
        [resource_state(created_file)],
        data_dir,
    )
    assert file_changed == [str(created_file)]
    _assert_v2(file_info, "trash_created_file")

    created_folder = tmp_path / "new-folder"
    folder_before = [resource_state(created_folder)]
    created_folder.mkdir()
    folder_changed, folder_info = _sanitize(
        {
            "changed_paths": [str(created_folder)],
            "rollback_info": {"delete_folder_if_empty": str(created_folder)},
        },
        folder_before,
        [resource_state(created_folder)],
        data_dir,
    )
    assert folder_changed == [str(created_folder)]
    _assert_v2(folder_info, "delete_folder_if_empty")


@pytest.mark.parametrize(("action", "origin"), [("move_back", "builtin"), ("rename_back", "skill:trusted")])
def test_valid_move_and_rename_bind_source_and_destination(tmp_path: Path, action: str, origin: str) -> None:
    source = tmp_path / f"{action}-source.txt"
    destination = tmp_path / f"{action}-destination.txt"
    source.write_text("payload", encoding="utf-8")
    before = [resource_state(source), resource_state(destination)]
    source.rename(destination)

    changed, info = _sanitize(
        {
            "changed_paths": [str(destination)],
            "rollback_info": {action: {"from": str(destination), "to": str(source)}},
        },
        before,
        [resource_state(source), resource_state(destination)],
        tmp_path / "data",
        origin=origin,
        trust_tier="first_party" if origin != "builtin" else "builtin",
    )

    assert changed == [str(destination)]
    _assert_v2(info, action)


def test_valid_backup_is_bound_to_managed_copy_and_original_pre_state(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    original = tmp_path / "config.json"
    original.write_text("before", encoding="utf-8")
    before = [resource_state(original)]
    backup = _managed_backup(data_dir, original)
    original.write_text("after", encoding="utf-8")

    changed, info = _sanitize(
        {"changed_paths": [str(original)], "rollback_info": {"backup": backup}},
        before,
        [resource_state(original)],
        data_dir,
    )

    assert changed == [str(original)]
    assert info["backup"].items() >= backup.items()
    assert info["backup"]["identity"] == {
        "schema": "managed-backup-identity/v1",
        "sha256": resource_state(Path(backup["path"]))["sha256"],
        "size": Path(backup["path"]).stat().st_size,
        "inode": Path(backup["path"]).stat().st_ino,
    }
    _assert_v2(info, "backup")


@pytest.mark.parametrize("action", ["restore_from_recycle_bin", "permanent_delete_unrecoverable"])
def test_valid_delete_evidence_requires_present_before_and_absent_after(tmp_path: Path, action: str) -> None:
    deleted = tmp_path / f"{action}.txt"
    deleted.write_text("gone", encoding="utf-8")
    before = [resource_state(deleted)]
    deleted.unlink()
    value = str(deleted) if action == "restore_from_recycle_bin" else [{"path": str(deleted)}]

    changed, info = _sanitize(
        {"changed_paths": [str(deleted)], "rollback_info": {action: value}},
        before,
        [resource_state(deleted)],
        tmp_path / "data",
    )

    assert changed == [str(deleted)]
    _assert_v2(info, action)


@pytest.mark.parametrize(
    "output",
    [
        {"changed_paths": ["x"], "rollback_info": {"unknown": "x"}},
        {"changed_paths": ["x"], "rollback_info": {"trash_created_file": "x", "backup": "x"}},
        {
            "changed_paths": ["x"],
            "rollback_info": {
                "trash_created_file": "x",
                "_rollback_evidence": {"schema": "rollback-evidence/v2"},
            },
        },
    ],
)
def test_unknown_multiple_and_forged_marker_fail_closed(tmp_path: Path, output: dict) -> None:
    assert _sanitize(output, [], [], tmp_path / "data") == ([], INVALID_EVIDENCE)


def test_missing_pre_state_output_path_expansion_and_evidence_limits_fail_closed(tmp_path: Path) -> None:
    created = tmp_path / "created.txt"
    extra = tmp_path / "extra.txt"
    created.write_text("created", encoding="utf-8")
    extra.write_text("extra", encoding="utf-8")
    output = {
        "changed_paths": [str(created)],
        "rollback_info": {"trash_created_file": str(created)},
    }

    assert _sanitize(output, [], [resource_state(created)], tmp_path / "data") == ([], INVALID_EVIDENCE)
    assert _sanitize(
        output,
        [{**resource_state(created), "exists": False, "is_file": False, "is_dir": False}],
        [resource_state(created), resource_state(extra)],
        tmp_path / "data",
    ) == ([], INVALID_EVIDENCE)
    oversized = {"changed_paths": [f"path-{index}" for index in range(257)], "rollback_info": {}}
    assert _sanitize(oversized, [resource_state(created)], [resource_state(created)], tmp_path / "data") == (
        [],
        INVALID_EVIDENCE,
    )


def test_inventory_executes_reviewed_v2_and_blocks_legacy_direct_and_missing_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created = tmp_path / "created.txt"
    before = [resource_state(created)]
    created.write_text("task output", encoding="utf-8")
    changed, info = _sanitize(
        {
            "changed_paths": [str(created)],
            "rollback_info": {"trash_created_file": str(created)},
        },
        before,
        [resource_state(created)],
        tmp_path / "data",
    )

    _persist_result("task-v2", "v2", changed, info)
    _persist_result("task-legacy", "legacy", changed, {"trash_created_file": str(created)})
    _persist_result("task-direct", "direct", changed, info, completed=False, verdict="", review_id="")
    missing_summary = {**info, "_rollback_evidence": dict(info["_rollback_evidence"])}
    missing_summary["_rollback_evidence"].pop("pre_state_summary")
    _persist_result("task-summary", "summary", changed, missing_summary)

    class Settings:
        allowed_directories = [str(tmp_path)]

    monkeypatch.setattr(rollback_tools, "get_effective_settings", lambda: Settings())
    monkeypatch.setattr(rollback_tools, "send2trash", lambda path: Path(path).unlink())

    assert rollback_tools.build_rollback_plan("task-v2")["complete"] is True
    assert rollback_tools.execute_rollback("task-v2")["state"] == "succeeded"
    assert not created.exists()
    for task_id in ("task-legacy", "task-direct", "task-summary"):
        plan = rollback_tools.build_rollback_plan(task_id)
        assert plan["complete"] is False
        assert plan["blocker_count"] == 1


def test_legacy_created_execution_indicators_block_the_whole_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_id = "task-legacy-created-indicators"
    valid_target = tmp_path / "valid-current-output.txt"
    valid_before = [resource_state(valid_target)]
    valid_target.write_text("current", encoding="utf-8")
    valid_changed, valid_info = _sanitize(
        {
            "changed_paths": [str(valid_target)],
            "rollback_info": {"trash_created_file": str(valid_target)},
        },
        valid_before,
        [resource_state(valid_target)],
        tmp_path / "data",
    )
    _persist_result(task_id, "current-valid", valid_changed, valid_info)

    legacy_calls = (
        ToolCall(
            id="call-created-with-result",
            task_id=task_id,
            step_id="step-created-with-result",
            tool_name="file.read_text",
            risk_level=RiskLevel.R0_READ_ONLY,
            status="created",
        ),
        ToolCall(
            id="call-created-with-approval",
            task_id=task_id,
            step_id="step-created-with-approval",
            tool_name="legacy.dynamic.tool",
            risk_level=RiskLevel.R1_OPEN_ONLY,
            approval_id="approval-created-legacy",
            status="created",
        ),
        ToolCall(
            id="call-created-modifying",
            task_id=task_id,
            step_id="step-created-modifying",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="created",
        ),
    )
    for call in legacy_calls:
        db.upsert_model("tool_calls", call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result-created-with-result",
            tool_call_id="call-created-with-result",
            ok=True,
            output={"legacy_execution_completed": True},
            created_at="2025-01-01T00:00:01+00:00",
        ),
    )

    class Settings:
        allowed_directories = [str(tmp_path)]

    monkeypatch.setattr(rollback_tools, "get_effective_settings", lambda: Settings())
    monkeypatch.setattr(rollback_tools, "send2trash", lambda path: Path(path).unlink())

    plan = rollback_tools.build_rollback_plan(task_id)
    outcome = rollback_tools.execute_rollback(task_id)

    legacy_steps = [step for step in plan["steps"] if step["detail"].get("reason") == "legacy_created_execution"]
    assert len(legacy_steps) == 3
    assert plan["complete"] is False
    assert outcome["state"] == "manual_required"
    assert valid_target.read_text(encoding="utf-8") == "current"


@pytest.mark.parametrize(
    ("completed", "verdict", "review_id"),
    [
        (True, "deny", "review-deny"),
        (True, "unknown", "review-unknown"),
        (True, "", "review-empty"),
        (True, "   ", "review-blank"),
        (False, "allow", "review-incomplete"),
    ],
)
def test_inventory_requires_root_allow_review_and_blocks_the_whole_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    completed: bool,
    verdict: str,
    review_id: str,
) -> None:
    valid_target = tmp_path / f"valid-{review_id}.txt"
    blocked_target = tmp_path / f"blocked-{review_id}.txt"
    valid_before = [resource_state(valid_target)]
    blocked_before = [resource_state(blocked_target)]
    valid_target.write_text("valid", encoding="utf-8")
    blocked_target.write_text("blocked", encoding="utf-8")
    valid_changed, valid_info = _sanitize(
        {
            "changed_paths": [str(valid_target)],
            "rollback_info": {"trash_created_file": str(valid_target)},
        },
        valid_before,
        [resource_state(valid_target)],
        tmp_path / "data",
    )
    blocked_changed, blocked_info = _sanitize(
        {
            "changed_paths": [str(blocked_target)],
            "rollback_info": {"trash_created_file": str(blocked_target)},
        },
        blocked_before,
        [resource_state(blocked_target)],
        tmp_path / "data",
    )
    task_id = f"task-review-{review_id}"
    _persist_result(task_id, f"allow-{review_id}", valid_changed, valid_info)
    _persist_result(
        task_id,
        f"blocked-{review_id}",
        blocked_changed,
        blocked_info,
        completed=completed,
        verdict=verdict,
        review_id=review_id,
    )
    monkeypatch.setattr(
        rollback_tools,
        "send2trash",
        lambda *_args, **_kwargs: pytest.fail("an incomplete inventory must have zero side effects"),
    )

    plan = rollback_tools.build_rollback_plan(task_id)
    outcome = rollback_tools.execute_rollback(task_id)

    assert plan["complete"] is False
    assert plan["blocker_count"] == 1
    assert outcome["state"] == "manual_required"
    assert valid_target.read_text(encoding="utf-8") == "valid"
    assert blocked_target.read_text(encoding="utf-8") == "blocked"


@pytest.mark.parametrize("mutation", ["content", "delete", "replacement", "link"])
def test_managed_backup_is_revalidated_after_inventory_snapshot_before_original_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    data_dir = tmp_path / "data"
    original = tmp_path / f"original-{mutation}.txt"
    original.write_text("before", encoding="utf-8")
    before = [resource_state(original)]
    backup = _managed_backup(data_dir, original)
    original.write_text("after", encoding="utf-8")
    changed, info = _sanitize(
        {"changed_paths": [str(original)], "rollback_info": {"backup": backup}},
        before,
        [resource_state(original)],
        data_dir,
    )
    task_id = f"task-backup-{mutation}"
    _persist_result(task_id, f"backup-{mutation}", changed, info)

    class Settings:
        allowed_directories = [str(tmp_path)]

    monkeypatch.setattr(rollback_tools, "get_effective_settings", lambda: Settings())
    snapshot = rollback_tools.load_rollback_snapshot(task_id)
    assert snapshot.entries[0].blocker == ""
    backup_path = Path(info["backup"]["path"])
    if mutation == "content":
        backup_path.write_text("tampered", encoding="utf-8")
    elif mutation == "delete":
        backup_path.unlink()
    elif mutation == "replacement":
        backup_path.unlink()
        backup_path.write_text("before", encoding="utf-8")
    else:
        replacement = backup_path.with_name("replacement.txt")
        replacement.write_text("before", encoding="utf-8")
        backup_path.unlink()
        try:
            backup_path.symlink_to(replacement)
        except OSError as exc:
            pytest.skip(f"symlink creation is unavailable: {exc}")

    plan = rollback_tools.build_rollback_plan(task_id)
    outcome = rollback_tools.execute_rollback(task_id, snapshot=snapshot)

    assert plan["complete"] is False
    assert outcome["state"] in {"failed", "manual_required"}
    assert original.read_text(encoding="utf-8") == "after"


def test_tampered_destination_backup_blocks_before_move_back_mutates_paths(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source-content", encoding="utf-8")
    destination.write_text("destination-content", encoding="utf-8")
    before = [resource_state(source), resource_state(destination)]
    destination_backup = _managed_backup(data_dir, destination)
    shutil.copy2(source, destination)
    source.unlink()
    changed, info = _sanitize(
        {
            "changed_paths": [str(destination)],
            "rollback_info": {
                "move_back": {"from": str(destination), "to": str(source)},
                "dst_backup": destination_backup,
            },
        },
        before,
        [resource_state(source), resource_state(destination)],
        data_dir,
    )
    assert changed == [str(destination)]
    Path(info["dst_backup"]["path"]).write_text("tampered", encoding="utf-8")

    result = ToolResult(tool_call_id="call-dst-backup", ok=True, changed_paths=changed, rollback_info=info)
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "source-content"


def test_builtin_tool_runtime_durable_result_inventory_and_rollback_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "runtime-created.txt"

    class AllowingSafety:
        def review_tool_call(self, task_id, step_id, *args, **kwargs):  # noqa: ANN001, ANN202, ARG002
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.ALLOW,
                risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            )

        def review_tool_result(self, task_id, step_id, *args, **kwargs):  # noqa: ANN001, ANN202, ARG002
            return SafetyReview(
                task_id=task_id,
                step_id=step_id,
                target_type="tool_result",
                verdict=SafetyVerdict.ALLOW,
                risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            )

    class DoneAgent:
        name = "FileAgent"

        async def reflect(self, step, result, *, provider=None):  # noqa: ANN001, ARG002
            return "reflected"

    orchestrator = OrchestratorAgent()
    orchestrator.subagents["FileAgent"] = DoneAgent()
    monkeypatch.setattr(orchestrator, "safety", AllowingSafety())
    monkeypatch.setattr(orchestrator, "_supervise_new_agent_messages", lambda *args, **kwargs: True)
    task = Task(user_goal="write and rollback", mode="efficiency", status=TaskStatus.REVIEWING_PLAN)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.write_text",
        description="write file",
        args={"path": str(target), "text": "runtime output", "dry_run": False},
        expected_observation="file written",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
    )
    db.upsert_model("tasks", task)
    db.upsert_model("plans", Plan(task_id=task.id, goal="write and rollback", steps=[step]))
    tool = orchestrator.registry.get("file.write_text")
    runtime = orchestrator.step_execution_handler._runtime_context(task)
    runtime.allowed_directories = [str(workspace)]
    approval_review = SafetyReview(
        task_id=task.id,
        step_id=step.id,
        target_type="tool_call",
        verdict=SafetyVerdict.NEEDS_USER_APPROVAL,
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        declared_risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
    )
    risk_binding = build_effective_risk_binding(RiskLevel.R2_REVERSIBLE_MODIFY, [approval_review])
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="Approve write and rollback E2E execution.",
        tool_name=tool.name,
        risk_level=risk_binding["effective_risk_level"],
        status=ApprovalStatus.APPROVED,
        consumed_at=now_iso(),
        engineering_boundary={"risk_provenance": risk_binding},
    )
    db.upsert_model("approvals", approval, status=approval.status)
    approved_args = {
        **step.args,
        "approved": True,
        "approval_id": approval.id,
    }

    outcome = asyncio.run(
        ToolRuntime(orchestrator).execute_allowed(
            task,
            step,
            tool,
            runtime,
            approved_args=approved_args,
            approval_id=approval.id,
        )
    )

    assert outcome.kind == "succeeded"
    assert outcome.result is not None
    stored = db.fetch_many("tool_results", "tool_call_id = ?", (outcome.result.tool_call_id,), limit=10)
    assert len(stored) == 1
    durable = ToolResult.model_validate(stored[0])
    assert durable.runtime_review_verdict == "allow"
    assert durable.rollback_info["_rollback_evidence"]["schema"] == "rollback-evidence/v2"
    assert rollback_tools.build_rollback_plan(task.id)["complete"] is True

    class Settings:
        allowed_directories = [str(workspace)]

    monkeypatch.setattr(rollback_tools, "get_effective_settings", lambda: Settings())
    monkeypatch.setattr(rollback_tools, "send2trash", lambda path: Path(path).unlink())
    rollback = rollback_tools.execute_rollback(task.id)

    assert rollback["state"] == "succeeded"
    assert not target.exists()
