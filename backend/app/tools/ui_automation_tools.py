from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.perception.ui_automation import create_ui_automation_target
from app.policy.execution_marker import execution_is_marked_approved
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition

MAX_VISION_GROUNDING_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VISION_GROUNDING_IMAGE_BASE64_CHARS = ((MAX_VISION_GROUNDING_IMAGE_BYTES + 2) // 3) * 4


def active_window(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    app_context = asyncio.run(target.active_window())
    return {"ok": bool(app_context.available), "app_context": app_context.model_dump(mode="json")}


def observe(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(
        target.observe(
            _selector_args(args),
            max_depth=int(args.get("max_depth") or args.get("maxDepth") or 2),
            max_elements=int(args.get("max_elements") or args.get("maxElements") or 200),
        )
    )


def find_element(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    element = asyncio.run(
        target.find_element(
            _selector_args(args),
        )
    )
    return {"ok": element is not None, "element": element.to_dict() if element else None}


def wait_for_element(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    element = asyncio.run(
        target.wait_for_element(
            _selector_args(args),
            timeout_seconds=float(args.get("timeout_seconds") or args.get("timeoutSeconds") or 5),
            poll_interval_seconds=float(args.get("poll_interval_seconds") or args.get("pollIntervalSeconds") or 0.25),
        )
    )
    return {"ok": element is not None, "element": element.to_dict() if element else None}


def click(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    selector = _selector_args(args)
    if args.get("dry_run", True):
        return _preview("click", selector)
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("click")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(
        target.click(
            selector,
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        )
    )


def type_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    selector = _selector_args(args)
    text = str(args.get("text") or "")
    if args.get("dry_run", True):
        return _preview("type_text", {**selector, "characters": len(text)})
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("type_text")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(
        target.type_text(
            selector,
            text,
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        )
    )


def focus(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(target.focus(_selector_args(args)))


def list_windows(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    windows = asyncio.run(target.list_windows())
    return {"ok": True, "windows": windows, "count": len(windows)}


def focus_window(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(
        target.focus_window(
            title=str(args.get("title") or ""),
            title_contains=str(args.get("title_contains") or args.get("titleContains") or ""),
            class_name=str(args.get("class_name") or args.get("className") or ""),
            process_id=_optional_int(args.get("process_id") or args.get("processId")),
            hwnd=_optional_int(args.get("hwnd")),
        )
    )


def click_at(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    x = int(args.get("x") or 0)
    y = int(args.get("y") or 0)
    detail = {
        "x": x,
        "y": y,
        "button": str(args.get("button") or "left"),
        "clicks": int(args.get("clicks") or 1),
    }
    if args.get("dry_run", True):
        return _preview("click_at", detail)
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("click_at")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(
        target.click_at(
            x,
            y,
            button=detail["button"],
            clicks=detail["clicks"],
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        )
    )


def drag(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    detail = {
        "start_x": int(args.get("start_x") or args.get("startX") or 0),
        "start_y": int(args.get("start_y") or args.get("startY") or 0),
        "end_x": int(args.get("end_x") or args.get("endX") or 0),
        "end_y": int(args.get("end_y") or args.get("endY") or 0),
        "duration_seconds": float(args.get("duration_seconds") or args.get("durationSeconds") or 0.2),
        "button": str(args.get("button") or "left"),
    }
    if args.get("dry_run", True):
        return _preview("drag", detail)
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("drag")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(
        target.drag(
            detail["start_x"],
            detail["start_y"],
            detail["end_x"],
            detail["end_y"],
            duration_seconds=detail["duration_seconds"],
            button=detail["button"],
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        )
    )


def key_press(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    key = str(args.get("key") or "")
    if not key:
        return {"ok": False, "error": "Key is required."}
    if args.get("dry_run", True):
        return _preview("key_press", {"key": key})
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("key_press")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(
        target.key_press(
            key,
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        )
    )


def hotkey(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    keys = args.get("keys") or []
    if isinstance(keys, str):
        keys = [item.strip() for item in keys.split("+") if item.strip()]
    keys = [str(key) for key in keys]
    if not keys:
        return {"ok": False, "error": "At least one key is required."}
    if args.get("dry_run", True):
        return _preview("hotkey", {"keys": keys})
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("hotkey")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(
        target.hotkey(
            keys,
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        )
    )


def screenshot(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    return asyncio.run(
        target.screenshot(
            max_width=int(args.get("max_width") or args.get("maxWidth") or 1280),
            max_height=int(args.get("max_height") or args.get("maxHeight") or 720),
            quality=int(args.get("quality") or 50),
        )
    )


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


def locate_on_screen(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Operator-style grounding chain: semantic UIA lookup, then vision fallback.

    Read-only: returns screen coordinates for the described element. Acting on
    them still goes through ui_automation.click_at, which keeps the
    dry-run + approval contract for coordinate clicks.
    """
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")))
    selector = _selector_args(args)
    description = str(args.get("target") or args.get("description") or "").strip()

    if any(selector.values()):
        element = asyncio.run(target.find_element(selector))
        if element is not None:
            payload = element.to_dict()
            center = _rect_center(payload.get("rect") or {})
            if center:
                return {
                    "ok": True,
                    "method": "uia",
                    "x": center[0],
                    "y": center[1],
                    "confidence": 1.0,
                    "element": payload,
                }

    if not description:
        return {
            "ok": False,
            "error": (
                "Semantic UIAutomation lookup found no element and no visual 'target' description "
                "was provided. Supply selector fields (name/control_type/automation_id) or a "
                "natural-language 'target' for the vision fallback."
            ),
        }

    screenshot_payload = asyncio.run(target.screenshot(max_width=1600, max_height=1000, quality=70))
    if not screenshot_payload.get("ok"):
        return {
            "ok": False,
            "error": f"Vision grounding fallback needs a screenshot, but capture failed: {screenshot_payload.get('error', 'unknown capture error')}",
        }
    return _vision_grounding(description, screenshot_payload)


def _vision_grounding(description: str, screenshot_payload: dict[str, Any]) -> dict[str, Any]:
    from app.llm.prompts import render_prompt
    from app.tools.vision_tools import _run_vision

    image_data = str(screenshot_payload.get("image") or "")
    encoded = image_data.split(",", 1)[1] if "," in image_data else image_data
    if not encoded:
        return {"ok": False, "error": "Screenshot payload contained no image data for vision grounding."}
    try:
        image_bytes = _decode_vision_grounding_image(encoded)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    prompt = render_prompt("vision_locate_element.md", {"target": description})
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(image_bytes)
        answer = _run_vision(prompt, temp_path, task="vision")
    finally:
        if temp_path is not None:
            with suppress(OSError):
                temp_path.unlink()

    parsed = _parse_grounding_answer(answer)
    if parsed is None:
        return {
            "ok": False,
            "method": "vision",
            "error": f"Vision grounding returned an unparseable answer: {answer[:300]}",
        }
    if not parsed.get("found"):
        return {
            "ok": False,
            "method": "vision",
            "error": f"Vision grounding could not find '{description}' on the current screen.",
            "confidence": float(parsed.get("confidence") or 0.0),
        }

    original_width = int(screenshot_payload.get("original_width") or screenshot_payload.get("width") or 0)
    original_height = int(screenshot_payload.get("original_height") or screenshot_payload.get("height") or 0)
    x_ratio = max(0.0, min(1.0, float(parsed.get("x_ratio") or 0.0)))
    y_ratio = max(0.0, min(1.0, float(parsed.get("y_ratio") or 0.0)))
    return {
        "ok": True,
        "method": "vision",
        "x": int(round(x_ratio * original_width)),
        "y": int(round(y_ratio * original_height)),
        "confidence": max(0.0, min(1.0, float(parsed.get("confidence") or 0.0))),
        "label": str(parsed.get("label") or ""),
        "screen_width": original_width,
        "screen_height": original_height,
    }


def _decode_vision_grounding_image(encoded: str) -> bytes:
    payload = str(encoded or "").strip()
    if len(payload) > MAX_VISION_GROUNDING_IMAGE_BASE64_CHARS:
        raise ValueError(f"Screenshot image exceeds the {MAX_VISION_GROUNDING_IMAGE_BYTES} byte limit.")
    try:
        image_bytes = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Screenshot payload is not valid base64.") from exc
    if len(image_bytes) > MAX_VISION_GROUNDING_IMAGE_BYTES:
        raise ValueError(f"Screenshot image exceeds the {MAX_VISION_GROUNDING_IMAGE_BYTES} byte limit.")
    return image_bytes


def _parse_grounding_answer(answer: str) -> dict[str, Any] | None:
    text = str(answer or "").strip()
    if not text or text.startswith("[vision unavailable") or "vision not configured" in text:
        return None
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _rect_center(rect: dict[str, Any]) -> tuple[int, int] | None:
    try:
        x = int(rect["x"])
        y = int(rect["y"])
        width = int(rect.get("width") or 0)
        height = int(rect.get("height") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    return (x + width // 2, y + height // 2)


def _selector_args(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(args.get("name") or ""),
        "name_contains": str(args.get("name_contains") or args.get("nameContains") or ""),
        "text_contains": str(args.get("text_contains") or args.get("textContains") or ""),
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


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def register(registry) -> None:
    definitions = [
        (
            "ui_automation.active_window",
            active_window,
            RiskLevel.R0_READ_ONLY,
            False,
            "Read the current foreground window and focused control.",
            ["observe", "inspect"],
        ),
        (
            "ui_automation.observe",
            observe,
            RiskLevel.R0_READ_ONLY,
            False,
            "Observe the current UIAutomation tree or a selected subtree.",
            ["observe", "inspect", "list"],
        ),
        (
            "ui_automation.find_element",
            find_element,
            RiskLevel.R0_READ_ONLY,
            False,
            "Find a semantic UIAutomation element by name/control type/automation id.",
            ["observe", "inspect"],
        ),
        (
            "ui_automation.wait_for_element",
            wait_for_element,
            RiskLevel.R0_READ_ONLY,
            False,
            "Wait until a semantic UIAutomation element appears.",
            ["observe", "wait"],
        ),
        (
            "ui_automation.click",
            click,
            RiskLevel.R2_REVERSIBLE_MODIFY,
            True,
            "Click a semantic UIAutomation element after approval.",
            ["click", "write"],
        ),
        (
            "ui_automation.type_text",
            type_text,
            RiskLevel.R2_REVERSIBLE_MODIFY,
            True,
            "Type text into a semantic UIAutomation element after approval.",
            ["type", "write"],
        ),
        (
            "ui_automation.focus",
            focus,
            RiskLevel.R1_OPEN_ONLY,
            False,
            "Focus a semantic UIAutomation element.",
            ["open", "focus"],
        ),
        (
            "ui_automation.list_windows",
            list_windows,
            RiskLevel.R0_READ_ONLY,
            False,
            "List visible desktop windows.",
            ["observe", "list"],
        ),
        (
            "ui_automation.focus_window",
            focus_window,
            RiskLevel.R1_OPEN_ONLY,
            False,
            "Bring a desktop window to the foreground.",
            ["open", "focus"],
        ),
        (
            "ui_automation.click_at",
            click_at,
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            True,
            "Click absolute screen coordinates after approval.",
            ["click", "input", "write"],
        ),
        (
            "ui_automation.drag",
            drag,
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            True,
            "Drag between absolute screen coordinates after approval.",
            ["drag", "input", "write"],
        ),
        (
            "ui_automation.key_press",
            key_press,
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            True,
            "Press a keyboard key after approval.",
            ["keyboard", "input", "write"],
        ),
        (
            "ui_automation.hotkey",
            hotkey,
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            True,
            "Press a keyboard shortcut after approval.",
            ["keyboard", "input", "write"],
        ),
        (
            "ui_automation.screenshot",
            screenshot,
            RiskLevel.R0_READ_ONLY,
            False,
            "Capture the current desktop screenshot.",
            ["observe", "screenshot"],
        ),
        (
            "ui_automation.locate_on_screen",
            locate_on_screen,
            RiskLevel.R0_READ_ONLY,
            False,
            "Locate a UI element: semantic UIAutomation lookup first, then screenshot + vision model grounding fallback; returns screen coordinates for click_at.",
            ["observe", "inspect", "screenshot"],
        ),
        (
            "ui_automation.get_property",
            get_property,
            RiskLevel.R0_READ_ONLY,
            False,
            "Read a property from a semantic UIAutomation element.",
            ["observe", "inspect"],
        ),
        (
            "ui_automation.get_children",
            get_children,
            RiskLevel.R0_READ_ONLY,
            False,
            "List children of a semantic UIAutomation element.",
            ["observe", "list"],
        ),
    ]
    for name, fn, risk, supports_dry_run, description, effects in definitions:
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
                search_hint="semantic ui automation accessibility windows app control gui desktop screen mouse keyboard",
                effects=effects,
                concurrency_safe=risk == RiskLevel.R0_READ_ONLY,
                concurrency_key="desktop_gui_input" if supports_dry_run or name in {"ui_automation.focus", "ui_automation.focus_window"} else "",
                sensitive_arg_keys=["text"] if name == "ui_automation.type_text" else [],
            )
        )
