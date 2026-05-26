from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.policy.risk import RiskLevel


ToolExecutor = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
ToolInputValidator = Callable[[dict[str, Any], dict[str, Any]], None]
ToolPermissionPolicy = Callable[[dict[str, Any], dict[str, Any]], bool]
ToolResultSummarizer = Callable[[dict[str, Any]], str]
ToolLifecycleHook = Callable[[dict[str, Any], dict[str, Any]], None]


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
    permission_mode: str = "ask_on_write"
    search_hint: str = ""
    read_only: bool | None = None
    concurrency_safe: bool | None = None
    concurrency_key: str = ""
    destructive: bool = False
    validate_input: ToolInputValidator | None = None
    permission_policy: ToolPermissionPolicy | None = None
    pre_execute: ToolLifecycleHook | None = None
    post_execute: ToolLifecycleHook | None = None
    max_result_size: int = 20000
    defer_loading: bool = False
    result_summary: ToolResultSummarizer | None = None
    progress_schema: dict[str, Any] = field(default_factory=dict)
    ui_summary: str = ""
    hooks: dict[str, list[str]] = field(default_factory=dict)
    origin: str = "builtin"
    app_target: dict[str, Any] | None = None
    workflow: dict[str, Any] | None = None
    capabilities: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    resource_kinds: list[str] = field(default_factory=list)
    fast_path_eligible: bool = False
    trust_tier: str = "unknown"
    sensitive_arg_keys: list[str] = field(default_factory=list)
    external_network: bool = False
    feature_flag: str = ""
    tool_version: str = "1"

    def is_read_only(self) -> bool:
        if self.read_only is not None:
            return self.read_only
        return self.risk_level == RiskLevel.R0_READ_ONLY and not self.supports_dry_run

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        if self.concurrency_safe is not None:
            return self.concurrency_safe
        if self.concurrency_key:
            return False
        if self.destructive:
            return False
        return self.is_read_only()

    def progress_event(
        self,
        status: str,
        *,
        task_id: str = "",
        step_id: str | None = None,
        tool_call_id: str = "",
        detail: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "kind": "tool_progress",
            "status": status,
            "task_id": task_id,
            "step_id": step_id,
            "tool_call_id": tool_call_id,
            "tool_name": self.name,
            "detail": detail or self.ui_summary or self.description,
            "schema": self.progress_schema,
            "payload": payload or {},
        }

    def to_public_dict(self, *, include_schema: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "agent_owner": self.agent_owner,
            "risk_level": self.risk_level.value,
            "permission_mode": self.permission_mode,
            "read_only": self.is_read_only(),
            "concurrency_safe": self.is_concurrency_safe(),
            "search_hint": self.search_hint,
            "defer_loading": self.defer_loading,
            "capabilities": list(self.capabilities),
            "effects": list(self.effects),
            "resource_kinds": list(self.resource_kinds),
            "fast_path_eligible": self.fast_path_eligible,
            "trust_tier": self.trust_tier,
            "origin": self.origin,
            "external_network": self.external_network,
            "feature_flag": self.feature_flag,
            "ui_summary": self.ui_summary,
            "hooks": self.hooks,
            "progress_schema": self.progress_schema,
            "tool_version": self.tool_version,
        }
        if include_schema:
            payload.update(
                {
                    "input_schema": self.input_schema,
                    "output_schema": self.output_schema,
                    "supports_dry_run": self.supports_dry_run,
                    "requires_authorized_path": self.requires_authorized_path,
                    "sensitive_arg_keys": list(self.sensitive_arg_keys),
                    "app_target": self.app_target,
                    "workflow": self.workflow,
                }
            )
        return payload
