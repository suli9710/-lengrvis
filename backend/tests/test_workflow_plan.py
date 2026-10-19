from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.schemas import Plan, PlanStep
from app.orchestration.handlers.planning_handler import PlanningHandler
from app.orchestration.workflow import WorkflowError
from app.orchestration.workflow_plan import expand_workflow_steps
from app.policy.execution_marker import mark_execution_approved
from app.policy.risk import RiskLevel
from app.tools import ui_automation_tools, workflow_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    result = ToolRegistry()
    ui_automation_tools.register(result)
    workflow_tools.register(result)
    return result


def _plan(workflow_steps, *following):
    return Plan(
        task_id="task",
        goal="Transfer source text to another app",
        steps=[
            PlanStep(
                id="transfer",
                task_id="task",
                tool_name="workflow.run",
                agent_name="ComputerAgent",
                description="Cross-app transfer",
                args={"workflow": {"id": "wf", "steps": workflow_steps}},
            ),
            *following,
        ],
    )


def _cross_app_steps():
    return [
        {
            "id": "read",
            "action": "get_property",
            "args": {"prop": "text"},
            "ui_selector": {"automation_id": "source", "process_id": 123},
        },
        {
            "id": "write",
            "action": "type_text",
            "depends_on": ["read"],
            "ui_selector": {"automation_id": "target", "process_id": 456},
            "input_bindings": {"text": {"step_id": "read", "path": ["value"]}},
        },
    ]


def test_workflow_expands_to_tools_with_authoritative_risk_and_source_binding(registry):
    plan = _plan(_cross_app_steps())
    visible = [tool.name for tool in registry.list()]
    expand_workflow_steps(plan, registry, visible)
    PlanningHandler(SimpleNamespace(registry=registry))._annotate_plan_tool_contracts(plan, visible)

    read, write = plan.steps
    assert read.tool_name == "ui_automation.get_property"
    assert write.tool_name == "ui_automation.type_text"
    assert write.args == {"automation_id": "target", "process_id": 456, "dry_run": True}
    assert write.risk_level == RiskLevel.R2_REVERSIBLE_MODIFY
    assert write.requires_approval is True
    assert write.depends_on == [read.id]
    assert write.input_bindings["text"].step_id == read.id
    assert "approved" not in write.args


def test_following_step_waits_for_every_terminal_workflow_step(registry):
    after = PlanStep(
        id="verify",
        agent_name="ComputerAgent",
        tool_name="ui_automation.list_windows",
        description="Continue after workflow",
        depends_on=["transfer"],
    )
    plan = _plan([{"id": "a", "action": "list_windows"}, {"id": "b", "action": "active_window"}], after)
    expand_workflow_steps(plan, registry, [tool.name for tool in registry.list()])
    assert set(plan.steps[-1].depends_on) == {"transfer__a", "transfer__b"}


def test_focus_becomes_a_real_dependency_without_losing_data_binding(registry):
    steps = _cross_app_steps()
    steps[1]["target_app"] = "Target editor"
    plan = _plan(steps)
    expand_workflow_steps(plan, registry, [tool.name for tool in registry.list()])

    read, focus, write = plan.steps
    assert focus.tool_name == "ui_automation.focus_window"
    assert focus.args == {"title": "Target editor"}
    assert write.depends_on == [read.id, focus.id]
    assert write.input_bindings["text"].step_id == read.id


@pytest.mark.parametrize(
    "steps",
    [
        [{"id": "a", "action": "unknown"}],
        [{"id": "a", "action": "workflow.run"}],
        [{"id": "a", "action": "list_windows", "args": {"approved": True}}],
        [{"id": "a", "action": "click", "args": {"name": "one"}, "ui_selector": {"name": "two"}}],
    ],
)
def test_invalid_workflow_does_not_partially_mutate_the_plan(registry, steps):
    plan = _plan(steps)
    original = plan.model_dump()
    with pytest.raises(WorkflowError):
        expand_workflow_steps(plan, registry, [tool.name for tool in registry.list()])
    assert plan.model_dump() == original


def test_workflow_cannot_expand_outside_visible_tool_surface(registry):
    plan = _plan(_cross_app_steps())
    with pytest.raises(WorkflowError, match="available primitive"):
        expand_workflow_steps(plan, registry, ["workflow.run", "ui_automation.get_property"])


def test_direct_macro_approval_never_authorizes_nested_native_actions():
    context = {}
    mark_execution_approved(context)
    result = workflow_tools.run_workflow(
        {
            "workflow": {"id": "wf", "steps": _cross_app_steps()},
            "dry_run": False,
            "approved": True,
            "approval_id": "parent-approval",
        },
        context,
    )
    assert result["ok"] is False
    assert result["error_code"] == "workflow_requires_plan_expansion"


def test_workflow_schema_exposes_actions_and_bindings(registry):
    schema = registry.get("workflow.run").input_schema
    assert "workflow" in schema["properties"]
    assert {"action", "args", "input_bindings"} <= set(schema["$defs"]["WorkflowStep"]["properties"])
