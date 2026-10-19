from __future__ import annotations

import base64
import json

import pytest

from app.config import AppSettings
from app.core import butler_store as store
from app.core import db
from app.core.butler_models import ButlerMatter, CompletionCondition, MatterStatus
from app.core.schemas import Task
from app.orchestration.agent_bus import AgentBus
from app.orchestration.runtime_context import TaskRuntimeContext
from app.services.butler_outcomes import check_browser_outcome, check_json_outcome


def test_receipt_needs_every_business_field_and_never_accepts_clipped_text():
    condition = CompletionCondition(
        kind="browser_fields",
        subject="https://example.com/receipt",
        checks=[
            {"label": "date", "selector": "#date", "expected": "2026-10-02"},
            {"label": "status", "selector": "#status", "expected": "confirmed"},
            {"label": "receipt", "selector": "#receipt", "operator": "nonempty"},
        ],
    )
    observation = {
        "extracted": {
            label: {"matched": True, "truncated": False, "text": value}
            for label, value in {"date": "2026-10-03", "status": "confirmed", "receipt": "receipt-one"}.items()
        }
    }
    assert not all(item["matched"] for item in check_browser_outcome(observation, condition))
    observation["extracted"]["date"]["text"] = "2026-10-02"
    assert all(item["matched"] for item in check_browser_outcome(observation, condition))
    observation["extracted"]["date"]["truncated"] = True
    assert not all(item["matched"] for item in check_browser_outcome(observation, condition))


def test_structured_artifact_checks_distinguish_missing_fields_boolean_and_number():
    condition = CompletionCondition(
        kind="file_json",
        subject="receipt.json",
        checks=[
            {"label": "quantity", "path": ["items", 0, "quantity"], "expected": 1},
            {"label": "state", "path": ["state"], "expected": "confirmed"},
        ],
    )
    assert all(
        item["matched"] for item in check_json_outcome({"items": [{"quantity": 1}], "state": "confirmed"}, condition)
    )
    assert not all(
        item["matched"] for item in check_json_outcome({"items": [{"quantity": True}], "state": "confirmed"}, condition)
    )
    assert not all(item["matched"] for item in check_json_outcome({"state": "confirmed"}, condition))


def test_owned_tab_survives_backend_cache_loss_and_replanning(monkeypatch):
    from app.services.browser_activity_runtime import BrowserActivityRuntime
    from app.services.browser_host_bridge_service import browser_host_bridge_hub

    old = Task(user_goal="prepare", metadata={"butler_matter_id": "matter-continuation"})
    new = Task(
        user_goal="verify receipt",
        metadata={"butler_matter_id": "matter-continuation", "butler_execution_ticket": "current"},
    )
    db.init_db()
    for task in (old, new):
        db.upsert_model("tasks", task)
    store.save(
        ButlerMatter(
            id="matter-continuation",
            goal="完整流程",
            task_ids=[old.id, new.id],
            active_task_id=new.id,
            status=MatterStatus.RUNNING,
            execution_ticket="current",
        )
    )
    hosted = {
        "id": "retained-tab",
        "task_id": old.id,
        "account_id": "",
        "current_url": "https://example.com/receipt",
        "status": "idle",
        "allowed_origins": ["https://example.com"],
        "last_observation": {"text": "receipt"},
    }
    monkeypatch.setattr(
        browser_host_bridge_hub, "status", lambda: {"connected": True, "snapshot": {"sessions": [hosted]}}
    )
    commands = []

    async def request(grant):
        claim = json.loads(base64.b64decode(grant["payload"]))
        commands.append(claim)
        hosted["task_id"] = claim["task_id"]
        return {"ok": True, "session": hosted}

    monkeypatch.setattr(browser_host_bridge_hub, "request_authorized_action", request)
    context = {
        "runtime": TaskRuntimeContext.from_task(new, AppSettings(), AgentBus()),
        "task_id": new.id,
        "butler_matter_id": "matter-continuation",
    }
    restored = BrowserActivityRuntime().ensure_session({"session_id": "retained-tab"}, context)
    assert restored.task_id == new.id and restored.current_url.endswith("/receipt")
    assert commands[0]["operation"] == "adopt" and commands[0]["previous_task_id"] == old.id
    again = BrowserActivityRuntime().ensure_session({"session_id": "retained-tab"}, context)
    assert again.id == restored.id and len(commands) == 1
    hosted["task_id"] = "foreign-task"
    with pytest.raises(ValueError, match="another matter"):
        BrowserActivityRuntime().ensure_session({"session_id": "retained-tab"}, context)


@pytest.mark.asyncio
async def test_waiting_matter_can_read_retained_receipt_without_a_new_execution(monkeypatch):
    from app.services.browser_host_bridge_service import browser_host_bridge_hub
    from app.services.desktop_browser_adapter import request_task_observation

    monkeypatch.setenv("LENGRVIS_ALLOW_BROWSER_NETWORK", "true")
    matter = store.save(ButlerMatter(goal="等待回执", status=MatterStatus.WAITING_EVENT, task_ids=["task-owned"]))
    hosted = {"id": "tab", "task_id": "task-owned", "account_id": "", "current_url": "https://example.com/receipt"}
    monkeypatch.setattr(
        browser_host_bridge_hub, "status", lambda: {"connected": True, "snapshot": {"sessions": [hosted]}}
    )

    async def read(grant):
        claim = json.loads(base64.b64decode(grant["payload"]))
        assert claim["action"]["kind"] == "observe" and claim["approval_id"] == ""
        return {"ok": True, "session": hosted, "observation": {"url": hosted["current_url"], "text": "confirmed"}}

    monkeypatch.setattr(browser_host_bridge_hub, "request_authorized_action", read)
    assert (await request_task_observation(matter, "tab"))["text"] == "confirmed"


