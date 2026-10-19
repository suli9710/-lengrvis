from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.step_inputs import StepInputBinding as WorkflowInputBinding
from app.core.step_inputs import resolve_inputs
from app.policy.redaction import redact_public_text, redact_value


class WorkflowError(ValueError):
    """Raised when a workflow graph is invalid or cannot be executed."""


class ClipboardProvider(Protocol):
    def get_text(self) -> str:
        raise NotImplementedError

    def set_text(self, text: str) -> None:
        raise NotImplementedError


class WindowFocusProvider(Protocol):
    def focus(self, target_app: str) -> bool:
        raise NotImplementedError


class InMemoryClipboard:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text


class BestEffortWindowFocus:
    def focus(self, target_app: str) -> bool:
        if not target_app:
            return False
        try:
            import pygetwindow  # type: ignore[import-not-found]
        except ImportError:
            return False
        try:
            windows = pygetwindow.getWindowsWithTitle(target_app)
            if len(windows) != 1:
                return False
            windows[0].activate()
            return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    target_app: str = Field(default="", validation_alias="target_app")
    action: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    input_bindings: dict[str, WorkflowInputBinding] = Field(default_factory=dict, max_length=32)
    data_transfer: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    ui_selector: dict[str, Any] = Field(default_factory=dict)
    interface: str = "ui_automation"
    description: str = ""


class Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    name: str = ""
    steps: list[WorkflowStep] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_dag(self) -> Workflow:
        step_ids = [step.id for step in self.steps]
        duplicates = sorted({step_id for step_id in step_ids if step_ids.count(step_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate workflow step ids: {', '.join(duplicates)}")
        known = set(step_ids)
        for step in self.steps:
            missing = [dependency for dependency in step.depends_on if dependency not in known]
            if missing:
                raise ValueError(f"step {step.id} depends on unknown steps: {', '.join(missing)}")
            for binding in step.input_bindings.values():
                if binding.step_id not in step.depends_on:
                    raise ValueError(f"step {step.id} input source {binding.step_id} must be an explicit dependency")
        topological_order(self.steps)
        return self


@dataclass(slots=True)
class WorkflowExecutionResult:
    ok: bool
    order: list[str]
    step_results: dict[str, dict[str, Any]]
    errors: list[str] = field(default_factory=list)


class WorkflowRuntime:
    def __init__(
        self,
        *,
        clipboard: ClipboardProvider | None = None,
        focus_provider: WindowFocusProvider | None = None,
    ) -> None:
        self.clipboard = clipboard or InMemoryClipboard()
        self.focus_provider = focus_provider or BestEffortWindowFocus()

    async def run(self, workflow: Workflow, handlers: dict[str, Any] | None = None) -> WorkflowExecutionResult:
        handlers = handlers or {}
        order = topological_order(workflow.steps)
        by_id = {step.id: step for step in workflow.steps}
        results: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for step_id in order:
            step = by_id[step_id]
            blocked = [dependency for dependency in step.depends_on if results[dependency].get("ok") is not True]
            if blocked:
                result = {
                    "ok": False,
                    "status": "skipped",
                    "blocked_by": blocked,
                    "error_code": "workflow_dependency_failed",
                    "error": f"Step {step.id} skipped because a dependency did not succeed.",
                    "next_actions": ["Resolve the failed dependency before retrying this step."],
                }
            else:
                result = await self._execute_step(step, handlers, results)
            if not result.get("ok", False):
                result = _public_workflow_failure_result(result)
                errors.append(_safe_workflow_error(result.get("error") or f"step {step.id} failed"))
            results[step.id] = result
        return WorkflowExecutionResult(ok=not errors, order=order, step_results=results, errors=errors)

    async def _execute_step(
        self, step: WorkflowStep, handlers: dict[str, Any], results: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        if not (handlers.get(step.action) or handlers.get(step.interface)):
            return {
                "ok": False,
                "error": "No workflow handler registered.",
                "error_code": "workflow_handler_missing",
                "step_id": step.id,
                "next_actions": ["Register a validated tool runtime handler for this action."],
            }
        result: dict[str, Any] = {}
        focus_ok = False
        clipboard_restore = None
        try:
            bound_step = bind_step_inputs(step, results)
            if step.target_app:
                focus_ok = await asyncio.to_thread(self.focus_provider.focus, step.target_app)
                if not focus_ok:
                    return {
                        "ok": False,
                        "focus_ok": False,
                        "error_code": "workflow_focus_failed",
                        "error": "Target application could not be uniquely focused; action was not performed.",
                        "next_actions": ["Locate a unique application window before retrying."],
                    }
            clipboard_restore = self._prepare_clipboard(bound_step)
            result = await self._run_step(bound_step, handlers)
            result.setdefault("focus_ok", focus_ok)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: workflow adapters fail as an individual step.
            result = {"ok": False, "error": _safe_workflow_error(exc), "focus_ok": focus_ok}
        finally:
            if clipboard_restore is not None:
                try:
                    self.clipboard.set_text(clipboard_restore)
                except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: clipboard restoration is part of the step outcome.
                    result.update(
                        ok=False,
                        error_code="workflow_clipboard_restore_failed",
                        error=_safe_workflow_error(f"Clipboard restoration failed: {exc}"),
                    )
        return result

    def _prepare_clipboard(self, step: WorkflowStep) -> str | None:
        transfer = step.data_transfer or {}
        if "clipboard_text" not in transfer:
            return None
        previous = self.clipboard.get_text()
        self.clipboard.set_text(str(transfer["clipboard_text"]))
        return previous if transfer.get("restore_clipboard", True) else None

    async def _run_step(self, step: WorkflowStep, handlers: dict[str, Any]) -> dict[str, Any]:
        handler = handlers.get(step.action) or handlers.get(step.interface)
        if handler is None:
            return {"ok": False, "error": "No workflow handler registered.", "step_id": step.id}
        if asyncio.iscoroutinefunction(handler):
            return await handler(step)
        return await asyncio.to_thread(handler, step)


def topological_order(steps: list[WorkflowStep]) -> list[str]:
    incoming = {step.id: set(step.depends_on) for step in steps}
    outgoing: dict[str, set[str]] = {step.id: set() for step in steps}
    for step in steps:
        for dependency in step.depends_on:
            outgoing[dependency].add(step.id)

    ready = deque(sorted(step_id for step_id, dependencies in incoming.items() if not dependencies))
    order: list[str] = []
    while ready:
        step_id = ready.popleft()
        order.append(step_id)
        for child in sorted(outgoing[step_id]):
            incoming[child].discard(step_id)
            if not incoming[child]:
                ready.append(child)

    if len(order) != len(steps):
        raise WorkflowError("workflow contains a dependency cycle")
    return order


def bind_step_inputs(step: WorkflowStep, results: dict[str, dict[str, Any]]) -> WorkflowStep:
    """Resolve explicit dependency output paths as data, without evaluating text."""
    args = resolve_inputs(step.args, step.input_bindings, step.depends_on, results)
    return step.model_copy(update={"args": args})


def _public_workflow_failure_result(result: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _public_workflow_failure_value(value) for key, value in result.items()}


def _public_workflow_failure_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _public_workflow_failure_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_workflow_failure_value(item) for item in value]
    if isinstance(value, tuple):
        return [_public_workflow_failure_value(item) for item in value]
    if isinstance(value, set):
        return [_public_workflow_failure_value(item) for item in sorted(value, key=str)]
    if isinstance(value, str):
        return _safe_workflow_error(value)
    return value


def _safe_workflow_error(value: Any) -> str:
    return redact_public_text(str(redact_value(str(value or "")) or "")) or value.__class__.__name__
