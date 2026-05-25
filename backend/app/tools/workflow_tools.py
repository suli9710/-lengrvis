from __future__ import annotations

import asyncio
from typing import Any

from app.orchestration.workflow import InMemoryClipboard, Workflow, WorkflowRuntime
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def run_workflow(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    workflow_data = args.get("workflow") or args
    try:
        workflow = Workflow.model_validate(workflow_data)
    except Exception as exc:  # noqa: BLE001
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

    runtime = WorkflowRuntime(clipboard=InMemoryClipboard())
    result = asyncio.run(runtime.run(workflow))
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
