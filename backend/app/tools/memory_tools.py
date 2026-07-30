from __future__ import annotations

import asyncio
from typing import Any

from app.agents.delegation_metadata import is_memory_non_persistence_goal
from app.agents.memory_agent import MemoryAgent
from app.policy.execution_marker import execution_is_marked_approved
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def remember(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    non_persistence_error = _non_persistence_error(context)
    if non_persistence_error:
        return non_persistence_error
    content = str(args.get("content") or "").strip()
    if not content:
        raise ValueError("memory content is required")
    kind = str(args.get("kind") or "fact").strip()
    if bool(args.get("dry_run", True)):
        return {
            "ok": True,
            "dry_run": True,
            "would_change": [
                {
                    "action": "remember",
                    "kind": kind,
                    "content_preview": content[:160],
                }
            ],
        }
    approval_error = _approval_error(context)
    if approval_error:
        return approval_error
    runtime = context.get("runtime")
    memory = asyncio.run(
        MemoryAgent(getattr(runtime, "bus", None)).remember(
            content,
            task_id=str(context.get("task_id") or "").strip(),
            kind=kind,
            source="user",
            user_confirmed=True,
        )
    )
    return {
        "ok": True,
        "dry_run": False,
        "memory_id": memory.id,
        "kind": memory.kind,
        "state": memory.state.value,
        "user_confirmed": memory.user_confirmed,
    }


def revoke(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    non_persistence_error = _non_persistence_error(context)
    if non_persistence_error:
        return non_persistence_error
    memory_id = str(args.get("memory_id") or "").strip()
    if not memory_id:
        raise ValueError("memory_id is required")
    if bool(args.get("dry_run", True)):
        return {
            "ok": True,
            "dry_run": True,
            "would_change": [{"action": "revoke", "memory_id": memory_id}],
        }
    approval_error = _approval_error(context)
    if approval_error:
        return approval_error
    memory = MemoryAgent().revoke(memory_id, reviewed_by="user")
    if memory is None:
        return {
            "ok": False,
            "dry_run": False,
            "error": "Memory not found.",
            "error_code": "MEMORY_NOT_FOUND",
        }
    return {
        "ok": True,
        "dry_run": False,
        "memory_id": memory.id,
        "state": memory.state.value,
    }


def _non_persistence_error(context: dict[str, Any]) -> dict[str, Any] | None:
    task = getattr(context.get("runtime"), "task", None)
    goal = str(getattr(task, "user_goal", "") or "")
    if not is_memory_non_persistence_goal(goal):
        return None
    return {
        "ok": False,
        "error": "The task explicitly requested that long-term memory remain unchanged.",
        "error_code": "MEMORY_NON_PERSISTENCE_REQUEST",
    }


def _approval_error(context: dict[str, Any]) -> dict[str, Any] | None:
    if execution_is_marked_approved(context):
        return None
    return {
        "ok": False,
        "error": "Live memory changes require approval after a dry-run preview.",
        "error_code": "MEMORY_APPROVAL_REQUIRED",
    }


def _validate_remember(args: dict[str, Any], _context: dict[str, Any]) -> None:
    if not str(args.get("content") or "").strip():
        raise ValueError("memory content is required")


def _validate_revoke(args: dict[str, Any], _context: dict[str, Any]) -> None:
    if not str(args.get("memory_id") or "").strip():
        raise ValueError("memory_id is required")


def register(registry) -> None:
    registry.register(
        ToolDefinition(
            name="memory.remember",
            description="Store a user-confirmed fact or preference in long-term memory.",
            search_hint="remember save store user preference long-term memory 记住 保存 偏好 记忆",
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "minLength": 1},
                    "kind": {"type": "string", "enum": ["fact", "preference"]},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["content"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            agent_owner="MemoryAgent",
            supports_dry_run=True,
            requires_authorized_path=False,
            execute=remember,
            validate_input=_validate_remember,
            read_only=False,
            concurrency_safe=False,
            concurrency_key="memory-store",
            effects=["remember", "write"],
            resource_kinds=["memory"],
            trust_tier="builtin",
            sensitive_arg_keys=["content"],
            compensation_strength="reversible",
        )
    )
    registry.register(
        ToolDefinition(
            name="memory.revoke",
            description="Revoke a user-selected long-term memory without deleting its audit record.",
            search_hint="revoke disable forget remove saved preference memory 撤销 停用 忘记 记忆 偏好",
            input_schema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "minLength": 1},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["memory_id"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            agent_owner="MemoryAgent",
            supports_dry_run=True,
            requires_authorized_path=False,
            execute=revoke,
            validate_input=_validate_revoke,
            read_only=False,
            concurrency_safe=False,
            concurrency_key="memory-store",
            effects=["revoke", "write"],
            resource_kinds=["memory"],
            trust_tier="builtin",
            compensation_strength="reversible",
        )
    )
