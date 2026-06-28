from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx
import pytest
from native_confirmation_helpers import native_confirmation_headers

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
            "goal": "open file",
            "steps": [
                {
                    "agent_name": "FileAgent",
                    "tool_name": "file.search_by_name",
                    "description": "Locate the requested file by name",
                    "args": {"query": "report.txt", "dry_run": True},
                    "risk_level": "R0_READ_ONLY",
                }
            ],
        }


def _fake_send2trash(path: str) -> None:
    target = Path(path)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _no_local_backend(monkeypatch):
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)


@pytest.mark.anyio
async def test_chat_only_turn_returns_supervisor_feedback_without_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    db.init_db()

    response = await handle_chat("agent how do you work", "privacy")

    assert response.delegated is False
    assert response.task_id is None
    assert response.status is None
    assert response.message
    assert db.fetch_many("tasks") == []
    assert len(db.fetch_many("chat_messages")) == 2


@pytest.mark.anyio
async def test_complaint_about_chatting_gets_natural_chat_reply(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    db.init_db()

    response = await handle_chat("你怎么不和我聊天", "efficiency")

    assert response.delegated is False
    assert response.task_id is None
    assert "可以聊天" in response.message or "当然" in response.message
    assert "确认意图" not in response.message


@pytest.mark.anyio
async def test_supervisor_calls_provider_even_for_chat_only_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider()
    monkeypatch.setattr(supervisor_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat("agent how do you work", "privacy")

    assert provider.calls == 1
    assert response.delegated is False
    assert response.message == "model supervisor reply"
    assert db.fetch_many("tasks") == []


@pytest.mark.anyio
async def test_provider_delegation_can_start_task_without_frontend_run(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(
        {
            "delegate": True,
            "reply": "我会先交给文件 Agent 生成清理预览，确认安全后再继续。",
            "agent_hint": "FileAgent",
        }
    )
    monkeypatch.setattr(supervisor_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat("帮我看看 d 盘哪些文件可以清理", "efficiency")

    assert provider.calls == 1
    assert response.delegated is True
    assert response.agent == "FileAgent"
    assert response.task_id
    assert response.message == "我会先交给文件 Agent 生成清理预览，确认安全后再继续。"
    assert len(db.fetch_many("tasks")) == 1


@pytest.mark.anyio
async def test_provider_chat_decision_is_not_overridden_by_keyword_heuristic(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(
        {
            "delegate": False,
            "reply": "我是 Lengrvis 的主管 Agent，会先和你自然对话，再按需要调其他 Agent。",
            "agent_hint": "",
        }
    )
    monkeypatch.setattr(supervisor_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat("你是什么模型", "efficiency")

    assert provider.calls == 1
    assert response.delegated is False
    assert response.task_id is None
    assert response.message.startswith("我是 Lengrvis")
    assert db.fetch_many("tasks") == []


@pytest.mark.anyio
async def test_short_conversation_uses_natural_fallback_when_model_is_unhelpful(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(
        {
            "delegate": False,
            "reply": "我没看懂你的意思，可以再具体说一下你想让我做什么吗？",
            "agent_hint": "",
        }
    )
    monkeypatch.setattr(supervisor_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat("你会啊", "efficiency")

    assert response.delegated is False
    assert response.task_id is None
    assert "会啊" in response.message
    assert "没看懂" not in response.message
    assert db.fetch_many("tasks") == []


@pytest.mark.anyio
async def test_identity_chat_stays_conversational(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(
        {
            "delegate": False,
            "reply": "请具体说明你的任务。",
            "agent_hint": "",
        }
    )
    monkeypatch.setattr(supervisor_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat("你是真人吗", "efficiency")

    assert response.delegated is False
    assert response.task_id is None
    assert "不是真人" in response.message
    assert "请具体" not in response.message
    assert db.fetch_many("tasks") == []


@pytest.mark.anyio
async def test_supervisor_uses_heuristic_when_provider_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(error=TimeoutError("provider unavailable"))
    monkeypatch.setattr(supervisor_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat(r"open C:\Temp\report.txt", "privacy")

    await asyncio.sleep(0.1)
    assert provider.calls == 1
    assert response.delegated is True
    assert response.task_id
    assert response.agent == "FileAgent"


@pytest.mark.anyio
async def test_executable_turn_without_local_backend_fails_clearly(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
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
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    db.init_db()
    provider = RecordingPlanProvider()
    monkeypatch.setattr(planner_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat(r"open C:\Temp\report.txt", "privacy")

    assert response.delegated is True
    assert response.task_id
    task = await _await_task_status(response.task_id, "completed")
    assert 1 <= provider.calls <= 2
    assert task["status"] == "completed"


@pytest.mark.anyio
async def test_privacy_provider_runtime_failure_does_not_fallback_to_mock(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    db.init_db()
    provider = RecordingSupervisorProvider(error=TimeoutError("local provider timeout"))
    monkeypatch.setattr(planner_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat(r"open C:\Temp\report.txt", "privacy")

    await asyncio.sleep(0.1)
    task = db.fetch_one("tasks", response.task_id)
    assert provider.calls == 1
    assert task["status"] == "failed"
    assert "local provider timeout" in task["final_summary"]


@pytest.mark.anyio
async def test_file_delete_turn_returns_immediate_file_agent_feedback(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(error=AssertionError("provider should not block clear execution requests"))
    monkeypatch.setattr(supervisor_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat(r"delete C:\Temp\old-folder", "privacy")

    await asyncio.sleep(0.1)
    assert provider.calls == 1
    assert response.delegated is True
    assert response.agent == "FileAgent"
    assert response.status == "planning"
    assert response.message


@pytest.mark.anyio
async def test_windows_path_delete_delegates_to_file_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(error=AssertionError("provider should not block clear execution requests"))
    monkeypatch.setattr(supervisor_module, "get_provider", lambda *args, **kwargs: provider)

    response = await handle_chat(r"delete C:\Users\Suli\Desktop\old-folder", "privacy")

    await asyncio.sleep(0.1)
    assert response.delegated is True
    assert response.agent == "FileAgent"
    assert response.status == "planning"


@pytest.mark.anyio
async def test_uninstall_app_turn_delegates_to_app_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    provider = RecordingSupervisorProvider(error=AssertionError("provider should not block clear uninstall requests"))
    monkeypatch.setattr(supervisor_module, "get_provider", lambda *args, **kwargs: provider)

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
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(target.parent))
    db.init_db()

    response = await handle_chat(f"delete {target}", "privacy")

    # Tool execution hops through asyncio.to_thread; poll instead of a fixed sleep.
    approvals: list[dict] = []
    for _ in range(40):
        await asyncio.sleep(0.1)
        approvals = db.fetch_many("approvals", "task_id = ?", (response.task_id,), limit=10)
        if approvals:
            break
    task = db.fetch_one("tasks", response.task_id)
    plans = db.fetch_many("plans", "task_id = ?", (response.task_id,), limit=1)

    assert task["status"] == "execution"
    assert task["execution_stage"] == "awaiting_approval"
    assert approvals
    assert approvals[0]["diff_preview"]["diff_preview"][0]["action"] == "trash"
    assert Path(approvals[0]["diff_preview"]["diff_preview"][0]["path"]) == target
    assert plans[0]["steps"][0]["tool_name"] == "file.trash"


@pytest.mark.anyio
async def test_approval_executes_trash_step_after_user_approval(monkeypatch, tmp_path):
    target = tmp_path / "workspace" / "old-folder"
    target.mkdir(parents=True)
    (target / "note.txt").write_text("remove me\n", encoding="utf-8")
    monkeypatch.setattr("app.tools.file_tools.send2trash", _fake_send2trash)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(target.parent))
    db.init_db()

    # ASGITransport keeps everything on this test's event loop so the
    # background task (which hops through asyncio.to_thread) can progress to
    # the approval checkpoint while we poll with asyncio.sleep.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        chat_response = await client.post(
            "/api/chat",
            json={"message": f"delete {target}", "mode": "privacy"},
        )
        assert chat_response.status_code == 200

        task_id = chat_response.json()["task_id"]
        approval = await _await_pending_approval(task_id)
        approve_response = await client.post(
            f"/api/approvals/{approval['id']}/approve",
            headers=native_confirmation_headers("approve", approval["id"]),
        )
        assert approve_response.status_code == 200
        task = await _await_task_status(task_id, "completed")

    assert task["status"] == "completed"
    assert not target.exists()
    results = db.fetch_many("tool_results", limit=10)
    assert any(str(target) in result.get("changed_paths", []) for result in results)


@pytest.mark.anyio
async def test_explicit_path_trash_can_run_without_global_authorized_directory(monkeypatch, tmp_path):
    target = tmp_path / "workspace" / "old-folder"
    target.mkdir(parents=True)
    (target / "note.txt").write_text("remove me\n", encoding="utf-8")
    monkeypatch.setattr("app.tools.file_tools.send2trash", _fake_send2trash)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", "")
    db.init_db()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        chat_response = await client.post(
            "/api/chat",
            json={"message": f"delete {target}", "mode": "privacy"},
        )
        assert chat_response.status_code == 200

        task_id = chat_response.json()["task_id"]
        approval = await _await_pending_approval(task_id)
        assert Path(approval["diff_preview"]["diff_preview"][0]["path"]) == target
        approve_response = await client.post(
            f"/api/approvals/{approval['id']}/approve",
            headers=native_confirmation_headers("approve", approval["id"]),
        )
        assert approve_response.status_code == 200
        task = await _await_task_status(task_id, "completed")

    assert task["status"] == "completed"
    assert not target.exists()


@pytest.mark.anyio
async def test_domain_mention_without_action_stays_conversational(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    db.init_db()

    response = await handle_chat("computer agent should explain responsibilities", "privacy")

    assert response.delegated is False
    assert response.task_id is None
    assert db.fetch_many("tasks") == []


async def _await_pending_approval(task_id: str, attempts: int = 100) -> dict:
    for _ in range(attempts):
        approvals = db.fetch_many("approvals", "task_id = ? AND status = ?", (task_id, "pending"), limit=10)
        if approvals:
            return approvals[0]
        await asyncio.sleep(0.05)
    raise AssertionError("Expected pending approval.")


async def _await_task_status(task_id: str, status: str, attempts: int = 100) -> dict:
    task: dict = {}
    for _ in range(attempts):
        task = db.fetch_one("tasks", task_id) or {}
        if task.get("status") == status:
            return task
        await asyncio.sleep(0.05)
    return task
