from __future__ import annotations

import asyncio
from typing import Any

from app.perception.ui_automation import create_ui_automation_target
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


def find_element(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    element = asyncio.run(
        target.find_element(
            name=str(args.get("name") or ""),
            control_type=str(args.get("control_type") or args.get("controlType") or ""),
            automation_id=str(args.get("automation_id") or args.get("automationId") or ""),
        )
    )
    return {"ok": element is not None, "element": element.to_dict() if element else None}


def click(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    selector = _selector_args(args)
    if args.get("dry_run", True):
        return _preview("click", selector)
    if not _has_approval(args):
        return _approval_error("click")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(target.click(selector, task_id=_task_id(context), step_id=_step_id(context)))


def type_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    selector = _selector_args(args)
    text = str(args.get("text") or "")
    if args.get("dry_run", True):
        return _preview("type_text", {**selector, "characters": len(text)})
    if not _has_approval(args):
        return _approval_error("type_text")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(target.type_text(selector, text, task_id=_task_id(context), step_id=_step_id(context)))


def get_property(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    prop = str(args.get("prop") or args.get("property") or "")
    if not prop:
        return {"ok": False, "error": "Property name is required."}
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    value = asyncio.run(target.get_property(_selector_args(args), prop))
    return {"ok": value is not None, "property": prop, "value": value}


def get_children(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    children = asyncio.run(target.get_children(_selector_args(args)))
    return {"ok": True, "children": [child.to_dict() for child in children], "count": len(children)}


def _selector_args(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(args.get("name") or ""),
        "control_type": str(args.get("control_type") or args.get("controlType") or ""),
        "automation_id": str(args.get("automation_id") or args.get("automationId") or ""),
        "class_name": str(args.get("class_name") or args.get("className") or ""),
        "process_id": args.get("process_id") or args.get("processId"),
    }


def _preview(action: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "dry_run": True,
        "message": "UIAutomation semantic action preview. User approval is required before execution.",
        "diff_preview": [{"action": action, **detail}],
    }


def _has_approval(args: dict[str, Any]) -> bool:
    return bool(args.get("approved") and args.get("approval_id"))


def _approval_error(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": f"UIAutomation {action} requires an approved approval_id after dry-run preview.",
    }


def _task_id(context: dict[str, Any]) -> str:
    runtime = context.get("runtime")
    task = getattr(runtime, "task", None)
    return str(getattr(task, "id", "") or "ui_automation")


def _step_id(context: dict[str, Any]) -> str | None:
    return str(context.get("step_id") or "") or None


def register(registry) -> None:
    definitions = [
        (
            "ui_automation.find_element",
            find_element,
            RiskLevel.R0_READ_ONLY,
            False,
            "Find a semantic UIAutomation element by name/control type/automation id.",
        ),
        (
            "ui_automation.click",
            click,
            RiskLevel.R2_REVERSIBLE_MODIFY,
            True,
            "Click a semantic UIAutomation element after approval.",
        ),
        (
            "ui_automation.type_text",
            type_text,
            RiskLevel.R2_REVERSIBLE_MODIFY,
            True,
            "Type text into a semantic UIAutomation element after approval.",
        ),
        (
            "ui_automation.get_property",
            get_property,
            RiskLevel.R0_READ_ONLY,
            False,
            "Read a property from a semantic UIAutomation element.",
        ),
        (
            "ui_automation.get_children",
            get_children,
            RiskLevel.R0_READ_ONLY,
            False,
            "List children of a semantic UIAutomation element.",
        ),
    ]
    for name, fn, risk, supports_dry_run, description in definitions:
        registry.register(
            ToolDefinition(
                name=name,
                description=description,
                input_schema={"type": "object", "additionalProperties": True},
                output_schema={"type": "object"},
                risk_level=risk,
                agent_owner="ComputerAgent",
                supports_dry_run=supports_dry_run,
                requires_authorized_path=False,
                execute=fn,
                search_hint="semantic ui automation accessibility windows app control",
            )
        )
