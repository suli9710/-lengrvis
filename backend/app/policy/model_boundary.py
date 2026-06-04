from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


MODEL_CONTROL_KEYS = {
    "approved",
    "approval_id",
    "approval_token",
    "approved_by_user",
    "args_binding_hmac",
    "auto_approved",
    "dry_run_approved",
    "model_boundary",
    "permission_policy_version",
    "policy_decision",
    "preview_hmac",
    "risk_level",
    "runtime_state",
    "settings_fingerprint",
    "consumed_at",
    "tool_trust_tier",
    "tool_version",
    "trust_tier",
}
MODEL_ACTION_TYPES = {
    "plan_step",
    "subagent_action",
    "tool_call",
    "approved_tool_call",
}
ModelActionType = Literal["plan_step", "subagent_action", "tool_call", "approved_tool_call"]


class ModelActionEnvelope(BaseModel):
    """Provider-safe description of a model-proposed action.

    The model is allowed to propose intent and arguments only. Runtime state,
    approvals, risk/trust classification, and policy decisions are attached by
    Mavris after boundary validation.
    """

    model_config = ConfigDict(extra="forbid")

    action_type: ModelActionType
    tool_name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    model_reason: str = ""
    context_snapshot_id: str = ""
    visible_tool_ids: list[str] = Field(default_factory=list)
    source: str = ""
    task_id: str = ""
    step_id: str | None = None
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)

    def boundary_error(self) -> str:
        return model_control_arg_error(self.args)

    def to_metadata(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def build_model_action_envelope(
    *,
    action_type: ModelActionType,
    tool_name: str,
    args: dict[str, Any] | None = None,
    model_reason: str = "",
    context_snapshot_id: str = "",
    visible_tool_ids: list[str] | None = None,
    source: str = "",
    task_id: str = "",
    step_id: str | None = None,
    runtime_metadata: dict[str, Any] | None = None,
) -> ModelActionEnvelope:
    proposed_args = dict(args or {})
    boundary_error = model_control_arg_error(proposed_args)
    if boundary_error:
        raise ValueError(boundary_error)
    return ModelActionEnvelope(
        action_type=action_type,
        tool_name=str(tool_name or ""),
        args=proposed_args,
        model_reason=str(model_reason or ""),
        context_snapshot_id=str(context_snapshot_id or ""),
        visible_tool_ids=[str(item) for item in (visible_tool_ids or []) if str(item).strip()],
        source=str(source or ""),
        task_id=str(task_id or ""),
        step_id=str(step_id) if step_id else None,
        runtime_metadata=dict(runtime_metadata or {}),
    )


def model_control_arg_paths(value: Any, *, _path: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{_path}.{key_text}" if _path else key_text
            if _is_model_control_key(key_text):
                paths.append(child_path)
                continue
            paths.extend(model_control_arg_paths(child, _path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{_path}[{index}]" if _path else f"[{index}]"
            paths.extend(model_control_arg_paths(child, _path=child_path))
    return paths


def model_control_arg_error(value: Any) -> str:
    paths = model_control_arg_paths(value)
    if not paths:
        return ""
    return "Model-proposed tool args cannot include runtime approval/control fields: " + ", ".join(paths)


def strip_model_control_args(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_model_control_args(child)
            for key, child in value.items()
            if not _is_model_control_key(str(key))
        }
    if isinstance(value, list):
        return [strip_model_control_args(child) for child in value]
    return value


def _is_model_control_key(key: str) -> bool:
    normalized = key.replace("-", "_").casefold()
    return normalized in MODEL_CONTROL_KEYS or normalized.startswith("_")
