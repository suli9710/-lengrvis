from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

from app.orchestration.workflow import InMemoryClipboard, Workflow, WorkflowRuntime
from app.policy.execution_marker import execution_is_marked_approved
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition

DEFAULT_WORKFLOW_RUN_TIMEOUT_SECONDS = 60.0


async def _with_timeout(coro, timeout_seconds: float | None) -> Any:
    if timeout_seconds is None or timeout_seconds <= 0:
        return await coro
    return await asyncio.wait_for(coro, timeout=timeout_seconds)


def _run_workflow_async(coro, *, timeout_seconds: float | None = None) -> Any:
    if timeout_seconds is None:
        timeout_seconds = DEFAULT_WORKFLOW_RUN_TIMEOUT_SECONDS
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_with_timeout(coro, timeout_seconds))
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(asyncio.run, _with_timeout(coro, timeout_seconds))
        try:
            guard_timeout = None if timeout_seconds is None or timeout_seconds <= 0 else timeout_seconds + 1
            return future.result(timeout=guard_timeout)
        finally:
            if not future.done():
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
    except TimeoutError:
        return None


def run_workflow(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    workflow_data = args.get("workflow") or args
    try:
        workflow = Workflow.model_validate(workflow_data)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        return {"ok": False, "error": f"Invalid workflow: {exc}"}

    if args.get("dry_run", True):
        return {
            "ok": True,
            "dry_run": True,
            "workflow_id": workflow.id,
            "steps": [step.model_dump(mode="json") for step in workflow.steps],
            "message": "Workflow preview. Execution requires approval for cross-application control.",
        }

    if not args.get("approved") or not args.get("approval_id"):
        return {"ok": False, "error": "Workflow execution requires an approved approval_id after dry-run preview."}
    if not execution_is_marked_approved(context):
        # SEC-002: live execution must run through the validated orchestrator/route gate.
        return {"ok": False, "error": "Workflow execution must run through the validated approval gate."}

    runtime = WorkflowRuntime(clipboard=InMemoryClipboard())
    result = _run_workflow_async(runtime.run(workflow))
    if result is None:
        return {
            "ok": False,
            "status": "timeout",
            "workflow_id": workflow.id,
            "error": "Workflow execution timed out.",
        }
    return {
        "ok": result.ok,
        "workflow_id": workflow.id,
        "order": result.order,
        "step_results": result.step_results,
        "errors": result.errors,
    }


def register(registry) -> None:
    registry.register(
        ToolDefinition(
            name="workflow.run",
            description="Run a cross-application workflow DAG with clipboard and window focus management.",
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object"},
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            agent_owner="ComputerAgent",
            supports_dry_run=True,
            requires_authorized_path=False,
            execute=run_workflow,
            search_hint="workflow cross application dag clipboard focus ui automation",
        )
    )
