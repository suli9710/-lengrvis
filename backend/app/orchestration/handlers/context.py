from __future__ import annotations

from dataclasses import dataclass

from app.core.schemas import ToolResult


@dataclass(slots=True)
class StepExecutionOutcome:
    kind: str
    result: ToolResult | None = None
