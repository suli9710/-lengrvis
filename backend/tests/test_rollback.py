"""Tests for P1-3 rollback executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import db
from app.core.errors import SecurityError
from app.core.schemas import Approval, ApprovalStatus, ToolResult
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.permissions import PermissionStore
from app.policy.risk import RiskLevel
from app.tools import rollback_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


def test_rollback_move_back_returns_file(tmp_path: Path):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-1",
        ok=True,
        rollback_info={"move_back": {"from": str(moved), "to": str(original)}},
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is True
    assert original.exists() and not moved.exists()


def test_rollback_trash_created_file_sends_to_recycle_bin(tmp_path: Path):
    created = tmp_path / "report.md"
    created.write_text("# report", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-2",
        ok=True,
        rollback_info={"trash_created_file": str(created)},
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is True
    assert not created.exists() or outcome.get("detail") == "already absent"


def test_rollback_delete_folder_if_empty(tmp_path: Path):
    folder = tmp_path / "empty-folder"
    folder.mkdir()
    result = ToolResult(
        tool_call_id="call-3",
        ok=True,
        rollback_info={"delete_folder_if_empty": str(folder)},
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is True
    assert not folder.exists()


def test_rollback_delete_folder_if_empty_skipped_when_not_empty(tmp_path: Path):
    folder = tmp_path / "with-stuff"
    folder.mkdir()
    (folder / "child.txt").write_text("x", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-4",
        ok=True,
        rollback_info={"delete_folder_if_empty": str(folder)},
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is False
    assert folder.exists()


def test_rollback_restore_backup(tmp_path: Path):
    original = tmp_path / "config.json"
    original.write_text("changed-content", encoding="utf-8")
    backup = tmp_path / "config.json.bak"
    backup.write_text("original-content", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-5",
        ok=True,
        rollback_info={"backup": str(backup)},
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is True
    assert original.read_text(encoding="utf-8") == "original-content"
    assert not backup.exists()


def test_rollback_restore_from_recycle_bin_requires_user_action(tmp_path: Path):
    result = ToolResult(
        tool_call_id="call-6",
        ok=True,
        rollback_info={"restore_from_recycle_bin": str(tmp_path / "trashed.txt")},
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is False
    assert outcome.get("requires_user_action") is True


def test_rollback_fails_closed_without_authorized_directories(tmp_path: Path):
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-no-auth",
        ok=True,
        rollback_info={"move_back": {"from": str(moved), "to": str(tmp_path / "from.txt")}},
    )

    outcome = rollback_tools.rollback_tool_result(result)

    assert outcome["ok"] is False
    assert "No authorized directories configured" in outcome["detail"]
    assert moved.exists()


def test_cleanup_rollback_preview_fails_closed_without_authorized_directories(tmp_path: Path):
    with pytest.raises(SecurityError, match="No authorized directories configured"):
        rollback_tools.rollback_cleanup_result(
            {"rollback_info": {"restore_from_recycle_bin": str(tmp_path / "trashed.txt")}},
            {},
        )


def test_cleanup_rollback_rejects_random_approval_id_for_live_execution(tmp_path: Path):
    with pytest.raises(SecurityError, match="approval"):
        rollback_tools.rollback_cleanup_result(
            {
                "rollback_info": {"restore_from_recycle_bin": str(tmp_path / "trashed.txt")},
                "dry_run": False,
                "approved": True,
                "approval_id": "random-forged-approval",
            },
            {"allowed_directories": [str(tmp_path)]},
        )


def test_cleanup_rollback_accepts_bound_approval_and_consumes_it(tmp_path: Path):
    args = {
        "rollback_info": {"restore_from_recycle_bin": str(tmp_path / "trashed.txt")},
        "dry_run": False,
    }
    context = {"allowed_directories": [str(tmp_path)]}
    preview = rollback_tools.rollback_cleanup_result({**args, "dry_run": True}, context)
    approval = Approval(
        task_id="task_cleanup_rollback",
        step_id="step_cleanup_rollback",
        message="Approve cleanup rollback",
        status=ApprovalStatus.APPROVED,
        tool_name="file.cleanup_rollback",
        risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value,
        args_binding_hmac=args_binding_hmac(
            "file.cleanup_rollback",
            args,
            task_id="task_cleanup_rollback",
            step_id="step_cleanup_rollback",
        ),
        preview_hmac=preview_hmac(preview),
        settings_fingerprint=settings_fingerprint(None, allowed_directories=context["allowed_directories"]),
        permission_policy_version=permission_policy_version(PermissionStore().updated_at()),
        tool_version="1",
        diff_preview=preview,
    )
    db.upsert_model("approvals", approval, status=approval.status)

    result = rollback_tools.rollback_cleanup_result({**args, "approved": True, "approval_id": approval.id}, context)

    assert result["ok"] is False
    assert result["requires_user_action"] is True
    refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed.consumed_at


def test_execute_rollback_uses_effective_authorized_directories(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-execute",
        ok=True,
        rollback_info={"move_back": {"from": str(moved), "to": str(original)}},
    )
    monkeypatch.setattr("app.tools.rollback_tools._results_for_task", lambda _task_id: [result])

    class Settings:
        allowed_directories = [str(tmp_path)]

    monkeypatch.setattr("app.tools.rollback_tools.get_effective_settings", lambda: Settings())

    outcome = rollback_tools.execute_rollback("task-1")

    assert outcome["count"] == 1
    assert outcome["executed"][0]["ok"] is True
    assert original.exists() and not moved.exists()


def test_rollback_noop_when_no_info():
    result = ToolResult(tool_call_id="call-x", ok=True, rollback_info={})
    outcome = rollback_tools.rollback_tool_result(result)
    assert outcome["ok"] is True
    assert outcome["action"] == "noop"
