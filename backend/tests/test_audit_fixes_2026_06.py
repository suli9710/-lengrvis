"""Regression tests for the 2026-06 cross-audit fixes.

Each test pins a specific business-flow/security bug that was confirmed by the
audit so it cannot silently regress. Tests are Linux-safe (no Windows-only
recycle-bin / screen-capture behavior).
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.agents.supervisor_agent as supervisor_module
from app.agents.supervisor_agent import SupervisorAgent
from app.core import db
from app.core.paths import SYSTEM_ROOTS
from app.core.schemas import RunEngine, Task
from app.main import create_app
from app.orchestration.task_phase import TaskPhase
from app.security.cors import cors_allow_origins
from app.security.desktop_api import assert_no_production_test_escape_hatches, desktop_api_token_optional_for_test
from app.services import run_service, run_service_background
from app.services.task_service import handle_chat


class _StubProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def structured_chat(self, messages, output_schema):
        self.calls += 1
        return self.payload


@pytest.fixture(autouse=True)
def _no_local_backend(monkeypatch):
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)


# --- SEC: path classification parity (C:/ProgramData) ---------------------


def test_programdata_is_a_protected_system_root():
    # Pin the path-classification parity fix (paths.SYSTEM_ROOTS must match the
    # policy engine's SYSTEM_PATH_PREFIXES, which already lists ProgramData).
    # Asserted via membership because Path("C:/...").resolve() is cwd-relative on
    # non-Windows and cannot exercise is_system_path here.
    assert Path("C:/ProgramData") in SYSTEM_ROOTS


# --- SEC: desktop token-optional escape hatch must not leak via APP_ENV ----


def test_desktop_token_optional_ignored_for_app_env_testing(monkeypatch):
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", "1")
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("LENGRVIS_ENV", "testing")
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    # Only the dedicated test signals may unlock the escape hatch; a staging box
    # with APP_ENV=testing must keep the desktop token guard ON.
    assert desktop_api_token_optional_for_test() is False


def test_desktop_token_optional_still_honored_for_lengrvis_test(monkeypatch):
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", "1")
    monkeypatch.setenv("LENGRVIS_TEST", "1")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert desktop_api_token_optional_for_test() is True


def test_production_environment_refuses_test_escape_hatches(monkeypatch):
    monkeypatch.setenv("LENGRVIS_ENV", "production")
    monkeypatch.setenv("LENGRVIS_TEST", "1")

    with pytest.raises(RuntimeError, match="Refusing to start production backend"):
        assert_no_production_test_escape_hatches()


def test_production_environment_without_escape_hatches_can_start(monkeypatch):
    monkeypatch.setenv("LENGRVIS_ENV", "production")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("LENGRVIS_TEST", "0")
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", "false")
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "0")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    assert_no_production_test_escape_hatches()


def test_desktop_token_guard_allows_cors_preflight_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    db.init_db()
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    response = client.options(
        "/api/chat",
        headers={
            "origin": "http://localhost:5173",
            "access-control-request-method": "POST",
            "access-control-request-headers": "X-Lengrvis-Desktop-Token, Content-Type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_production_cors_excludes_vite_dev_origins(monkeypatch):
    monkeypatch.setenv("LENGRVIS_ENV", "production")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    origins = cors_allow_origins()

    assert origins == ["app://local"]
    assert "http://localhost:5173" not in origins
    assert "http://127.0.0.1:5173" not in origins


# --- LIFECYCLE: duplicate engine loop guard -------------------------------


def test_track_active_run_if_idle_rejects_second_owner():
    run_id = "audit_idle_track_run"
    first = concurrent.futures.Future()
    second = concurrent.futures.Future()
    try:
        assert run_service_background.track_active_run_if_idle(run_id, first) is True
        # A still-running owner blocks a duplicate loop from claiming the slot.
        assert run_service_background.track_active_run_if_idle(run_id, second) is False
        # Once the first owner finishes, the slot is reclaimable.
        first.set_result(None)
        third = concurrent.futures.Future()
        assert run_service_background.track_active_run_if_idle(run_id, third) is True
        third.set_result(None)
    finally:
        run_service_background.untrack_active_run(run_id)
        if not first.done():
            first.set_result(None)
        if not second.done():
            second.set_result(None)


# --- LIFECYCLE: PAUSED run must still reflect terminal task outcome --------


def _make_paused_run(task: Task, run_id: str) -> run_service.Run:
    return run_service.Run(
        id=run_id,
        message=task.user_goal,
        mode=task.mode,
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.PAUSED,
        task_id=task.id,
        state={
            "run_id": run_id,
            "engine": "os",
            "phase": "paused",
            "goal": task.user_goal,
            "mode": task.mode,
            "task_id": task.id,
        },
    )


def test_paused_run_syncs_terminal_task_completion(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    task = Task(user_goal="finished while paused", mode="efficiency", status=TaskPhase.COMPLETED)
    task.final_summary = "all done"
    db.upsert_model("tasks", task)
    run = _make_paused_run(task, "osrun_paused_then_complete")
    db.upsert_model("runs", run)

    synced = run_service.get_run(run.id)

    assert synced.phase == run_service.RunPhase.COMPLETED


def test_paused_run_stays_paused_when_task_not_terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    task = Task(user_goal="still working while paused", mode="efficiency", status=TaskPhase.EXECUTION)
    db.upsert_model("tasks", task)
    run = _make_paused_run(task, "osrun_paused_still_running")
    db.upsert_model("runs", run)

    synced = run_service.get_run(run.id)

    assert synced.phase == run_service.RunPhase.PAUSED


# --- CHAT: supervisor downgrade must not echo delegation-flavored text -----


@pytest.mark.anyio
async def test_supervisor_downgrade_without_hint_returns_chat_reply(monkeypatch):
    provider = _StubProvider({"delegate": True, "reply": "我会把这件事交给文件 Agent 处理。", "agent_hint": ""})
    monkeypatch.setattr(supervisor_module, "get_provider", lambda: provider)

    decision = await SupervisorAgent().decide("你是什么模型", "efficiency")

    assert decision.delegate is False
    assert decision.agent_hint == ""
    # The misleading "交给...Agent" text must not survive the downgrade.
    assert "Agent" not in decision.reply or "交给" not in decision.reply


# --- CHAT: explicit destructive path overrides a wrong (valid) delegation --


@pytest.mark.anyio
async def test_explicit_path_delete_overrides_wrong_agent_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    # The model delegates the explicit file deletion to the WRONG (but valid)
    # worker; the deterministic safety override must redirect to FileAgent.
    provider = _StubProvider({"delegate": True, "reply": "交给电脑 Agent", "agent_hint": "ComputerAgent"})
    monkeypatch.setattr(supervisor_module, "get_provider", lambda: provider)

    response = await handle_chat(r"删除 C:\Temp\old.txt", "efficiency")

    assert response.delegated is True
    assert response.agent == "FileAgent"


# --- CHAT: failed/paused diagnostics run must not be aliased as a task -----


@pytest.mark.anyio
async def test_system_diagnostics_without_task_is_not_delegated(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()

    async def _failed_create_run(message, mode, requested_engine, *, agent_hint=None, task_metadata=None):
        return run_service.Run(
            message=message,
            mode=mode,
            requested_engine=RunEngine.AUTO,
            engine=RunEngine.OS,
            phase=run_service.RunPhase.FAILED,
            error="boom",
        )

    monkeypatch.setattr(run_service, "create_run", _failed_create_run)

    response = await handle_chat("帮我检查这台电脑的磁盘和内存", "efficiency")

    assert response.delegated is False
    assert response.task_id is None
    # No run id may be aliased as a task id (clients would 404 on /api/tasks/{id}).
