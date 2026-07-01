from __future__ import annotations

import inspect
from typing import Any

from app.core.schemas import PlanStep, Task, ToolResult
from app.tools.schemas import ToolDefinition


class ToolRuntimeReviewMixin:
    def _review_tool_call(
        self,
        safety: Any,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        risk_level: Any,
        *,
        context: dict[str, Any],
        tool_definition: ToolDefinition,
    ):
        review_tool_call = safety.review_tool_call
        kwargs: dict[str, Any] = {}
        accepted_keywords = self._accepted_review_tool_call_keywords(review_tool_call)
        if accepted_keywords is None or "context" in accepted_keywords:
            kwargs["context"] = context
        if accepted_keywords is None or "tool_definition" in accepted_keywords:
            kwargs["tool_definition"] = tool_definition
        return review_tool_call(task_id, step_id, tool_name, args, risk_level, **kwargs)

    def _accepted_review_tool_call_keywords(self, review_tool_call: Any) -> set[str] | None:
        try:
            signature = inspect.signature(review_tool_call)
        except (TypeError, ValueError):
            return None
        accepted: set[str] = set()
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return None
            if parameter.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}:
                accepted.add(parameter.name)
        return accepted

    def _review_tool_result(
        self,
        task: Task,
        step: PlanStep,
        tool: ToolDefinition,
        result: ToolResult,
    ):
        orchestrator = self.orchestrator
        review_tool_result = orchestrator.safety.review_tool_result
        kwargs: dict[str, Any] = {}
        accepted_keywords = self._accepted_review_tool_call_keywords(review_tool_result)
        if accepted_keywords is None or "tool_definition" in accepted_keywords:
            kwargs["tool_definition"] = tool
        return review_tool_result(task.id, step.id, step.tool_name, result, tool.risk_level, **kwargs)
