from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.policy.risk import RiskLevel


ToolExecutor = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
ToolInputValidator = Callable[[dict[str, Any], dict[str, Any]], None]
ToolPermissionPolicy = Callable[[dict[str, Any], dict[str, Any]], bool]
ToolResultSummarizer = Callable[[dict[str, Any]], str]


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
    search_hint: str = ""
    read_only: bool | None = None
    concurrency_key: str = ""
    destructive: bool = False
    validate_input: ToolInputValidator | None = None
    permission_policy: ToolPermissionPolicy | None = None
    max_result_size: int = 20000
    defer_loading: bool = False
    result_summary: ToolResultSummarizer | None = None
    app_target: dict[str, Any] | None = None
    workflow: dict[str, Any] | None = None

    def is_read_only(self) -> bool:
        if self.read_only is not None:
            return self.read_only
        return self.risk_level == RiskLevel.R0_READ_ONLY and not self.supports_dry_run

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        if self.concurrency_key:
            return False
        if self.destructive:
            return False
        return self.is_read_only()
