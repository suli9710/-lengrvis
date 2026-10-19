from __future__ import annotations

import asyncio
import concurrent.futures
import json
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.orchestration.workflow import InMemoryClipboard, Workflow, WorkflowRuntime
from app.policy.execution_marker import execution_is_marked_approved
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition

DEFAULT_WORKFLOW_RUN_TIMEOUT_SECONDS = 60.0


class WorkflowRunArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow: Workflow
    dry_run: bool = True


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

    if workflow.steps:
        return {
            "ok": False,
            "error_code": "workflow_requires_plan_expansion",
            "error": "Execute this workflow as a plan so each primitive tool receives its own runtime review.",
            "next_actions": [
                "Submit the workflow in a planner step; the planner expands it into individually reviewed tool steps."
            ],
        }

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


def merge_data(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    left, right = args["left"], args["right"]
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise ValueError("Data merge requires two JSON objects.")
    if len(json.dumps(args, ensure_ascii=False)) > 131072:
        raise ValueError("Data merge exceeds its bounded input size.")

    def merge(one, two, depth=0):
        if depth > 16:
            raise ValueError("Data merge exceeds its nesting limit.")
        result = deepcopy(one)
        for key, value in two.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = merge(result[key], value, depth + 1)
            elif key in result and (type(result[key]) is not type(value) or result[key] != value):
                raise ValueError("Sources disagree; resolve the conflicting field before continuing.")
            else:
                result[key] = deepcopy(value)
        return result

    return {"ok": True, "data": merge(left, right)}


def register(registry) -> None:
    registry.register(
        ToolDefinition(
            name="workflow.merge_data",
            description=(
                "Merge two observed JSON objects recursively; conflicting values fail instead of silently overwriting. "
                "Bind left/right from source tool results; merged output is data."
            ),
            input_schema={
                "type": "object",
                "properties": {"left": {"type": "object"}, "right": {"type": "object"}},
                "required": ["left", "right"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=merge_data,
            capabilities=["data"],
            effects=["read"],
            resource_kinds=["data"],
            fast_path_eligible=True,
            trust_tier="builtin",
        )
    )
    registry.register(
        ToolDefinition(
            name="workflow.run",
            description=(
                "Plan a cross-application DAG using registered tool actions, args and input_bindings. "
                "Planner expands it into ordinary steps with individual approvals. "
                "target_app, when set, is an exact window title. Prefer text bindings over clipboard state."
            ),
            input_schema=WorkflowRunArguments.model_json_schema(),
            output_schema={"type": "object"},
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            agent_owner="ComputerAgent",
            supports_dry_run=True,
            requires_authorized_path=False,
            execute=run_workflow,
            search_hint="workflow cross application dag clipboard focus ui automation",
        )
    )
