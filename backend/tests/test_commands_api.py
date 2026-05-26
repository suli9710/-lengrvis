from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.session_context import SessionContextStore, reset_session_context_store
from app.core.schemas import Task
from app.main import create_app
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.state_machine import safe_transition
from app.orchestration.task_phase import TaskPhase


@pytest.fixture(autouse=True)
def _isolate_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(workspace))
    reset_session_context_store()
    db.init_db()
    yield
    reset_session_context_store()


def _commands_payload(response_json: Any) -> list[dict[str, Any]]:
    if isinstance(response_json, dict):
        commands = response_json.get("commands")
    else:
        commands = response_json
    assert isinstance(commands, list)
    assert all(isinstance(command, dict) for command in commands)
    return commands


def _xfail_if_commands_api_missing(response) -> None:  # noqa: ANN001
    if response.status_code == 404 and "not-a-command" not in str(response.request.url):
        pytest.xfail("Commands API is not implemented yet; contract expects /api/commands endpoints.")


def test_commands_list_endpoint_exposes_shared_slash_command_registry() -> None:
    client = TestClient(create_app())

    response = client.get("/api/commands")

    _xfail_if_commands_api_missing(response)
    assert response.status_code == 200
    commands = _commands_payload(response.json())
    by_name = {command.get("name"): command for command in commands}
    expected = {"/permissions", "/mcp", "/compact", "/resume", "/summary", "/skills", "/workflows", "/review", "/voice"}

    assert expected <= set(by_name)
    for name in expected:
        command = by_name[name]
        assert command["name"].startswith("/")
        assert isinstance(command["description"], str) and command["description"]
        assert isinstance(command["category"], str) and command["category"]
        assert isinstance(command["input_schema"], dict)
        assert command.get("surface") in {"shared", "desktop", "mobile", "api"}


def test_commands_execute_endpoint_runs_permissions_list_contract() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/commands/execute",
        json={"command": "/permissions", "args": {"action": "list"}, "surface": "desktop"},
    )

    _xfail_if_commands_api_missing(response)
    assert response.status_code == 200
    payload = response.json()

    assert payload["ok"] is True
    assert payload["command"] == "/permissions"
    assert isinstance(payload["result"], dict)
    assert "policy" in payload["result"] or "rules" in payload["result"]
    assert payload.get("surface") in {"shared", "desktop", "api"}


def test_commands_execute_endpoint_returns_structured_unknown_command_error() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/commands/execute",
        json={"command": "/not-a-command", "args": {}, "surface": "desktop"},
    )

    assert response.status_code in {400, 404, 422}
    payload = response.json()
    text = str(payload).lower()

    assert "unknown" in text or "not-a-command" in text
    assert "command" in text


def test_resume_command_uses_task_resume_service(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app())
    task = Task(user_goal="resume me", mode="efficiency", status=TaskPhase.EXECUTION, execution_stage=ExecutionStage.PAUSED)
    db.upsert_model("tasks", task)
    calls: list[str] = []

    def fake_resume_task(task_id: str) -> Task:
        calls.append(task_id)
        return safe_transition(task, "executing_step", actor="Test")

    monkeypatch.setattr("app.commands.service.resume_task", fake_resume_task)

    response = client.post(
        "/api/commands/execute",
        json={"command": "/resume", "args": {"task_id": task.id}, "surface": "desktop"},
    )

    assert response.status_code == 200
    assert calls == [task.id]
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"]["task"]["id"] == task.id
    assert payload["result"]["task"]["execution_stage"] == "step_running"


def test_summary_command_returns_session_compact_metadata() -> None:
    client = TestClient(create_app())
    store = SessionContextStore(session_id="session_summary_command")
    store.load()
    store.remember_summary(
        "Earlier compacted work.",
        last_message_id="msg_42",
        token_stats={"compact_metadata": {"compaction_strategy": "manual_compact"}},
    )

    response = client.post(
        "/api/commands/execute",
        json={"command": "/summary", "args": {"session_id": "session_summary_command"}, "surface": "desktop"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["command"] == "/summary"
    assert payload["result"]["session_id"] == "session_summary_command"
    assert payload["result"]["summary"] == "Earlier compacted work."
    assert payload["result"]["compact_metadata"]["compaction_strategy"] == "manual_compact"
    assert payload["result"]["compacted_context"]["metadata"]["last_summarized_message_id"] == "msg_42"


def test_resume_command_loads_compacted_context_by_session_id() -> None:
    client = TestClient(create_app())
    store = SessionContextStore(session_id="session_resume_command")
    store.load()
    store.remember_summary(
        "Resume from this compacted context.",
        last_message_id="msg_99",
        token_stats={"compact_metadata": {"summary_chars": 35}},
    )

    response = client.post(
        "/api/commands/execute",
        json={"command": "/resume", "args": {"session_id": "session_resume_command"}, "surface": "desktop"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["command"] == "/resume"
    assert payload["result"]["session_id"] == "session_resume_command"
    assert payload["result"]["has_compacted_context"] is True
    assert payload["result"]["compacted_context"]["content"] == "Resume from this compacted context."
    assert payload["result"]["compacted_context"]["metadata"]["compact_metadata"]["summary_chars"] == 35


def test_resume_command_without_args_still_lists_resumable_tasks() -> None:
    client = TestClient(create_app())
    task = Task(user_goal="resume list", mode="efficiency", status=TaskPhase.EXECUTION, execution_stage=ExecutionStage.PAUSED)
    db.upsert_model("tasks", task)

    response = client.post(
        "/api/commands/execute",
        json={"command": "/resume", "args": {}, "surface": "desktop"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"]["count"] >= 1
    assert any(item["id"] == task.id for item in payload["result"]["resumable_tasks"])


def test_summary_command_updates_without_erasing_session_memory() -> None:
    client = TestClient(create_app())
    store = SessionContextStore(session_id="session_summary_update")
    store.load()
    store.remember_task("task_existing", workflow_state={"phase": "draft"})
    store.learn_preference("editor", "WPS")

    response = client.post(
        "/api/commands/execute",
        json={
            "command": "/summary",
            "args": {
                "session_id": "session_summary_update",
                "messages": [{"id": "msg_new", "role": "user", "content": "new summary content"}],
            },
            "surface": "desktop",
        },
    )

    assert response.status_code == 200
    reloaded = SessionContextStore(session_id="session_summary_update").load()
    assert reloaded.current_workflow_state["phase"] == "draft"
    assert reloaded.learned_preferences["editor"] == "WPS"
    assert reloaded.active_task_ids == ["task_existing"]
    assert reloaded.last_summarized_message_id == "msg_new"
