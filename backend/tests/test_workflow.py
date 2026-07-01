from __future__ import annotations

import pytest

from app.orchestration.workflow import (
    InMemoryClipboard,
    Workflow,
    WorkflowError,
    WorkflowRuntime,
    WorkflowStep,
    topological_order,
)


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


@pytest.mark.asyncio
async def test_runtime_redacts_handler_exception_errors():
    private_path = "C:/Users/Suli/private/workflow/.env"
    secret_token = "workflow-secret-1234567890"
    workflow = Workflow(id="wf", steps=[WorkflowStep(id="step", action="explode")])

    def handler(_step: WorkflowStep) -> dict:
        raise RuntimeError(f"failed reading {private_path} token={secret_token}")

    result = await WorkflowRuntime().run(workflow, {"explode": handler})

    assert result.ok is False
    error = result.step_results["step"]["error"]
    assert "failed reading" in error
    assert "[REDACTED_LOCAL_PATH]" in error
    assert private_path not in error
    assert secret_token not in error
    assert result.errors == [error]


@pytest.mark.asyncio
async def test_runtime_redacts_failed_handler_result_details():
    private_file = "workflow-output.log"
    api_key = "sk-workflow-secret"
    workflow = Workflow(id="wf", steps=[WorkflowStep(id="step", action="fail")])

    def handler(_step: WorkflowStep) -> dict:
        return {
            "ok": False,
            "error": f"tool failed at {private_file} api_key={api_key}",
            "details": {"path": "C:/Users/Suli/private/workflow/result.json"},
        }

    result = await WorkflowRuntime().run(workflow, {"fail": handler})

    assert result.ok is False
    step_result = result.step_results["step"]
    assert "tool failed" in step_result["error"]
    assert private_file not in step_result["error"]
    assert api_key not in step_result["error"]
    assert step_result["details"]["path"] == "[REDACTED_LOCAL_PATH]"
    assert result.errors == [step_result["error"]]


def test_topological_order_raises_workflow_error_for_raw_cycle():
    a = WorkflowStep(id="a", action="one", depends_on=["b"])
    b = WorkflowStep(id="b", action="two", depends_on=["a"])

    with pytest.raises(WorkflowError):
        topological_order([a, b])
