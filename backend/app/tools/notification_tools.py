from __future__ import annotations

from typing import Any

from app.policy.risk import RiskLevel
from app.services import notification_service
from app.tools.schemas import ToolDefinition

_MAX_TITLE_CHARS = 160
_MAX_BODY_CHARS = 2000
_SEVERITIES = {"info", "success", "warning", "error"}


def send(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "").strip()[:_MAX_TITLE_CHARS]
    body = str(args.get("body") or "").strip()[:_MAX_BODY_CHARS]
    severity = str(args.get("severity") or "info").strip().lower()
    task_id = str(args.get("task_id") or context.get("task_id") or "").strip()
    if not title or not body:
        raise ValueError("notification title and body are required")
    if severity not in _SEVERITIES:
        raise ValueError("notification severity is invalid")
    if bool(args.get("dry_run", False)):
        return {
            "ok": True,
            "dry_run": True,
            "queued": False,
            "title": title,
            "body": body,
            "severity": severity,
            "task_id": task_id,
        }
    receipt = notification_service.notify(
        title,
        body,
        task_id=task_id or None,
        severity=severity,
    )
    return {"ok": True, **receipt}


def register(registry) -> None:
    registry.register(
        ToolDefinition(
            name="notification.send",
            description="Queue a task-inbox and Windows desktop notification.",
            search_hint="notify the user after a governed task reaches a meaningful state",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": _MAX_TITLE_CHARS},
                    "body": {"type": "string", "maxLength": _MAX_BODY_CHARS},
                    "severity": {"type": "string", "enum": sorted(_SEVERITIES)},
                    "task_id": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["title", "body"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "queued": {"type": "boolean"},
                    "task_id": {"type": "string"},
                },
            },
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            agent_owner="HumanGateAgent",
            supports_dry_run=True,
            requires_authorized_path=False,
            execute=send,
            read_only=False,
            concurrency_safe=False,
            effects=["notify", "write"],
            resource_kinds=["desktop_notification"],
            trust_tier="builtin",
            sensitive_arg_keys=["body"],
        )
    )
