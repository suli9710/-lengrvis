"""Tests for P1-3 rollback executor."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from app.core import db
from app.core.errors import SecurityError
from app.core.schemas import Approval, ApprovalStatus, ToolCall, ToolResult
from app.orchestration.resource_state import resource_state
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.permissions import PermissionStore
from app.policy.risk import RiskLevel
from app.tools import file_tools, rollback_tools
from app.tools.managed_backups import create_managed_backup
from app.tools.tool_abort import ToolAbortedError


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


def _rollback_info(info: dict, *paths: Path) -> dict:
    return {**info, "_post_resource_state": [resource_state(path) for path in paths]}


def _snapshot(task_id: str, *results: ToolResult) -> rollback_tools.RollbackSnapshot:
    return rollback_tools.RollbackSnapshot(
        task_id=task_id,
        entries=tuple(
            rollback_tools.RollbackSnapshotEntry(
                tool_call_id=result.tool_call_id,
                effect_at=result.created_at,
                stable_id=result.id,
                result=result,
            )
            for result in results
        ),
    )


def test_rollback_move_back_returns_file(tmp_path: Path):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-1",
        ok=True,
        rollback_info=_rollback_info({"move_back": {"from": str(moved), "to": str(original)}}, moved, original),
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is True
    assert outcome["verified"] is True
    assert outcome["verification"]["checks"] == {
        "source_absent": True,
        "target_is_file": True,
        "content_match": True,
    }
    assert original.exists() and not moved.exists()


def test_rollback_move_back_restores_overwritten_destination(tmp_path: Path):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    original.write_text("source-content", encoding="utf-8")
    moved.write_text("destination-content", encoding="utf-8")
    destination_backup = create_managed_backup(moved)
    moved.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
    original.unlink()
    result = ToolResult(
        tool_call_id="call-move-overwrite",
        ok=True,
        rollback_info=_rollback_info(
            {
                "move_back": {"from": str(moved), "to": str(original)},
                "dst_backup": destination_backup,
            },
            moved,
            original,
        ),
    )

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is True
    assert original.read_text(encoding="utf-8") == "source-content"
    assert moved.read_text(encoding="utf-8") == "destination-content"
    assert outcome["dst_restore"]["verification"]["status"] == "passed"
    assert not Path(str(destination_backup["path"])).exists()


def test_compound_move_stops_before_destination_restore_after_lease_loss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    original.write_text("source-content", encoding="utf-8")
    moved.write_text("destination-content", encoding="utf-8")
    destination_backup = create_managed_backup(moved)
    moved.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
    original.unlink()
    result = ToolResult(
        tool_call_id="call-move-lease-loss",
        ok=True,
        rollback_info=_rollback_info(
            {
                "move_back": {"from": str(moved), "to": str(original)},
                "dst_backup": destination_backup,
            },
            moved,
            original,
        ),
    )

    class Settings:
        allowed_directories = [str(tmp_path)]

    monkeypatch.setattr(rollback_tools, "get_effective_settings", lambda: Settings())
    heartbeat_results = iter((True, False))

    with pytest.raises(RuntimeError, match="lease was lost"):
        rollback_tools.execute_rollback(
            "task-move-lease-loss",
            snapshot=_snapshot("task-move-lease-loss", result),
            heartbeat=lambda: next(heartbeat_results),
        )

    assert original.read_text(encoding="utf-8") == "source-content"
    assert not moved.exists()
    assert Path(str(destination_backup["path"])).read_text(encoding="utf-8") == "destination-content"


def test_lease_loss_after_trash_stops_before_the_next_rollback_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    results = (
        ToolResult(
            id="result-first-trash",
            tool_call_id="call-first-trash",
            ok=True,
            rollback_info=_rollback_info({"trash_created_file": str(first)}, first),
        ),
        ToolResult(
            id="result-second-trash",
            tool_call_id="call-second-trash",
            ok=True,
            rollback_info=_rollback_info({"trash_created_file": str(second)}, second),
        ),
    )

    class Settings:
        allowed_directories = [str(tmp_path)]

    monkeypatch.setattr(rollback_tools, "get_effective_settings", lambda: Settings())
    monkeypatch.setattr(rollback_tools, "send2trash", lambda path: Path(path).unlink())
    heartbeat_results = iter((True, False))

    with pytest.raises(RuntimeError, match="lease was lost"):
        rollback_tools.execute_rollback(
            "task-trash-lease-loss",
            snapshot=_snapshot("task-trash-lease-loss", *results),
            heartbeat=lambda: next(heartbeat_results),
        )

    assert not first.exists()
    assert second.read_text(encoding="utf-8") == "second"


def test_rollback_trash_created_file_sends_to_recycle_bin(tmp_path: Path):
    created = tmp_path / "report.md"
    created.write_text("# report", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-2",
        ok=True,
        rollback_info=_rollback_info({"trash_created_file": str(created)}, created),
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is True
    assert outcome["verification"]["status"] == "passed"
    assert not created.exists() or outcome.get("detail") == "already absent"


def test_rollback_does_not_trash_created_file_modified_after_task(tmp_path: Path):
    created = tmp_path / "report.md"
    created.write_text("task-output", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-created-file-user-edit",
        ok=True,
        rollback_info=_rollback_info({"trash_created_file": str(created)}, created),
    )
    created.write_text("user-change", encoding="utf-8")

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["requires_user_action"] is True
    assert outcome["verification"]["status"] == "manual_required"
    assert created.read_text(encoding="utf-8") == "user-change"


def test_rollback_does_not_treat_dangling_symlink_as_an_absent_created_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created = tmp_path / "report.md"
    created.write_text("task-output", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-created-file-replaced-by-link",
        ok=True,
        rollback_info=_rollback_info({"trash_created_file": str(created)}, created),
    )
    created.unlink()
    try:
        created.symlink_to(tmp_path / "missing-target.md")
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")
    monkeypatch.setattr(
        rollback_tools,
        "send2trash",
        lambda *_args, **_kwargs: pytest.fail("dangling symlink must not be sent to trash automatically"),
    )

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["requires_user_action"] is True
    assert outcome["verification"]["status"] == "manual_required"
    assert created.is_symlink()


def test_rollback_state_capture_does_not_follow_an_outside_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not be read", encoding="utf-8")
    link = workspace / "task-output.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")
    monkeypatch.setattr(
        file_tools,
        "sha256_file",
        lambda _path: pytest.fail("rollback evidence must not hash a filesystem-link target"),
    )

    state = file_tools._rollback_resource_state(
        link,
        [str(workspace)],
        {"allowed_directories": [str(workspace)]},
    )

    assert state["path"] == str(link.absolute())
    assert state["exists"] is True
    assert state["is_reparse_point"] is True
    assert state["auto_rollback_safe"] is False
    assert "sha256" not in state


def test_rollback_delete_folder_if_empty(tmp_path: Path):
    folder = tmp_path / "empty-folder"
    folder.mkdir()
    result = ToolResult(
        tool_call_id="call-3",
        ok=True,
        rollback_info=_rollback_info({"delete_folder_if_empty": str(folder)}, folder),
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is True
    assert outcome["verification"]["checks"]["target_absent"] is True
    assert not folder.exists()


def test_rollback_delete_folder_if_empty_skipped_when_not_empty(tmp_path: Path):
    folder = tmp_path / "with-stuff"
    folder.mkdir()
    (folder / "child.txt").write_text("x", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-4",
        ok=True,
        rollback_info=_rollback_info({"delete_folder_if_empty": str(folder)}, folder),
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
        rollback_info=_rollback_info({"backup": str(backup)}, original),
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})
    assert outcome["ok"] is True
    assert outcome["verification"]["checks"] == {
        "original_is_file": True,
        "content_match": True,
        "backup_absent": True,
    }
    assert original.read_text(encoding="utf-8") == "original-content"
    assert not backup.exists()


def test_rollback_does_not_overwrite_file_modified_after_task(tmp_path: Path):
    original = tmp_path / "config.json"
    original.write_text("task-output", encoding="utf-8")
    backup = tmp_path / "config.json.bak"
    backup.write_text("old-content", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-backup-user-edit",
        ok=True,
        rollback_info=_rollback_info({"backup": str(backup)}, original),
    )
    original.write_text("user-change", encoding="utf-8")

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["requires_user_action"] is True
    assert outcome["verification"]["status"] == "manual_required"
    assert original.read_text(encoding="utf-8") == "user-change"
    assert backup.read_text(encoding="utf-8") == "old-content"


def test_rollback_restore_backup_aborts_before_copy(tmp_path: Path):
    original = tmp_path / "config.json"
    original.write_text("changed-content", encoding="utf-8")
    backup = tmp_path / "config.json.bak"
    backup.write_text("original-content", encoding="utf-8")
    abort = threading.Event()
    abort.set()
    result = ToolResult(
        tool_call_id="call-abort",
        ok=True,
        rollback_info=_rollback_info({"backup": str(backup)}, original),
    )

    with pytest.raises(ToolAbortedError):
        rollback_tools.rollback_tool_result(
            result,
            {"allowed_directories": [str(tmp_path)], "_tool_abort_event": abort},
        )

    assert original.read_text(encoding="utf-8") == "changed-content"
    assert backup.exists()


def test_rollback_restore_managed_backup(tmp_path: Path):
    original = tmp_path / "config.json"
    original.write_text("original-content", encoding="utf-8")
    backup = create_managed_backup(original)
    original.write_text("changed-content", encoding="utf-8")

    result = ToolResult(
        tool_call_id="call-managed-backup",
        ok=True,
        rollback_info=_rollback_info({"backup": backup}, original),
    )
    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is True
    assert original.read_text(encoding="utf-8") == "original-content"
    assert not Path(str(backup["path"])).exists()


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


def test_rollback_failure_detail_redacts_paths_and_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-redacted-failure",
        ok=True,
        rollback_info=_rollback_info({"move_back": {"from": str(moved), "to": str(original)}}, moved, original),
    )

    def fake_move_file(*args, **kwargs):  # noqa: ANN001, ANN002
        raise OSError("move failed for C:/Users/Suli/private/private-rollback.xlsx token=rollback-secret-1234567890")

    monkeypatch.setattr(rollback_tools, "safe_move_file", fake_move_file)

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["action"] == "move_back"
    assert "move failed" in outcome["detail"]
    assert "rollback-secret-1234567890" not in outcome["detail"]
    assert "C:/Users/Suli/private/private-rollback.xlsx" not in outcome["detail"]
    assert "private-rollback.xlsx" not in outcome["detail"]
    assert "[REDACTED]" in outcome["detail"]


def test_rollback_move_back_does_not_swallow_unexpected_move_bugs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-move-bug",
        ok=True,
        rollback_info=_rollback_info({"move_back": {"from": str(moved), "to": str(original)}}, moved, original),
    )

    def buggy_move_file(*args, **kwargs):  # noqa: ANN001, ANN002
        raise TypeError("move implementation bug")

    monkeypatch.setattr(rollback_tools, "safe_move_file", buggy_move_file)

    with pytest.raises(TypeError, match="move implementation bug"):
        rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})


def test_rollback_delete_empty_folder_reports_expected_filesystem_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    folder = tmp_path / "empty-folder"
    folder.mkdir()
    result = ToolResult(
        tool_call_id="call-delete-permission",
        ok=True,
        rollback_info=_rollback_info({"delete_folder_if_empty": str(folder)}, folder),
    )

    def deny_mutation(*args, **kwargs):  # noqa: ANN001, ANN002
        raise PermissionError("permission denied")

    monkeypatch.setattr(rollback_tools, "ensure_mutation_path_safe", deny_mutation)

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["action"] == "delete_folder_if_empty"
    assert "permission denied" in outcome["detail"]
    assert folder.exists()


def test_rollback_restore_backup_reports_expected_copy_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    original = tmp_path / "config.json"
    original.write_text("changed-content", encoding="utf-8")
    backup = tmp_path / "config.json.bak"
    backup.write_text("original-content", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-restore-copy-error",
        ok=True,
        rollback_info=_rollback_info({"backup": str(backup)}, original),
    )

    def fail_copy(*args, **kwargs):  # noqa: ANN001, ANN002
        raise shutil.SameFileError("same file")

    monkeypatch.setattr(rollback_tools, "safe_copy_file_between_scopes", fail_copy)

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["action"] == "restore_backup"
    assert "same file" in outcome["detail"]
    assert backup.exists()
    assert original.read_text(encoding="utf-8") == "changed-content"


def test_rollback_restore_backup_does_not_swallow_unexpected_copy_bugs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    original = tmp_path / "config.json"
    original.write_text("changed-content", encoding="utf-8")
    backup = tmp_path / "config.json.bak"
    backup.write_text("original-content", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-restore-copy-bug",
        ok=True,
        rollback_info=_rollback_info({"backup": str(backup)}, original),
    )

    def buggy_copy(*args, **kwargs):  # noqa: ANN001, ANN002
        raise TypeError("copy implementation bug")

    monkeypatch.setattr(rollback_tools, "safe_copy_file_between_scopes", buggy_copy)

    with pytest.raises(TypeError, match="copy implementation bug"):
        rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})


def test_rollback_move_back_fails_when_post_action_state_does_not_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-move-noop",
        ok=True,
        rollback_info=_rollback_info({"move_back": {"from": str(moved), "to": str(original)}}, moved, original),
    )
    monkeypatch.setattr(rollback_tools, "safe_move_file", lambda *args, **kwargs: None)

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["verified"] is False
    assert outcome["verification"]["status"] == "failed"
    assert outcome["verification"]["checks"] == {
        "source_absent": False,
        "target_is_file": False,
        "content_match": False,
    }


def test_rollback_restore_backup_keeps_backup_when_content_verification_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    original = tmp_path / "config.json"
    original.write_text("changed-content", encoding="utf-8")
    backup = tmp_path / "config.json.bak"
    backup.write_text("original-content", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-restore-wrong-content",
        ok=True,
        rollback_info=_rollback_info({"backup": str(backup)}, original),
    )

    def copy_wrong_content(*args, **kwargs):  # noqa: ANN001, ANN002
        original.write_text("wrong-content", encoding="utf-8")

    monkeypatch.setattr(rollback_tools, "safe_copy_file_between_scopes", copy_wrong_content)

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["verification"]["status"] == "failed"
    assert outcome["verification"]["checks"]["content_match"] is False
    assert backup.exists()


def test_rollback_trash_fails_when_os_api_returns_without_removing_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    created = tmp_path / "report.md"
    created.write_text("# report", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-trash-noop",
        ok=True,
        rollback_info=_rollback_info({"trash_created_file": str(created)}, created),
    )
    monkeypatch.setattr(rollback_tools, "send2trash", lambda *args, **kwargs: None)

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["verification"]["status"] == "failed"
    assert outcome["verification"]["checks"]["target_absent"] is False
    assert created.exists()


def test_rollback_trash_keeps_os_boundary_failures_best_effort(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    created = tmp_path / "report.md"
    created.write_text("# report", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-trash-boundary",
        ok=True,
        rollback_info=_rollback_info({"trash_created_file": str(created)}, created),
    )

    def fail_trash(*args, **kwargs):  # noqa: ANN001, ANN002
        raise Exception("COM init failed")  # noqa: TRY002

    monkeypatch.setattr(rollback_tools, "send2trash", fail_trash)

    outcome = rollback_tools.rollback_tool_result(result, {"allowed_directories": [str(tmp_path)]})

    assert outcome["ok"] is False
    assert outcome["action"] == "trash"
    assert "COM init failed" in outcome["detail"]
    assert created.exists()


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


def test_legacy_rollback_records_are_ordered_but_not_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    older_call = ToolCall(
        id="call-older",
        task_id="task-ordered-rollback",
        step_id="step-older",
        tool_name="file.write_text",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status="committed",
        dry_run=False,
        committed_at="2025-01-01T00:00:01+00:00",
        created_at="2025-01-01T00:00:02+00:00",
    )
    newer_call = ToolCall(
        id="call-newer",
        task_id="task-ordered-rollback",
        step_id="step-newer",
        tool_name="file.write_text",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status="committed",
        dry_run=False,
        committed_at="2025-01-01T00:00:03+00:00",
        created_at="2025-01-01T00:00:00+00:00",
    )
    effect_times = {
        older_call.id: "2025-01-01T01:00:00+02:00",
        newer_call.id: "2025-01-01T00:30:00+00:00",
    }
    for call in (older_call, newer_call):
        db.upsert_model("tool_calls", call)
        db.upsert_model(
            "tool_results",
            ToolResult(
                tool_call_id=call.id,
                ok=True,
                rollback_info={"trash_created_file": call.step_id},
                created_at=effect_times[call.id],
            ),
        )

    executed: list[str] = []

    def fake_rollback(result: ToolResult, _context: dict) -> dict:
        executed.append(result.tool_call_id)
        return {
            "ok": True,
            "action": "test",
            "verified": True,
            "verification": {"status": "passed", "method": "test"},
        }

    monkeypatch.setattr(rollback_tools, "_rollback_context", lambda: {})
    monkeypatch.setattr(rollback_tools, "rollback_tool_result", fake_rollback)

    plan = rollback_tools.build_rollback_plan("task-ordered-rollback")
    outcome = rollback_tools.execute_rollback("task-ordered-rollback")

    assert [step["tool_call_id"] for step in plan["steps"]] == ["call-newer", "call-older"]
    assert executed == []
    assert [step["tool_call_id"] for step in outcome["executed"]] == ["call-newer", "call-older"]
    assert outcome["state"] == "manual_required"


def test_rollback_snapshot_digest_binds_durable_result_identity_and_timestamp() -> None:
    first = ToolResult(
        id="result-first",
        tool_call_id="call-same-plan",
        ok=True,
        rollback_info={"trash_created_file": "same.txt"},
        created_at="2025-01-01T00:00:00+00:00",
    )
    replacement = ToolResult(
        id="result-replacement",
        tool_call_id="call-same-plan",
        ok=True,
        rollback_info={"trash_created_file": "same.txt"},
        created_at="2025-01-01T00:00:01+00:00",
    )
    first_snapshot = _snapshot("task-exact-snapshot", first)
    replacement_snapshot = _snapshot("task-exact-snapshot", replacement)

    assert rollback_tools.build_rollback_plan(
        "task-exact-snapshot",
        snapshot=first_snapshot,
    ) == rollback_tools.build_rollback_plan(
        "task-exact-snapshot",
        snapshot=replacement_snapshot,
    )
    assert rollback_tools.rollback_snapshot_hmac(first_snapshot) != rollback_tools.rollback_snapshot_hmac(
        replacement_snapshot
    )


def test_legacy_write_then_move_inventory_blocks_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = {"allowed_directories": [str(workspace)]}
    original = workspace / "draft.txt"
    moved = workspace / "final.txt"

    write_output = file_tools.write_text(
        {"path": str(original), "text": "task output", "dry_run": False},
        context,
    )
    move_output = file_tools.move_file(
        {"source": str(original), "destination": str(moved), "dry_run": False},
        context,
    )
    calls = (
        ToolCall(
            id="call-write-before-move",
            task_id="task-write-move",
            step_id="step-write",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="committed",
            dry_run=False,
            committed_at="2025-01-01T00:00:01+00:00",
            created_at="2025-01-01T00:00:00+00:00",
        ),
        ToolCall(
            id="call-move-after-write",
            task_id="task-write-move",
            step_id="step-move",
            tool_name="file.move",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="committed",
            dry_run=False,
            committed_at="2025-01-01T00:00:03+00:00",
            created_at="2025-01-01T00:00:02+00:00",
        ),
    )
    results = (
        ToolResult(
            id="result-write-before-move",
            tool_call_id=calls[0].id,
            ok=True,
            changed_paths=write_output["changed_paths"],
            rollback_info=write_output["rollback_info"],
            created_at="2025-01-01T00:00:01+00:00",
        ),
        ToolResult(
            id="result-move-after-write",
            tool_call_id=calls[1].id,
            ok=True,
            changed_paths=move_output["changed_paths"],
            rollback_info=move_output["rollback_info"],
            created_at="2025-01-01T00:00:03+00:00",
        ),
    )
    for call, result in zip(calls, results, strict=True):
        db.upsert_model("tool_calls", call)
        db.upsert_model("tool_results", result)

    class Settings:
        allowed_directories = [str(workspace)]

    monkeypatch.setattr(rollback_tools, "get_effective_settings", lambda: Settings())
    monkeypatch.setattr(rollback_tools, "send2trash", lambda path: Path(path).unlink())

    outcome = rollback_tools.execute_rollback("task-write-move")

    assert [item["tool_call_id"] for item in outcome["executed"]] == [
        "call-move-after-write",
        "call-write-before-move",
    ]
    assert outcome["state"] == "manual_required"
    assert not original.exists()
    assert moved.read_text(encoding="utf-8") == "task output"


def test_existing_folder_creation_is_a_noop_without_rollback_inventory(tmp_path: Path) -> None:
    folder = tmp_path / "existing"
    folder.mkdir()
    context = {"allowed_directories": [str(tmp_path)]}

    output = file_tools.create_folder({"path": str(folder), "dry_run": False}, context)

    assert output == {"changed_paths": [], "rollback_info": {}, "no_side_effect": True}
    call = ToolCall(
        id="call-create-existing-folder",
        task_id="task-create-existing-folder",
        step_id="step-create-existing-folder",
        tool_name="file.create_folder",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status="committed",
        dry_run=False,
        committed_at="2025-01-01T00:00:00+00:00",
    )
    db.upsert_model("tool_calls", call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result-create-existing-folder",
            tool_call_id=call.id,
            ok=True,
            output=output,
            changed_paths=output["changed_paths"],
            rollback_info=output["rollback_info"],
        ),
    )

    assert rollback_tools.build_rollback_plan(call.task_id) == {
        "task_id": call.task_id,
        "steps": [],
        "count": 0,
        "blocker_count": 0,
        "complete": True,
    }


def test_modifying_non_file_call_without_rollback_evidence_blocks_the_inventory() -> None:
    task_id = "task-mcp-side-effect-without-evidence"
    call = ToolCall(
        id="call-mcp-side-effect-without-evidence",
        task_id=task_id,
        step_id="step-mcp-side-effect-without-evidence",
        tool_name="mcp.remote.update",
        risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        status="committed",
        dry_run=False,
        committed_at="2025-01-01T00:00:00+00:00",
    )
    db.upsert_model("tool_calls", call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result-mcp-side-effect-without-evidence",
            tool_call_id=call.id,
            ok=True,
            output={"updated": True},
            changed_paths=[],
            rollback_info={},
            created_at="2025-01-01T00:00:01+00:00",
        ),
    )

    plan = rollback_tools.build_rollback_plan(task_id)
    outcome = rollback_tools.execute_rollback(task_id)

    assert plan["complete"] is False
    assert plan["blocker_count"] == 1
    assert plan["steps"][0]["detail"]["reason"] == "missing_rollback_evidence"
    assert outcome["state"] == "manual_required"
    assert outcome["attempted"] == 1
    assert outcome["manual_required"] == 1


def test_rollback_inventory_rejects_call_json_that_disagrees_with_physical_columns() -> None:
    task_id = "task-physical-call-binding"
    call = ToolCall(
        id="call-physical-call-binding",
        task_id=task_id,
        step_id="step-physical-call-binding",
        tool_name="file.write_text",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status="committed",
        dry_run=False,
        committed_at="2025-01-01T00:00:00+00:00",
    )
    db.upsert_model("tool_calls", call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result-physical-call-binding",
            tool_call_id=call.id,
            ok=True,
            output={"ok": True},
            created_at="2025-01-01T00:00:01+00:00",
        ),
    )
    tampered = call.model_dump(mode="json")
    tampered["status"] = "prepared"
    tampered["dry_run"] = True
    tampered["risk_level"] = RiskLevel.R0_READ_ONLY.value
    with db.connect() as conn:
        conn.execute("UPDATE tool_calls SET data = ? WHERE id = ?", (db._json(tampered), call.id))

    plan = rollback_tools.build_rollback_plan(task_id)

    assert plan["complete"] is False
    assert plan["blocker_count"] == 1
    assert plan["steps"][0]["detail"]["reason"] == "corrupt_tool_call"


def test_legacy_approved_call_cannot_use_dry_run_flag_to_hide_missing_evidence() -> None:
    task_id = "task-legacy-approved-missing-evidence"
    call = ToolCall(
        id="call-legacy-approved-missing-evidence",
        task_id=task_id,
        step_id="step-legacy-approved-missing-evidence",
        tool_name="legacy.dynamic.modify",
        risk_level=RiskLevel.R1_OPEN_ONLY,
        approval_id="approval-legacy-dynamic-risk",
        status="committed",
        dry_run=True,
        committed_at="2025-01-01T00:00:00+00:00",
    )
    db.upsert_model("tool_calls", call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result-legacy-approved-missing-evidence",
            tool_call_id=call.id,
            ok=True,
            output={"updated": True},
            created_at="2025-01-01T00:00:01+00:00",
        ),
    )

    plan = rollback_tools.build_rollback_plan(task_id)

    assert plan["complete"] is False
    assert plan["blocker_count"] == 1
    assert plan["steps"][0]["detail"]["reason"] == "missing_rollback_evidence"


def test_ambiguous_or_unknown_rollback_actions_block_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_id = "task-ambiguous-rollback-evidence"
    created = tmp_path / "must-remain.txt"
    created.write_text("keep", encoding="utf-8")
    evidence = (
        {
            "trash_created_file": str(created),
            "backup": str(tmp_path / "unrelated.bak"),
        },
        {
            "operation": "app.excel.write_cell",
            "path": str(created),
            "previous_value": "before",
        },
    )
    for index, rollback_info in enumerate(evidence):
        call = ToolCall(
            id=f"call-ambiguous-{index}",
            task_id=task_id,
            step_id=f"step-ambiguous-{index}",
            tool_name="file.write_text" if index == 0 else "app.excel.write_cell",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="committed",
            dry_run=False,
            committed_at=f"2025-01-01T00:00:0{index}+00:00",
        )
        db.upsert_model("tool_calls", call)
        db.upsert_model(
            "tool_results",
            ToolResult(
                id=f"result-ambiguous-{index}",
                tool_call_id=call.id,
                ok=True,
                changed_paths=[str(created)],
                rollback_info=rollback_info,
                created_at=f"2025-01-01T00:00:1{index}+00:00",
            ),
        )
    monkeypatch.setattr(
        rollback_tools,
        "send2trash",
        lambda *_args, **_kwargs: pytest.fail("ambiguous evidence must not mutate the filesystem"),
    )

    plan = rollback_tools.build_rollback_plan(task_id)
    outcome = rollback_tools.execute_rollback(task_id)

    assert plan["complete"] is False
    assert plan["blocker_count"] == 2
    assert {step["detail"]["reason"] for step in plan["steps"]} == {"invalid_rollback_evidence"}
    messages = {step["detail"]["message"] for step in plan["steps"]}
    assert any("exactly one primary action" in message for message in messages)
    assert any("unsupported actions" in message for message in messages)
    assert outcome["state"] == "manual_required"
    assert outcome["manual_required"] == 2
    assert created.read_text(encoding="utf-8") == "keep"


def test_rollback_inventory_surfaces_missing_corrupt_and_unknown_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_id = "task-incomplete-inventory"
    known_file = tmp_path / "known-task-output.txt"
    known_file.write_text("keep until inventory is complete", encoding="utf-8")
    calls = (
        ToolCall(
            id="call-known-result",
            task_id=task_id,
            step_id="step-known",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="committed",
            committed_at="2025-01-01T00:00:00+00:00",
        ),
        ToolCall(
            id="call-missing-result",
            task_id=task_id,
            step_id="step-missing",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="committed",
            committed_at="2025-01-01T00:00:01+00:00",
        ),
        ToolCall(
            id="call-corrupt-result",
            task_id=task_id,
            step_id="step-corrupt",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="committed",
            committed_at="2025-01-01T00:00:02+00:00",
        ),
        ToolCall(
            id="call-unknown-result",
            task_id=task_id,
            step_id="step-unknown",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="outcome_unknown",
            outcome_unknown_at="2025-01-01T00:00:03+00:00",
        ),
        ToolCall(
            id="call-still-executing",
            task_id=task_id,
            step_id="step-executing",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="executing",
            started_at="2025-01-01T00:00:04+00:00",
        ),
        ToolCall(
            id="call-review-pending",
            task_id=task_id,
            step_id="step-review-pending",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="committed",
            committed_at="2025-01-01T00:00:05+00:00",
        ),
    )
    for call in calls:
        db.upsert_model("tool_calls", call)
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result-known",
            tool_call_id="call-known-result",
            ok=True,
            rollback_info=_rollback_info({"trash_created_file": str(known_file)}, known_file),
        ),
    )
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result-review-pending",
            tool_call_id="call-review-pending",
            ok=False,
            output={"review_pending": True},
            rollback_info={"trash_created_file": "review-pending.txt"},
        ),
    )
    db.upsert_model(
        "tool_results",
        ToolResult(
            id="result-unknown",
            tool_call_id="call-unknown-result",
            ok=False,
            rollback_info={"trash_created_file": "unknown.txt"},
        ),
    )
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO tool_results (id, tool_call_id, data, created_at) VALUES (?, ?, ?, ?)",
            ("result-corrupt", "call-corrupt-result", "{", "2025-01-01T00:00:02+00:00"),
        )
    monkeypatch.setattr(rollback_tools, "send2trash", lambda path: Path(path).unlink())

    plan = rollback_tools.build_rollback_plan(task_id)
    outcome = rollback_tools.execute_rollback(task_id)

    reasons = {step["detail"]["reason"] for step in plan["steps"] if step["actions"] == ["manual_review"]}
    assert reasons == {
        "missing_tool_result",
        "corrupt_tool_result",
        "outcome_unknown",
        "outcome_in_progress",
        "review_pending",
        "invalid_rollback_evidence",
    }
    assert plan["complete"] is False
    assert plan["blocker_count"] == 6
    assert outcome["state"] == "manual_required"
    assert outcome["manual_required"] == 6
    assert next(item for item in outcome["executed"] if item["tool_call_id"] == "call-known-result")["action"] == (
        "manual_review"
    )
    assert known_file.read_text(encoding="utf-8") == "keep until inventory is complete"


def test_rollback_inventory_is_not_truncated_after_500_calls() -> None:
    task_id = "task-large-rollback-inventory"
    for index in range(501):
        call = ToolCall(
            id=f"call-{index:04d}",
            task_id=task_id,
            step_id=f"step-{index:04d}",
            tool_name="file.write_text",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            status="committed",
            committed_at="2025-01-01T00:00:01+00:00",
        )
        db.upsert_model("tool_calls", call)
        db.upsert_model(
            "tool_results",
            ToolResult(
                id=f"result-{index:04d}",
                tool_call_id=call.id,
                ok=True,
                rollback_info={"trash_created_file": f"file-{index:04d}.txt"},
                created_at="2025-01-01T00:00:02+00:00",
            ),
        )

    plan = rollback_tools.build_rollback_plan(task_id)

    assert plan["count"] == 501
    assert plan["complete"] is False
    assert plan["blocker_count"] == 501
    assert {step["tool_call_id"] for step in plan["steps"]} == {f"call-{index:04d}" for index in range(501)}


def test_execute_rollback_uses_effective_authorized_directories(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-execute",
        ok=True,
        rollback_info=_rollback_info({"move_back": {"from": str(moved), "to": str(original)}}, moved, original),
    )
    monkeypatch.setattr(
        "app.tools.rollback_tools.load_rollback_snapshot",
        lambda _task_id: _snapshot("task-1", result),
    )

    class Settings:
        allowed_directories = [str(tmp_path)]

    monkeypatch.setattr("app.tools.rollback_tools.get_effective_settings", lambda: Settings())

    outcome = rollback_tools.execute_rollback("task-1")

    assert outcome["count"] == 1
    assert outcome["state"] == "succeeded"
    assert outcome["attempted"] == 1
    assert outcome["succeeded"] == 1
    assert outcome["verified"] == 1
    assert outcome["verification_failed"] == 0
    assert outcome["failed"] == 0
    assert outcome["executed"][0]["ok"] is True
    assert original.exists() and not moved.exists()


def test_execute_rollback_fails_closed_when_settings_context_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    original = tmp_path / "from.txt"
    moved = tmp_path / "to.txt"
    moved.write_text("hello", encoding="utf-8")
    result = ToolResult(
        tool_call_id="call-settings-fail",
        ok=True,
        rollback_info=_rollback_info({"move_back": {"from": str(moved), "to": str(original)}}, moved, original),
    )
    monkeypatch.setattr(
        "app.tools.rollback_tools.load_rollback_snapshot",
        lambda _task_id: _snapshot("task-settings-fail", result),
    )

    def fail_settings():
        raise Exception("settings unavailable")  # noqa: TRY002

    monkeypatch.setattr("app.tools.rollback_tools.get_effective_settings", fail_settings)

    outcome = rollback_tools.execute_rollback("task-settings-fail")

    assert outcome["count"] == 1
    assert outcome["state"] == "failed"
    assert outcome["succeeded"] == 0
    assert outcome["failed"] == 1
    assert outcome["executed"][0]["ok"] is False
    assert "No authorized directories configured" in outcome["executed"][0]["detail"]
    assert moved.exists()
    assert not original.exists()


def test_rollback_noop_when_no_info():
    result = ToolResult(tool_call_id="call-x", ok=True, rollback_info={})
    outcome = rollback_tools.rollback_tool_result(result)
    assert outcome["ok"] is True
    assert outcome["action"] == "noop"


@pytest.mark.parametrize(
    ("executed", "expected"),
    [
        ([{"ok": True}, {"ok": False}], {"state": "partial", "succeeded": 1, "failed": 1}),
        (
            [{"ok": False, "requires_user_action": True}],
            {"state": "manual_required", "manual_required": 1},
        ),
        (
            [{"ok": False, "action": "permanent_delete_unrecoverable"}],
            {"state": "unrecoverable", "unrecoverable": 1},
        ),
    ],
)
def test_summarize_rollback_classifies_non_success_outcomes(executed, expected):
    summary = rollback_tools._summarize_rollback(executed)

    assert summary.items() >= expected.items()