def test_readback_pins_owned_page_without_authorizing_navigation_or_foreign_tabs(monkeypatch):
    from dataclasses import replace

    from app.services.browser_continuity import bind_owned_readback
    from app.services.browser_host_bridge_service import browser_host_bridge_hub
    from app.tools.browser_tools import register
    from app.tools.registry import ToolRegistry

    task = Task(
        user_goal="read receipt", metadata={"butler_matter_id": "matter-read", "butler_execution_ticket": "ticket"}
    )
    store.save(
        ButlerMatter(
            id="matter-read", goal="read receipt", task_ids=[task.id], active_task_id=task.id, execution_ticket="ticket"
        )
    )
    hosted = {"id": "owned", "task_id": task.id, "current_url": "https://example.com/receipt", "account_id": ""}
    monkeypatch.setattr(
        browser_host_bridge_hub, "status", lambda: {"connected": True, "snapshot": {"sessions": [hosted]}}
    )
    registry = ToolRegistry()
    register(registry)
    read = registry.get("browser.observe")
    args = {"url": "https://example.com/receipt", "selectors": {"id": "#receipt-id"}}
    assert bind_owned_readback(task, read, args) and args["session_id"] == "owned"
    assert not bind_owned_readback(task, read, {"url": "https://other.example/"})
    assert not bind_owned_readback(task, registry.get("browser.navigate"), args)
    assert not bind_owned_readback(task, replace(read, trust_tier="untrusted"), args)
    hosted["paused"] = True
    assert not bind_owned_readback(task, read, args)
    hosted["paused"] = False
    hosted["task_id"] = "foreign"
    assert not bind_owned_readback(task, read, args)


@pytest.mark.parametrize("status", ["committed", "outcome_unknown", "executing"])
def test_submission_cannot_be_repeated_across_runs_or_browser_aliases(status):
    from app.core.schemas import ToolCall, ToolResult
    from app.policy.risk import RiskLevel
    from app.services.butler_effects import repeated_submission
    from app.services.butler_evidence import uncertain_execution

    db.init_db()
    args = {"url": "https://example.com/form", "selector": "#form", "fields": {"reference": "unique-request"}}
    call = ToolCall(
        task_id="task-prior",
        step_id="submit",
        tool_name="browser.submit_form",
        args=args,
        risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        status=status,
        dry_run=False,
    )
    db.upsert_model("tool_calls", call)
    matter = ButlerMatter(goal="submit once", task_ids=[call.task_id, "task-continuation"])
    assert repeated_submission(matter, "browser.act", {"action": {"kind": "submit", **args}})
    assert uncertain_execution(matter)
    db.upsert_model(
        "tool_results",
        ToolResult(
            tool_call_id=call.id, ok=False, output={"adapter": "desktop_browser_host", "commit_state": "not_submitted"}
        ),
    )
    assert not repeated_submission(matter, "browser.submit_form", args)
    assert not uncertain_execution(matter)


@pytest.mark.asyncio
async def test_ambiguous_submission_reads_receipt_but_never_promotes_it_to_completion(monkeypatch):
    from app.core.schemas import ToolCall
    from app.policy.risk import RiskLevel
    from app.services import butler_evidence, desktop_browser_adapter
    from app.services.butler_service import ButlerCoordinator

    db.init_db()
    call = ToolCall(
        task_id="unknown-task",
        step_id="submit",
        tool_name="browser.submit_form",
        risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        status="outcome_unknown",
        dry_run=False,
    )
    db.upsert_model("tool_calls", call)
    matter = store.save(
        ButlerMatter(
            goal="submit once",
            task_ids=[call.task_id],
            completion_conditions=[
                {
                    "kind": "browser_fields",
                    "subject": "https://example.com/receipt",
                    "checks": [{"label": "id", "selector": "#id", "expected": "receipt-one"}],
                }
            ],
        )
    )
    monkeypatch.setattr(butler_evidence, "browser_observation", lambda *_: {"session_id": "retained"})
    reads = []

    async def observe(matter, session_id, **kwargs):
        reads.append(session_id)
        return {"extracted": {"id": {"text": "receipt-one", "matched": True, "truncated": False}}}

    monkeypatch.setattr(desktop_browser_adapter, "request_task_observation", observe)
    await ButlerCoordinator().check_uncertain_submission(matter)
    saved = store.get(ButlerMatter, matter.id)
    assert reads == ["retained"]
    assert saved.status == MatterStatus.WAITING_INPUT
    assert saved.completion_evidence[-1]["matched"]
    assert db.fetch_one("tool_calls", call.id)["status"] == "outcome_unknown"
    assert not (await butler_evidence.verify_conditions(saved))[0]


def test_json_sources_preserve_data_and_reject_ambiguous_merges(tmp_path):
    from app.tools.file_tools import read_json
    from app.tools.workflow_tools import merge_data

    path = tmp_path / "source.json"
    context = {"allowed_directories": [str(tmp_path)]}
    path.write_text('{"fields":{"notes":"中文 😀","count":2}}', encoding="utf-8-sig")
    source = read_json({"path": str(path)}, context)
    merged = merge_data({"left": source["data"], "right": {"fields": {"date": "2026-10-02"}}}, context)
    assert merged["data"]["fields"]["notes"] == "中文 😀"
    assert "date" not in source["data"]["fields"]
    with pytest.raises(ValueError, match="Sources disagree"):
        merge_data({"left": source["data"], "right": {"fields": {"count": 3}}}, context)
    with pytest.raises(ValueError, match="Sources disagree"):
        merge_data({"left": {"count": 1}, "right": {"count": True}}, context)
    path.write_text('{"count":1,"count":2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        read_json({"path": str(path)}, context)
