from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.policy.risk import RiskLevel


ToolExecutor = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    risk_level: RiskLevel
    agent_owner: str
    supports_dry_run: bool
    requires_authorized_path: bool
    execute: ToolExecutor

