from __future__ import annotations

import asyncio
import base64
import binascii
import concurrent.futures
import json
import re
import tempfile
from collections.abc import Callable
from contextlib import suppress
from functools import wraps
from pathlib import Path
from typing import Any

from app.perception.ui_automation import UnavailableUIAutomationTarget, create_ui_automation_target
from app.perception.ui_automation_observability import (
    record_action_result,
    record_screenshot_capture_result,
)
from app.policy.execution_marker import execution_is_marked_approved
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition
from app.tools.tool_abort import ToolAbortedError, raise_if_tool_aborted

MAX_VISION_GROUNDING_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VISION_GROUNDING_IMAGE_BASE64_CHARS = ((MAX_VISION_GROUNDING_IMAGE_BYTES + 2) // 3) * 4
DEFAULT_UI_AUTOMATION_TIMEOUT_SECONDS = 30.0
_APPROVAL_ACTIONS = frozenset({"click", "type_text", "click_at", "drag", "key_press", "hotkey"})
_ToolAction = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


def is_dry_run(args: dict[str, Any]) -> bool:
    """Only an explicit JSON boolean false may request a live UI action."""

    return args.get("dry_run", True) is not False


def _observed_tool_action(action: str) -> Callable[[_ToolAction], _ToolAction]:
    """Bind one public tool attempt to the privacy-safe metrics interface."""

    def decorate(fn: _ToolAction) -> _ToolAction:
        @wraps(fn)
        def observed(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
            metric_action = f"{action}_preview" if action in _APPROVAL_ACTIONS and is_dry_run(args) else action
            try:
                result = fn(args, context)
            except ToolAbortedError:
                record_action_result(metric_action, None, terminal="aborted")
                raise
            except Exception:  # noqa: BLE001 - broad-exception-boundary: record then preserve the tool exception.
                record_action_result(metric_action, None, terminal="exception")
                raise
            return record_action_result(metric_action, result)

        return observed

    return decorate


async def _with_timeout(coro, timeout_seconds: float | None, abort_context: dict[str, Any] | None) -> Any:
    task = asyncio.create_task(coro)
    loop = asyncio.get_running_loop()
    deadline = None if timeout_seconds is None or timeout_seconds <= 0 else loop.time() + timeout_seconds
    try:
        while True:
            raise_if_tool_aborted(abort_context)
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError
            wait_for = 0.05 if remaining is None else min(0.05, remaining)
            done, _ = await asyncio.wait({task}, timeout=wait_for)
            if task in done:
                return await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def _run_ui_automation(
    coro,
    action: str,
    *,
    timeout_seconds: float | None = DEFAULT_UI_AUTOMATION_TIMEOUT_SECONDS,
    abort_context: dict[str, Any] | None = None,
) -> Any:
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(_with_timeout(coro, timeout_seconds, abort_context))
        else:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = pool.submit(asyncio.run, _with_timeout(coro, timeout_seconds, abort_context))
            try:
                guard_timeout = None if timeout_seconds is None or timeout_seconds <= 0 else timeout_seconds + 1
                result = future.result(timeout=guard_timeout)
            finally:
                if not future.done():
                    future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
    except TimeoutError:
        return record_screenshot_capture_result(
            action,
            {
                "ok": False,
                "error": f"UIAutomation {action} timed out.",
                "error_code": "ui_automation_timeout",
            },
            terminal="timeout",
        )
    except ToolAbortedError:
        record_screenshot_capture_result(action, None, terminal="aborted")
        raise
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: UIAutomation adapters should fail inline for tool callers.
        return record_screenshot_capture_result(
            action,
            {
                "ok": False,
                "error": f"UIAutomation {action} failed: {exc}",
                "error_code": "ui_automation_adapter_error",
            },
            terminal="exception",
        )
    return record_screenshot_capture_result(action, result)


@_observed_tool_action("active_window")
def active_window(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    app_context = _run_ui_automation(target.active_window(), "active_window", abort_context=context)
    if isinstance(app_context, dict):
        return app_context
    return {"ok": bool(app_context.available), "app_context": app_context.model_dump(mode="json")}


@_observed_tool_action("observe")
def observe(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(
        target.observe(
            _selector_args(args),
            max_depth=int(args.get("max_depth") or args.get("maxDepth") or 2),
            max_elements=int(args.get("max_elements") or args.get("maxElements") or 200),
        ),
        "observe",
        abort_context=context,
    )


@_observed_tool_action("find_element")
def find_element(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    result = _run_ui_automation(
        target.inspect_selector(
            _selector_args(args),
            max_candidates=int(args.get("max_candidates") or args.get("maxCandidates") or 10),
        ),
        "find_element",
        abort_context=context,
    )
    return result if isinstance(result, dict) else {"ok": False, "error": "UIAutomation inspection failed."}


@_observed_tool_action("wait_for_element")
def wait_for_element(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    if unavailable := _unavailable_target_result(target):
        return unavailable
    element = _run_ui_automation(
        target.wait_for_element(
            _selector_args(args),
            timeout_seconds=float(args.get("timeout_seconds") or args.get("timeoutSeconds") or 5),
            poll_interval_seconds=float(args.get("poll_interval_seconds") or args.get("pollIntervalSeconds") or 0.25),
        ),
        "wait_for_element",
        abort_context=context,
    )
    if isinstance(element, dict):
        return element
    return {"ok": element is not None, "element": element.to_dict() if element else None}


@_observed_tool_action("click")
def click(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    selector = _selector_args(args)
    if is_dry_run(args):
        return _semantic_preview("click", selector, context)
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("click")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(
        target.click(
            selector,
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        ),
        "click",
        abort_context=context,
    )


@_observed_tool_action("type_text")
def type_text(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    selector = _selector_args(args)
    text = str(args.get("text") or "")
    if is_dry_run(args):
        return _semantic_preview("type_text", selector, context, detail={"characters": len(text)})
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("type_text")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(
        target.type_text(
            selector,
            text,
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        ),
        "type_text",
        abort_context=context,
    )


@_observed_tool_action("focus")
def focus(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(target.focus(_selector_args(args)), "focus", abort_context=context)


@_observed_tool_action("list_windows")
def list_windows(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    if unavailable := _unavailable_target_result(target):
        return unavailable
    windows = _run_ui_automation(target.list_windows(), "list_windows", abort_context=context)
    if isinstance(windows, dict):
        return windows
    return {"ok": True, "windows": windows, "count": len(windows)}


@_observed_tool_action("focus_window")
def focus_window(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(
        target.focus_window(
            title=str(args.get("title") or ""),
            title_contains=str(args.get("title_contains") or args.get("titleContains") or ""),
            class_name=str(args.get("class_name") or args.get("className") or ""),
            process_id=_optional_int(args.get("process_id") or args.get("processId")),
            hwnd=_optional_int(args.get("hwnd")),
        ),
        "focus_window",
        abort_context=context,
    )


@_observed_tool_action("click_at")
def click_at(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    x = int(args.get("x") or 0)
    y = int(args.get("y") or 0)
    detail = {
        "x": x,
        "y": y,
        "button": str(args.get("button") or "left"),
        "clicks": int(args.get("clicks") or 1),
    }
    if is_dry_run(args):
        return _preview("click_at", detail)
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("click_at")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(
        target.click_at(
            x,
            y,
            button=detail["button"],
            clicks=detail["clicks"],
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        ),
        "click_at",
        abort_context=context,
    )


@_observed_tool_action("drag")
def drag(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    detail = {
        "start_x": int(args.get("start_x") or args.get("startX") or 0),
        "start_y": int(args.get("start_y") or args.get("startY") or 0),
        "end_x": int(args.get("end_x") or args.get("endX") or 0),
        "end_y": int(args.get("end_y") or args.get("endY") or 0),
        "duration_seconds": float(args.get("duration_seconds") or args.get("durationSeconds") or 0.2),
        "button": str(args.get("button") or "left"),
    }
    if is_dry_run(args):
        return _preview("drag", detail)
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("drag")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(
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
        ),
        "drag",
        abort_context=context,
    )


@_observed_tool_action("key_press")
def key_press(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    key = str(args.get("key") or "")
    if not key:
        return {"ok": False, "error": "Key is required."}
    if is_dry_run(args):
        return _preview("key_press", {"key": key})
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("key_press")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(
        target.key_press(
            key,
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        ),
        "key_press",
        abort_context=context,
    )


@_observed_tool_action("hotkey")
def hotkey(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    keys = args.get("keys") or []
    if isinstance(keys, str):
        keys = [item.strip() for item in keys.split("+") if item.strip()]
    keys = [str(key) for key in keys]
    if not keys:
        return {"ok": False, "error": "At least one key is required."}
    if is_dry_run(args):
        return _preview("hotkey", {"keys": keys})
    if not _has_approval(args) or not execution_is_marked_approved(context):
        return _approval_error("hotkey")
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(
        target.hotkey(
            keys,
            task_id=_task_id(context),
            step_id=_step_id(context),
            approved=bool(args.get("approved")),
            approval_id=str(args.get("approval_id") or ""),
        ),
        "hotkey",
        abort_context=context,
    )


@_observed_tool_action("screenshot")
def screenshot(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    return _run_ui_automation(
        target.screenshot(
            max_width=int(args.get("max_width") or args.get("maxWidth") or 1280),
            max_height=int(args.get("max_height") or args.get("maxHeight") or 720),
            quality=int(args.get("quality") or 50),
        ),
        "screenshot",
        abort_context=context,
    )


@_observed_tool_action("get_property")
def get_property(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    prop = str(args.get("prop") or args.get("property") or "")
    if not prop:
        return {"ok": False, "error": "Property name is required."}
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    if unavailable := _unavailable_target_result(target):
        return unavailable
    value = _run_ui_automation(
        target.get_property(_selector_args(args), prop),
        "get_property",
        abort_context=context,
    )
    if isinstance(value, dict):
        return value
    return {"ok": value is not None, "property": prop, "value": value}


@_observed_tool_action("get_children")
def get_children(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    if unavailable := _unavailable_target_result(target):
        return unavailable
    children = _run_ui_automation(
        target.get_children(_selector_args(args)),
        "get_children",
        abort_context=context,
    )
    if isinstance(children, dict):
        return children
    return {"ok": True, "children": [child.to_dict() for child in children], "count": len(children)}


@_observed_tool_action("locate_on_screen")
def locate_on_screen(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Operator-style grounding chain: semantic UIA lookup, then vision fallback.

    Read-only: returns screen coordinates for the described element. Acting on
    them still goes through ui_automation.click_at, which keeps the
    dry-run + approval contract for coordinate clicks.
    """
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    selector = _selector_args(args)
    description = str(args.get("target") or args.get("description") or "").strip()

    if any(selector.values()):
        element = _run_ui_automation(
            target.find_element(selector),
            "locate_on_screen.find_element",
            abort_context=context,
        )
        if isinstance(element, dict):
            return element
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

    if any(selector.values()) and (unavailable := _unavailable_target_result(target)) and not description:
        return unavailable
    if not description:
        return {
            "ok": False,
            "error": (
                "Semantic UIAutomation lookup found no element and no visual 'target' description "
                "was provided. Supply selector fields (name/control_type/automation_id) or a "
                "natural-language 'target' for the vision fallback."
            ),
        }

    screenshot_payload = _run_ui_automation(
        target.screenshot(max_width=1600, max_height=1000, quality=70),
        "locate_on_screen.screenshot",
        abort_context=context,
    )
    if not screenshot_payload.get("ok"):
        error = screenshot_payload.get("error", "unknown capture error")
        return {
            "ok": False,
            "available": screenshot_payload.get("available", True),
            "error_code": screenshot_payload.get("error_code", "ui_automation_screenshot_capture_failed"),
            "error": f"Vision grounding fallback needs a screenshot, but capture failed: {error}",
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
            "not_found": True,
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


def _semantic_preview(
    action: str,
    selector: dict[str, Any],
    context: dict[str, Any],
    *,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = create_ui_automation_target(policy_engine=PolicyEngine(context.get("settings")), approval_context=context)
    inspection = _run_ui_automation(
        target.inspect_selector(selector, max_candidates=10),
        f"{action}_preview",
        abort_context=context,
    )
    if not isinstance(inspection, dict) or inspection.get("ok") is not True:
        failure = inspection if isinstance(inspection, dict) else {}
        result = {
            "ok": False,
            "dry_run": True,
            "error": str(failure.get("error") or "UI target could not be resolved uniquely."),
            "selector": selector,
            "candidates": list(failure.get("candidates") or []),
        }
        if "match_count" in failure:
            result["match_count"] = int(failure.get("match_count") or 0)
        if failure.get("available") is False:
            result["available"] = False
        if failure.get("search_truncated") is True:
            result["search_truncated"] = True
        if failure.get("error_code") in {"ui_automation_timeout", "ui_automation_adapter_error"}:
            result["error_code"] = failure["error_code"]
        return result
    preview = _preview(action, {**selector, **(detail or {})})
    resource_state = inspection.get("resource_state")
    target_window = resource_state.get("target_window") if isinstance(resource_state, dict) else None
    if not isinstance(target_window, dict):
        return {
            "ok": False,
            "dry_run": True,
            "error": (
                "UI target process, parent chain, and owning window identity could not be proven; "
                "semantic action approval was not created."
            ),
            "selector": selector,
            "match_count": 1,
            "candidates": list(inspection.get("candidates") or []),
        }
    preview["_resource_state"] = [resource_state]
    return preview


def _has_approval(args: dict[str, Any]) -> bool:
    return bool(args.get("approved") and args.get("approval_id"))


def _approval_error(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "approval_required": True,
        "_approval_gate_stage": "tool_guard",
        "error": f"UIAutomation {action} requires an approved approval_id after dry-run preview.",
    }


def _unavailable_target_result(target: object) -> dict[str, Any] | None:
    if not isinstance(target, UnavailableUIAutomationTarget):
        return None
    return {"ok": False, "available": False, "error": target.reason}


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
            (
                "Locate a UI element: semantic UIAutomation lookup first, then screenshot + "
                "vision model grounding fallback; returns screen coordinates for click_at."
            ),
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
                search_hint=(
                    "semantic ui automation accessibility windows app control gui desktop screen mouse keyboard"
                ),
                effects=effects,
                concurrency_safe=risk == RiskLevel.R0_READ_ONLY,
                concurrency_key=(
                    "desktop_gui_input"
                    if supports_dry_run or name in {"ui_automation.focus", "ui_automation.focus_window"}
                    else ""
                ),
                sensitive_arg_keys=["text"] if name == "ui_automation.type_text" else [],
                tool_version="3" if name in {"ui_automation.click", "ui_automation.type_text"} else "1",
            )
        )
