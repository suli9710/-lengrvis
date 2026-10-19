from __future__ import annotations

from app.core.schemas import PlanStep, ToolResult
from app.core.step_inputs import resolve_inputs
from app.policy.model_boundary import model_control_arg_error


def bind_plan_step_inputs(step: PlanStep, observations: dict[str, ToolResult]) -> None:
    """Bind data before tool validation, safety review and approval creation."""
    if not step.input_bindings:
        return
    targets = dict.fromkeys(step.input_bindings, None)
    if model_control_arg_error(targets) or "dry_run" in targets:
        raise ValueError("Dependency outputs cannot supply runtime or approval control arguments.")
    results = {}
    for dependency in step.depends_on:
        result = observations.get(dependency)
        if not isinstance(result, ToolResult) or not result.ok:
            continue
        if any(result.output.get(flag) for flag in ("withheld", "review_pending", "outcome_unknown")):
            continue
        results[dependency] = {**result.output, "ok": result.output.get("ok", True) is True}
    args = resolve_inputs(step.args, step.input_bindings, step.depends_on, results)
    if model_control_arg_error(args):
        raise ValueError("Resolved dependency data contains forbidden runtime control arguments.")
    step.args = args
