from __future__ import annotations

import asyncio
import time
from typing import Any

from app.orchestration.workflow import WorkflowExecutionResult
from app.policy.execution_marker import mark_execution_approved
from app.tools import workflow_tools


def _approved_context() -> dict[str, Any]:
    context: dict[str, Any] = {}
    mark_execution_approved(context)
    return context


def _workflow_args() -> dict[str, Any]:
    return {
        "dry_run": False,
        "approved": True,
        "approval_id": "approval_test",
        "workflow": {"id": "wf_slow", "steps": []},
    }


def test_run_workflow_times_out_slow_runtime(monkeypatch):
    class SlowWorkflowRuntime:
        def __init__(self, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        async def run(self, workflow, handlers=None):  # noqa: ANN001, ARG002
            await asyncio.sleep(1.0)
            return WorkflowExecutionResult(ok=True, order=[], step_results={})

    monkeypatch.setattr(workflow_tools, "WorkflowRuntime", SlowWorkflowRuntime)
    monkeypatch.setattr(workflow_tools, "DEFAULT_WORKFLOW_RUN_TIMEOUT_SECONDS", 0.01)

    started = time.monotonic()
    result = workflow_tools.run_workflow(_workflow_args(), _approved_context())

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["workflow_id"] == "wf_slow"
    assert "timed out" in result["error"]
    assert time.monotonic() - started < 0.5


def test_run_workflow_timeout_returns_from_running_event_loop(monkeypatch):
    class SlowWorkflowRuntime:
        def __init__(self, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        async def run(self, workflow, handlers=None):  # noqa: ANN001, ARG002
            await asyncio.sleep(1.0)
            return WorkflowExecutionResult(ok=True, order=[], step_results={})

    monkeypatch.setattr(workflow_tools, "WorkflowRuntime", SlowWorkflowRuntime)
    monkeypatch.setattr(workflow_tools, "DEFAULT_WORKFLOW_RUN_TIMEOUT_SECONDS", 0.01)

    async def invoke() -> dict[str, Any]:
        return workflow_tools.run_workflow(_workflow_args(), _approved_context())

    started = time.monotonic()
    result = asyncio.run(invoke())

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["workflow_id"] == "wf_slow"
    assert "timed out" in result["error"]
    assert time.monotonic() - started < 0.5
