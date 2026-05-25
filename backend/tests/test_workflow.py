from __future__ import annotations

import pytest

from app.orchestration.workflow import InMemoryClipboard, Workflow, WorkflowError, WorkflowRuntime, WorkflowStep, topological_order


class FocusRecorder:
    def __init__(self) -> None:
        self.targets: list[str] = []

    def focus(self, target_app: str) -> bool:
        self.targets.append(target_app)
        return target_app == "wps.office"


def test_workflow_validates_and_orders_dag():
    workflow = Workflow(
        id="wf",
        steps=[
            WorkflowStep(id="write", target_app="wps.office", action="write", depends_on=["open"]),
            WorkflowStep(id="open", target_app="wps.office", action="open"),
            WorkflowStep(id="save", target_app="wps.office", action="save", depends_on=["write"]),
        ],
    )

    assert topological_order(workflow.steps) == ["open", "write", "save"]


def test_workflow_rejects_cycles():
    with pytest.raises(ValueError, match="dependency cycle"):
        Workflow(
            id="cycle",
            steps=[
                WorkflowStep(id="a", action="one", depends_on=["b"]),
                WorkflowStep(id="b", action="two", depends_on=["a"]),
            ],
        )


@pytest.mark.asyncio
async def test_runtime_restores_clipboard_and_tracks_focus():
    clipboard = InMemoryClipboard("original")
    focus = FocusRecorder()
    workflow = Workflow(
        id="wf",
        steps=[
            WorkflowStep(
                id="paste",
                target_app="wps.office",
                action="paste",
                data_transfer={"clipboard_text": "draft", "restore_clipboard": True},
            )
        ],
    )

    def handler(step: WorkflowStep) -> dict:
        assert clipboard.get_text() == "draft"
        return {"ok": True, "step_id": step.id}

    result = await WorkflowRuntime(clipboard=clipboard, focus_provider=focus).run(workflow, {"paste": handler})

    assert result.ok is True
    assert result.order == ["paste"]
    assert result.step_results["paste"]["focus_ok"] is True
    assert clipboard.get_text() == "original"
    assert focus.targets == ["wps.office"]


@pytest.mark.asyncio
async def test_runtime_reports_unknown_handler_as_failed_execution():
    workflow = Workflow(id="wf", steps=[WorkflowStep(id="noop", target_app="unknown", action="missing")])

    result = await WorkflowRuntime().run(workflow)

    assert result.ok is False
    assert result.step_results["noop"]["ok"] is False
    assert "No workflow handler" in result.errors[0]


def test_topological_order_raises_workflow_error_for_raw_cycle():
    a = WorkflowStep(id="a", action="one", depends_on=["b"])
    b = WorkflowStep(id="b", action="two", depends_on=["a"])

    with pytest.raises(WorkflowError):
        topological_order([a, b])
