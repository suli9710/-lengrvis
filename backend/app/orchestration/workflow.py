from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        except Exception:
            return False
        try:
            windows = pygetwindow.getWindowsWithTitle(target_app)
            if not windows:
                return False
            windows[0].activate()
            return True
        except Exception:
            return False


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    target_app: str = Field(default="", validation_alias="target_app")
    action: str = Field(min_length=1)
    data_transfer: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    ui_selector: dict[str, Any] = Field(default_factory=dict)
    interface: str = "ui_automation"
    description: str = ""


class Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    name: str = ""
    steps: list[WorkflowStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dag(self) -> "Workflow":
        step_ids = [step.id for step in self.steps]
        duplicates = sorted({step_id for step_id in step_ids if step_ids.count(step_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate workflow step ids: {', '.join(duplicates)}")
        known = set(step_ids)
        for step in self.steps:
            missing = [dependency for dependency in step.depends_on if dependency not in known]
            if missing:
                raise ValueError(f"step {step.id} depends on unknown steps: {', '.join(missing)}")
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
            focus_ok = self.focus_provider.focus(step.target_app) if step.target_app else False
            clipboard_restore = self._prepare_clipboard(step)
            try:
                result = await self._run_step(step, handlers)
                result.setdefault("focus_ok", focus_ok)
                results[step.id] = result
                if not result.get("ok", False):
                    errors.append(str(result.get("error") or f"step {step.id} failed"))
            except Exception as exc:  # noqa: BLE001 - workflow handlers are user-provided integrations.
                results[step.id] = {"ok": False, "error": str(exc), "focus_ok": focus_ok}
                errors.append(str(exc))
            finally:
                if clipboard_restore is not None:
                    self.clipboard.set_text(clipboard_restore)
        return WorkflowExecutionResult(ok=not errors, order=order, step_results=results, errors=errors)

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
    by_id = {step.id: step for step in steps}
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
