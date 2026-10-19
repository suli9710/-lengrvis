"""Business submission deduplication through production redaction and journal."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import butler_store as store
from app.core import db
from app.core.butler_models import ButlerMatter, MatterStatus
from app.core.schemas import PlanStep, SafetyReview, Task
from app.llm.registry import get_effective_settings
from app.orchestration.butler_runtime import ButlerExecutionDeferred, validate_task_action
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.tool_execution_journal import (
    mark_tool_call_committed,
    mark_tool_call_executing,
    mark_tool_call_outcome_unknown,
)
from app.orchestration.tool_runtime import ToolRuntime
from app.policy.effective_risk_binding import build_effective_risk_binding
from app.policy.risk import SafetyVerdict
from app.services.butler_effects import repeated_submission


@pytest.fixture
def journal(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_TASK_RECORDING_ENABLED", "false")
    db.init_db()
    orchestrator = OrchestratorAgent()
    task = Task(
        user_goal="Submit one request", metadata={"butler_matter_id": "matter", "butler_execution_ticket": "ticket"}
    )
    db.upsert_model("tasks", task)
    matter = store.save(
        ButlerMatter(
            id="matter",
            goal=task.user_goal,
            status=MatterStatus.RUNNING,
            active_task_id=task.id,
            task_ids=[task.id],
            execution_ticket="ticket",
        )
    )
    calls = []
    runtime_host = ToolRuntime(orchestrator)

    def prepare(tool_name, *, bound_task=task, reference="PRIVATE_BUSINESS_REFERENCE", output=None):
        action = {
            "url": "https://example.com/form#first",
            "selector": "#PRIVATE_FORM_SELECTOR",
            "fields": {"reference": reference, "name": "PRIVATE_FIELD_VALUE"},
        }
        args = (
            {**action, "dry_run": False}
            if tool_name == "browser.submit_form"
            else {"action": {"kind": "submit", **action}, "dry_run": False}
        )
        step = PlanStep(
            task_id=bound_task.id, agent_name="BrowserAgent", tool_name=tool_name, args=args, description="Submit once"
        )

        def execute(args, context):
            calls.append(bound_task.id)
            return output or {"ok": True, "adapter": "desktop_browser_host", "commit_state": "submitted"}

        tool = replace(orchestrator.registry.get(tool_name), execute=execute)
        runtime = TaskRuntimeContext.from_task(bound_task, get_effective_settings(), orchestrator.bus)
        review = SafetyReview(
            task_id=bound_task.id,
            step_id=step.id,
            target_type="tool_call",
            verdict=SafetyVerdict.ALLOW,
            risk_level=tool.risk_level,
        )
        db.upsert_model("safety_reviews", review)
        runtime.extra_context["effective_risk_binding"] = build_effective_risk_binding(tool.risk_level, [review])
        call, created = runtime_host._publish_tool_call_proposal(
            bound_task, step, tool, runtime, args, approval_id=None
        )
        assert created
        return call, step, tool, runtime, args

    return task, matter, runtime_host, prepare, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_tool", ["browser.submit_form", "browser.act"])
@pytest.mark.parametrize("next_tool", ["browser.submit_form", "browser.act"])
@pytest.mark.parametrize("status", ["committed", "outcome_unknown", "executing"])
@pytest.mark.parametrize("threaded", [False, True])
async def test_redacted_submission_is_blocked_across_tasks_and_aliases(
    journal, prior_tool, next_tool, status, threaded
):
    task, matter, host, prepare, executor_calls = journal
    call, step, tool, runtime, args = prepare(prior_tool)
    assert not repeated_submission(matter, prior_tool, args)  # prepared has no side effect
    call = mark_tool_call_executing(call)
    assert call is not None
    assert not repeated_submission(matter, prior_tool, args, current_tool_call_id=call.id)
    result = await host._execute_tool_call(
        task, step, tool, runtime, call, args, threaded_tools=threaded, approval_id=None
    )
    assert result.ok and executor_calls == [task.id]  # first execution remains usable
    if status != "executing":
        call = mark_tool_call_committed(call)
        if status == "outcome_unknown":
            call = mark_tool_call_outcome_unknown(call, expected_status="committed")
    stored = db.fetch_tool_call(call.id)
    assert stored["submission_digest"].startswith("browser-submission/v2:")
    assert stored["submission_binding_hmac"]
    assert all(
        marker not in json.dumps(stored)
        for marker in ("PRIVATE_FORM_SELECTOR", "PRIVATE_FIELD_VALUE", "PRIVATE_BUSINESS_REFERENCE")
    )
    continuation = Task(user_goal=task.user_goal, metadata=dict(task.metadata))
    db.upsert_model("tasks", continuation)

    def continue_matter(item):
        item.active_task_id = continuation.id
        item.task_ids.append(continuation.id)

    matter = store.mutate(ButlerMatter, matter.id, continue_matter)
    next_call, _, next_definition, _, next_args = prepare(next_tool, bound_task=continuation)
    action = next_args.get("action", next_args)
    action["url"] = "https://example.com/form#other"  # fragments are not a new submission
    assert next_call.submission_digest == call.submission_digest
    assert repeated_submission(matter, next_tool, next_args)
    with pytest.raises(ButlerExecutionDeferred, match="已有执行记录"):
        validate_task_action(continuation, next_definition, next_args, live=True)
    assert executor_calls == [task.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["browser.submit_form", "browser.act"])
async def test_verified_not_submitted_receipt_allows_safe_retry(journal, tool_name):
    task, matter, host, prepare, executor_calls = journal
    call, step, tool, runtime, args = prepare(
        tool_name, output={"ok": False, "adapter": "desktop_browser_host", "commit_state": "not_submitted"}
    )
    call = mark_tool_call_executing(call)
    receipt = await host._execute_tool_call(
        task, step, tool, runtime, call, args, threaded_tools=False, approval_id=None
    )
    db.upsert_model("tool_results", receipt)
    call = mark_tool_call_committed(call)
    assert not repeated_submission(matter, tool_name, args)
    assert validate_task_action(task, tool, args, live=True) is not None
    retry, retry_step, retry_tool, retry_runtime, retry_args = prepare(tool_name)
    retry = mark_tool_call_executing(retry)
    result = await host._execute_tool_call(
        task, retry_step, retry_tool, retry_runtime, retry, retry_args, threaded_tools=False, approval_id=None
    )
    assert result.ok and executor_calls == [task.id, task.id]


@pytest.mark.asyncio
async def test_only_current_executing_claim_is_excluded_and_different_business_payload_is_allowed(journal):
    task, matter, host, prepare, _ = journal
    call, step, tool, runtime, args = prepare("browser.act")
    call = mark_tool_call_executing(call)
    another, _, _, _, _ = prepare("browser.submit_form")
    assert repeated_submission(matter, "browser.act", args, current_tool_call_id=another.id)
    await host._execute_tool_call(task, step, tool, runtime, call, args, threaded_tools=False, approval_id=None)
    call = mark_tool_call_committed(call)
    assert repeated_submission(matter, "browser.act", args, current_tool_call_id=call.id)
    different, _, different_tool, _, different_args = prepare(
        "browser.submit_form", reference="ANOTHER_BUSINESS_REQUEST"
    )
    assert different.submission_digest != call.submission_digest
    assert validate_task_action(task, different_tool, different_args, live=True) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["digest", "binding", "legacy"])
async def test_invalid_or_legacy_redacted_submission_policy_fails_closed(journal, tamper):
    task, matter, host, prepare, _ = journal
    call, step, tool, runtime, args = prepare("browser.submit_form")
    call = mark_tool_call_executing(call)
    await host._execute_tool_call(task, step, tool, runtime, call, args, threaded_tools=False, approval_id=None)
    call = mark_tool_call_committed(call)
    if tamper == "digest":
        call.submission_digest = "browser-submission/v1:" + "0" * 64
    elif tamper == "binding":
        call.submission_binding_hmac = "browser-submission-binding/v1:" + "0" * 64
    else:
        call.submission_digest = call.submission_binding_hmac = ""
    db.upsert_model("tool_calls", call)
    assert repeated_submission(matter, "browser.act", {"action": {"kind": "submit", **args}})
