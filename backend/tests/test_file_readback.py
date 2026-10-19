from __future__ import annotations

import hashlib

import pytest

from app.tools import file_tools
from app.tools.registry import ToolRegistry


@pytest.mark.parametrize("bom", [False, True])
@pytest.mark.parametrize("expected", ["", "跨应用 😀\r\n第二行\r\n"])
def test_saved_file_verification_preserves_unicode_newlines_and_hash(tmp_path, bom, expected):
    path = tmp_path / "saved.txt"
    content = (b"\xef\xbb\xbf" if bom else b"") + expected.encode("utf-8")
    path.write_bytes(content)
    result = file_tools.read_text(
        {"path": str(path), "expected_value": expected}, {"allowed_directories": [str(tmp_path)]}
    )
    assert result["ok"] and result["verified"]
    assert result["text"] == expected
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert "expected_value" not in result


@pytest.mark.parametrize("content", [b"first\nsecond", b"first\r\nwrong", b"first\r\nsecond\n", b"first\r\nsecond\xff"])
def test_saved_file_verification_rejects_changed_or_undecodable_content(tmp_path, content):
    path = tmp_path / "saved.txt"
    path.write_bytes(content)
    result = file_tools.read_text(
        {"path": str(path), "expected_value": "first\r\nsecond"}, {"allowed_directories": [str(tmp_path)]}
    )
    assert not result["ok"] and not result["verified"]
    assert result["error_code"] == "file_postcondition_failed"
    assert "expected_value" not in result


@pytest.mark.parametrize("matches", [True, False])
def test_saved_file_verification_compares_beyond_the_returned_preview(tmp_path, matches):
    path = tmp_path / "saved.txt"
    path.write_bytes(b"prefix followed by full content")
    expected = "prefix followed by full content" if matches else "prefix followed by WRONG content"
    result = file_tools.read_text(
        {"path": str(path), "expected_value": expected, "max_chars": 6}, {"allowed_directories": [str(tmp_path)]}
    )
    assert result["ok"] is matches and result["verified"] is matches
    assert result["truncated"] is True and result["text"] == "prefix"


def test_plain_file_read_keeps_existing_newline_behavior(tmp_path):
    path = tmp_path / "saved.txt"
    path.write_bytes(b"first\r\nsecond")
    result = file_tools.read_text({"path": str(path)}, {"allowed_directories": [str(tmp_path)]})
    assert result["ok"] and result["text"] == "first\nsecond"
    assert "verified" not in result


def test_saved_file_expectation_is_exposed_and_redacted():
    registry = ToolRegistry()
    file_tools.register(registry)
    tool = registry.get("file.read_text")
    assert tool.input_schema["properties"]["expected_value"]["type"] == "string"
    assert "expected_value" in tool.sensitive_arg_keys


@pytest.mark.asyncio
async def test_saved_file_mismatch_stops_dependent_steps_in_the_real_engine(monkeypatch, tmp_path):
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.core import db
    from app.core.schemas import Plan, PlanStep, StepStatus, Task, TaskStatus

    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_TASK_RECORDING_ENABLED", "false")
    db.init_db()
    path = tmp_path / "saved.txt"
    path.write_text("Old file content", encoding="utf-8")
    task = Task(
        user_goal="Verify the saved file before continuing", mode="efficiency", status=TaskStatus.REVIEWING_PLAN
    )
    verify = PlanStep(
        id="verify",
        task_id=task.id,
        agent_name="FileAgent",
        tool_name="file.read_text",
        description="Verify saved content",
        args={"path": str(path), "expected_value": "New expected content"},
    )
    after = PlanStep(
        id="after",
        task_id=task.id,
        agent_name="FileAgent",
        tool_name="file.get_metadata",
        description="Read metadata only after verification",
        args={"path": str(path)},
        depends_on=[verify.id],
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[verify, after])
    orchestrator = OrchestratorAgent()
    orchestrator.planning_handler._annotate_plan_tool_contracts(
        plan, [tool.name for tool in orchestrator.registry.list()]
    )
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    await orchestrator._process_steps(task, plan)
    assert task.status == TaskStatus.FAILED
    assert verify.status == StepStatus.FAILED
    calls = db.list_tool_calls_for_task(task.id)
    assert [call["tool_name"] for call in calls] == ["file.read_text"]
    results = db.fetch_many("tool_results", "tool_call_id = ?", (calls[0]["id"],))
    assert results[0]["output"]["error_code"] == "file_postcondition_failed"
