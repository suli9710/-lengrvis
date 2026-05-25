from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.agents.planner_agent as planner_module
import app.agents.supervisor_agent as supervisor_module
from app.core import db
from app.main import app
from app.services.task_service import handle_chat


class RecordingSupervisorProvider:
    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self.payload = payload or {
            "delegate": False,
            "reply": "model supervisor reply",
            "agent_hint": "",
        }
        self.error = error
        self.calls = 0

    async def structured_chat(self, messages, output_schema):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


class RecordingPlanProvider(RecordingSupervisorProvider):
    async def structured_chat(self, messages, output_schema):
        self.calls += 1
        return {
            "goal": "inspect computer",
            "steps": [
                {
                    "agent_name": "ComputerAgent",
                    "tool_name": "system.get_info",
                    "description": "Read system info",
                    "args": {},
                    "risk_level": "R0_READ_ONLY",
                }
            ],
        }


@pytest.fixture(autouse=True)
def _no_local_backend(monkeypatch):
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)


@pytest.mark.anyio
async def test_chat_only_turn_returns_supervisor_feedback_without_task(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()

    response = await handle_chat("agent how do you work", "privacy")

    assert response.delegated is False
    assert response.task_id is None
    assert response.status is None
    assert response.message
    assert db.fetch_many("tasks") == []
    assert len(db.fetch_many("chat_messages")) == 2


@pytest.mark.anyio
async def test_supervisor_calls_provider_even_for_chat_only_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider()
    monkeypatch.setattr(supervisor_module, "get_provider", lambda: provider)

    response = await handle_chat("agent how do you work", "privacy")

    assert provider.calls == 1
    assert response.delegated is False
    assert response.message == "model supervisor reply"
    assert db.fetch_many("tasks") == []


@pytest.mark.anyio
async def test_supervisor_uses_heuristic_when_provider_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(error=TimeoutError("provider unavailable"))
    monkeypatch.setattr(supervisor_module, "get_provider", lambda: provider)

    response = await handle_chat(r"open C:\Temp\report.txt", "privacy")

    await asyncio.sleep(0.1)
    assert provider.calls == 1
    assert response.delegated is True
    assert response.task_id
    assert response.agent == "FileAgent"


@pytest.mark.anyio
async def test_executable_turn_without_local_backend_fails_clearly(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()

    response = await handle_chat(r"open C:\Temp\report.txt", "privacy")

    assert response.delegated is True
    assert response.task_id
    assert response.status == "planning"
    assert response.agent == "FileAgent"
    assert len(db.fetch_many("tasks")) == 1
    assert len(db.fetch_many("chat_messages")) == 2
    await asyncio.sleep(0.1)
    task = db.fetch_one("tasks", response.task_id)
    assert task["status"] == "failed"
    assert "Privacy mode requires a reachable local LLM backend" in task["final_summary"]


@pytest.mark.anyio
async def test_executable_turn_uses_local_provider_when_available(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()
    provider = RecordingPlanProvider()
    monkeypatch.setattr(planner_module, "get_provider", lambda: provider)

    response = await handle_chat(r"open C:\Temp\report.txt", "privacy")

    await asyncio.sleep(0.1)
    assert response.delegated is True
    assert response.task_id
    assert provider.calls == 1
    assert db.fetch_one("tasks", response.task_id)["status"] == "completed"


@pytest.mark.anyio
async def test_privacy_provider_runtime_failure_does_not_fallback_to_mock(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()
    provider = RecordingSupervisorProvider(error=TimeoutError("local provider timeout"))
    monkeypatch.setattr(planner_module, "get_provider", lambda: provider)

    response = await handle_chat(r"open C:\Temp\report.txt", "privacy")

    await asyncio.sleep(0.1)
    task = db.fetch_one("tasks", response.task_id)
    assert provider.calls == 1
    assert task["status"] == "failed"
    assert "local provider timeout" in task["final_summary"]


@pytest.mark.anyio
async def test_file_delete_turn_returns_immediate_file_agent_feedback(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(error=AssertionError("provider should not block clear execution requests"))
    monkeypatch.setattr(supervisor_module, "get_provider", lambda: provider)

    response = await handle_chat(r"delete C:\Temp\old-folder", "privacy")

    await asyncio.sleep(0.1)
    assert provider.calls == 1
    assert response.delegated is True
    assert response.agent == "FileAgent"
    assert response.status == "planning"
    assert response.message


@pytest.mark.anyio
async def test_windows_path_delete_delegates_to_file_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(error=AssertionError("provider should not block clear execution requests"))
    monkeypatch.setattr(supervisor_module, "get_provider", lambda: provider)

    response = await handle_chat(r"delete C:\Users\Suli\Desktop\old-folder", "privacy")

    await asyncio.sleep(0.1)
    assert response.delegated is True
    assert response.agent == "FileAgent"
    assert response.status == "planning"


@pytest.mark.anyio
async def test_uninstall_app_turn_delegates_to_app_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(error=AssertionError("provider should not block clear uninstall requests"))
    monkeypatch.setattr(supervisor_module, "get_provider", lambda: provider)

    response = await handle_chat("uninstall bean app", "privacy")

    await asyncio.sleep(0.1)
    assert provider.calls == 1
    assert response.delegated is True
    assert response.agent == "AppAgent"
    assert response.status == "planning"
    assert response.message


@pytest.mark.anyio
async def test_file_delete_path_creates_trash_approval(monkeypatch, tmp_path):
    target = tmp_path / "workspace" / "old-folder"
    target.mkdir(parents=True)
    (target / "note.txt").write_text("remove me\n", encoding="utf-8")
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(target.parent))
    db.init_db()

    response = await handle_chat(f"delete {target}", "privacy")

    await asyncio.sleep(0.2)
    task = db.fetch_one("tasks", response.task_id)
    approvals = db.fetch_many("approvals", "task_id = ?", (response.task_id,), limit=10)
    plans = db.fetch_many("plans", "task_id = ?", (response.task_id,), limit=1)

    assert task["status"] == "waiting_user_approval"
    assert approvals
    assert approvals[0]["diff_preview"]["diff_preview"][0]["action"] == "trash"
    assert Path(approvals[0]["diff_preview"]["diff_preview"][0]["path"]) == target
    assert plans[0]["steps"][0]["tool_name"] == "file.trash"


def test_approval_executes_trash_step_after_user_approval(monkeypatch, tmp_path):
    target = tmp_path / "workspace" / "old-folder"
    target.mkdir(parents=True)
    (target / "note.txt").write_text("remove me\n", encoding="utf-8")
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(target.parent))
    db.init_db()

    client = TestClient(app)
    chat_response = client.post(
        "/api/chat",
        json={"message": f"delete {target}", "mode": "privacy"},
    )
    assert chat_response.status_code == 200

    task_id = chat_response.json()["task_id"]
    approval = _wait_for_pending_approval(task_id)
    approve_response = client.post(f"/api/approvals/{approval['id']}/approve")

    assert approve_response.status_code == 200
    task = db.fetch_one("tasks", task_id)
    assert task["status"] == "completed"
    assert not target.exists()
    results = db.fetch_many("tool_results", limit=10)
    assert any(str(target) in result.get("changed_paths", []) for result in results)


def test_explicit_path_trash_can_run_without_global_authorized_directory(monkeypatch, tmp_path):
    target = tmp_path / "workspace" / "old-folder"
    target.mkdir(parents=True)
    (target / "note.txt").write_text("remove me\n", encoding="utf-8")
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", "")
    db.init_db()

    client = TestClient(app)
    chat_response = client.post(
        "/api/chat",
        json={"message": f"delete {target}", "mode": "privacy"},
    )
    assert chat_response.status_code == 200

    task_id = chat_response.json()["task_id"]
    approval = _wait_for_pending_approval(task_id)
    assert Path(approval["diff_preview"]["diff_preview"][0]["path"]) == target
    approve_response = client.post(f"/api/approvals/{approval['id']}/approve")

    assert approve_response.status_code == 200
    assert db.fetch_one("tasks", task_id)["status"] == "completed"
    assert not target.exists()


@pytest.mark.anyio
async def test_domain_mention_without_action_stays_conversational(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()

    response = await handle_chat("computer agent should explain responsibilities", "privacy")

    assert response.delegated is False
    assert response.task_id is None
    assert db.fetch_many("tasks") == []


def _wait_for_pending_approval(task_id: str, attempts: int = 20) -> dict:
    for _ in range(attempts):
        approvals = db.fetch_many("approvals", "task_id = ? AND status = ?", (task_id, "pending"), limit=10)
        if approvals:
            return approvals[0]
        import time

        time.sleep(0.05)
    raise AssertionError("Expected pending approval.")
