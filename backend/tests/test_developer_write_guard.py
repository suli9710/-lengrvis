from __future__ import annotations

import subprocess
import sys

import pytest

from app.integrations.lengrvis_code import validate_allowed_tools
from app.orchestration.developer_write_guard import (
    extract_write_targets,
    git_worktree_diff_preview,
    infer_verification_command,
    run_write_verification,
    validate_write_paths,
)


def test_extract_write_targets_collects_unique_paths() -> None:
    events = [
        {"name": "Read", "input": {"file_path": "a.py"}},
        {"name": "Write", "input": {"file_path": "backend/app/foo.py"}},
        {"name": "Edit", "input": {"file_path": "backend/app/foo.py"}},
        {"name": "Edit", "input": {"file_path": "backend/tests/test_foo.py"}},
        {"name": "NotebookEdit", "input": {"notebook_path": "backend/notebooks/demo.ipynb"}},
    ]
    assert extract_write_targets(events) == [
        "backend/app/foo.py",
        "backend/tests/test_foo.py",
        "backend/notebooks/demo.ipynb",
    ]


def test_validate_allowed_tools_rejects_notebook_edit_in_readonly_mode() -> None:
    with pytest.raises(ValueError, match="NotebookEdit"):
        validate_allowed_tools(["Read", "NotebookEdit"], allow_write_tools=False)


def test_validate_write_paths_rejects_outside_workspace(tmp_path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    inside = workspace / "backend" / "app"
    inside.mkdir(parents=True)
    target = inside / "safe.py"
    target.write_text("ok", encoding="utf-8")

    ok = validate_write_paths(
        [str(target), str(tmp_path / "outside.py")],
        workspace=workspace,
        allowed_directories=[str(workspace)],
    )
    assert ok["ok"] is False
    assert len(ok["validated_paths"]) == 1
    assert ok["rejected_paths"][0]["path"].endswith("outside.py")


def test_infer_verification_command_prefers_changed_tests() -> None:
    command = infer_verification_command(
        "fix failing backend pytest around planner imports",
        ["backend/tests/test_planner.py", "backend/app/planner_agent.py"],
    )
    assert command is not None
    assert "pytest" in command
    assert "backend/tests/test_planner.py" in command


def test_git_worktree_diff_preview_reports_changed_files(tmp_path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=workspace, check=True, capture_output=True)
    target = workspace / "backend" / "app" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=workspace, check=True, capture_output=True)
    target.write_text("print('changed')\n", encoding="utf-8")

    preview = git_worktree_diff_preview(workspace, allowed_directories=[str(workspace)])

    assert preview["dry_run"] is True
    assert preview["changed_files"]
    assert preview["changed_files"][0]["path"].endswith("sample.py")


@pytest.mark.asyncio
async def test_developer_engine_applies_write_verification_on_success(tmp_path, monkeypatch) -> None:
    from app.config import AppSettings
    from app.orchestration.developer_engine import DeveloperExecutionEngine
    from app.orchestration.execution_engine import InMemoryRunStore
    from app.orchestration.execution_models import RunPhase
    from app.orchestration.lengrvis_code_config import LengrvisCodeConfig
    from app.orchestration.lengrvis_code_runner import LengrvisCodeStreamSummary

    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=workspace, check=True, capture_output=True)
    test_file = workspace / "backend" / "tests" / "test_sample.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "def test_always_passes():\n    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=workspace, check=True, capture_output=True)

    async def spy_run_lengrvis_code(  # noqa: ANN001, ARG001
        prompt, *, cwd, settings, config, run_id="", allow_write_tools=False
    ):
        assert allow_write_tools is True
        return LengrvisCodeStreamSummary(
            result={"is_error": False, "result": "patched test"},
            assistant_text=["done"],
            tool_events=[
                {"name": "Edit", "input": {"file_path": "backend/tests/test_sample.py"}},
            ],
        )

    monkeypatch.setattr("app.orchestration.developer_engine.run_lengrvis_code", spy_run_lengrvis_code)
    engine = DeveloperExecutionEngine(
        settings=AppSettings(
            allowed_directories=[str(workspace)],
            api_key="test-api-key",
            developer_writes_enabled=True,
            developer_writes_require_verification=True,
            data_dir=str(tmp_path / "data"),
        ),
        store=InMemoryRunStore(),
        lengrvis_code_config=LengrvisCodeConfig(command=(sys.executable, "-c", "print('noop')"), max_turns=1),
        use_lengrvis_code=True,
    )
    state = await engine.start_run("fix failing backend pytest in backend/tests", "efficiency", "developer")
    awaiting = await engine.run_turn(state)
    assert awaiting.state.phase == RunPhase.AWAITING_APPROVAL

    from app.core import db
    from app.core.schemas import Approval
    from app.services.mobile_pairing_service import approve_approval

    approval = Approval.model_validate(db.fetch_many("approvals", "task_id = ?", (state.task_id,), limit=1)[0])
    approve_approval(approval.id)
    result = await engine.run_turn(awaiting.state)

    assert result.state.phase == RunPhase.COMPLETED
    assert "write_verification" in result.outputs
    assert result.outputs["write_verification"]["verification_command"] is not None
    assert result.state.current_plan["write_verification"]["ok"] is True


@pytest.mark.asyncio
async def test_developer_engine_permission_denial_enters_awaiting_approval(tmp_path, monkeypatch) -> None:
    from app.config import AppSettings
    from app.orchestration.developer_engine import DeveloperExecutionEngine
    from app.orchestration.execution_engine import InMemoryRunStore
    from app.orchestration.execution_models import RunPhase
    from app.orchestration.lengrvis_code_config import LengrvisCodeConfig
    from app.orchestration.lengrvis_code_runner import LengrvisCodeStreamSummary

    async def spy_run_lengrvis_code(  # noqa: ANN001, ARG001
        prompt, *, cwd, settings, config, run_id="", allow_write_tools=False
    ):
        assert allow_write_tools is True
        return LengrvisCodeStreamSummary(
            result={
                "is_error": False,
                "result": "Write blocked pending approval",
                "permission_denials": [
                    {"tool_name": "Write", "reason": "default permission mode requires user approval"}
                ],
            },
            assistant_text=["blocked"],
            tool_events=[{"name": "Write", "input": {"file_path": "backend/app/sample.py"}}],
        )

    monkeypatch.setattr("app.orchestration.developer_engine.run_lengrvis_code", spy_run_lengrvis_code)
    engine = DeveloperExecutionEngine(
        settings=AppSettings(
            allowed_directories=[str(tmp_path)],
            api_key="test-api-key",
            developer_writes_enabled=True,
        ),
        store=InMemoryRunStore(),
        lengrvis_code_config=LengrvisCodeConfig(command=(sys.executable, "-c", "print('noop')"), max_turns=1),
        use_lengrvis_code=True,
    )
    state = await engine.start_run("fix failing pytest", "efficiency", "developer")
    result = await engine.run_turn(state)

    assert result.state.phase == RunPhase.AWAITING_APPROVAL
    assert result.outputs["lengrvis_code"]["awaiting_write_approval"] is True
    assert result.state.current_plan["pending_write_approvals"]


def test_run_write_verification_without_writes_is_ok(tmp_path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    payload = run_write_verification(
        workspace=workspace,
        allowed_directories=[str(workspace)],
        goal="inspect repository",
        tool_events=[{"name": "Read", "input": {"file_path": "README.md"}}],
        require_verification=True,
        data_dir=tmp_path / "data",
    )
    assert payload["ok"] is True
    assert payload["writes_detected"] is False
