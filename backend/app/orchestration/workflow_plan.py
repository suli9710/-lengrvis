"""Compile cross-app workflows into ordinary, individually reviewed plan steps."""

from __future__ import annotations

from copy import deepcopy

from app.core.schemas import Plan, PlanStep
from app.orchestration.workflow import Workflow, WorkflowError, topological_order
from app.policy.model_boundary import model_control_arg_error


def expand_workflow_steps(plan: Plan, registry, visible_tools: list[str]) -> None:
    if not any(step.tool_name == "workflow.run" for step in plan.steps):
        return
    expanded: list[PlanStep] = []
    replacements: dict[str, list[str]] = {}
    visible = set(visible_tools)
    for parent in plan.steps:
        if parent.tool_name != "workflow.run":
            expanded.append(parent.model_copy(deep=True))
            continue
        if parent.input_bindings or model_control_arg_error(parent.args):
            raise WorkflowError("A workflow definition must be concrete and cannot supply approval/runtime controls.")
        workflow_data = parent.args.get("workflow") or {
            key: value for key, value in parent.args.items() if key != "dry_run"
        }
        try:
            workflow = Workflow.model_validate(workflow_data)
        except ValueError as exc:
            raise WorkflowError("The workflow definition does not match the workflow input schema.") from exc
        if not workflow.steps:
            raise WorkflowError("An executable workflow must contain at least one step.")
        ids = {step.id: f"{parent.id}__{step.id}" for step in workflow.steps}
        by_id = {step.id: step for step in workflow.steps}
        nonterminal = {dependency for step in workflow.steps for dependency in step.depends_on}
        replacements[parent.id] = [ids[step.id] for step in workflow.steps if step.id not in nonterminal]
        for step_id in topological_order(workflow.steps):
            source = by_id[step_id]
            tool_name = source.action if "." in source.action else f"{source.interface}.{source.action}"
            if tool_name == "workflow.run" or tool_name not in visible:
                raise WorkflowError(f"Workflow action {tool_name} is not an available primitive tool.")
            try:
                tool = registry.get(tool_name)
            except KeyError as exc:
                raise WorkflowError(f"Workflow action {tool_name} is not registered.") from exc
            args = deepcopy(source.args)
            for name, value in source.ui_selector.items():
                if name in args and args[name] != value:
                    raise WorkflowError(f"Workflow step {source.id} has conflicting selector arguments.")
                args[name] = value
            if source.data_transfer:
                if tool_name != "ui_automation.type_text" or set(source.data_transfer) - {
                    "clipboard_text",
                    "restore_clipboard",
                }:
                    raise WorkflowError("Use args and input_bindings for workflow data transfer.")
                if "clipboard_text" in source.data_transfer:
                    if "text" in args and args["text"] != source.data_transfer["clipboard_text"]:
                        raise WorkflowError(f"Workflow step {source.id} has conflicting text arguments.")
                    args["text"] = source.data_transfer["clipboard_text"]
            dependencies = [ids[item] for item in source.depends_on] or list(parent.depends_on)
            if source.target_app:
                if "ui_automation.focus_window" not in visible:
                    raise WorkflowError("Window focus is not available in this plan's tool surface.")
                focus_id = ids[source.id] + "__focus"
                expanded.append(
                    PlanStep(
                        id=focus_id,
                        task_id=parent.task_id,
                        agent_name="ComputerAgent",
                        tool_name="ui_automation.focus_window",
                        description=f"Focus window {source.target_app}",
                        args={"title": source.target_app},
                        depends_on=list(dependencies),
                    )
                )
                # Preserve data dependencies as well as the window-focus dependency.
                dependencies = [*dependencies, focus_id]
            expanded.append(
                PlanStep(
                    id=ids[source.id],
                    task_id=parent.task_id,
                    agent_name=tool.agent_owner,
                    tool_name=tool_name,
                    description=source.description or f"Workflow {workflow.name or workflow.id}: {source.action}",
                    args=args,
                    depends_on=dependencies,
                    input_bindings={
                        name: binding.model_copy(update={"step_id": ids[binding.step_id]})
                        for name, binding in source.input_bindings.items()
                    },
                )
            )
    if len(expanded) > 128 or len({step.id for step in expanded}) != len(expanded):
        raise WorkflowError("Expanded workflow exceeds the plan limit or has conflicting step ids.")
    for order, step in enumerate(expanded, 1):
        step.order = order
        step.depends_on = list(
            dict.fromkeys(
                replacement
                for dependency in step.depends_on
                for replacement in replacements.get(dependency, [dependency])
            )
        )
        for name, binding in step.input_bindings.items():
            if binding.step_id in replacements:
                leaves = replacements[binding.step_id]
                if len(leaves) != 1:
                    raise WorkflowError("A workflow with multiple terminal outputs cannot be used as one input source.")
                step.input_bindings[name] = binding.model_copy(update={"step_id": leaves[0]})
    # Publish atomically only after the complete expansion has been validated.
    plan.steps = expanded
