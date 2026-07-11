from __future__ import annotations

import asyncio
import ctypes
import logging
import sys
import time
from abc import ABC, abstractmethod
from typing import Any

from app.core.schemas import Approval, SafetyReview
from app.perception.app_context import get_current_app_context
from app.perception.schemas import AppContext
from app.perception.ui_automation_actions import (
    UIAutomationUnavailable,
)
from app.perception.ui_automation_actions import (
    bounded_int as _bounded_int,
)
from app.perception.ui_automation_actions import (
    capture_screenshot as _capture_screenshot_sync,
)
from app.perception.ui_automation_actions import (
    focus_window as _focus_window_sync,
)
from app.perception.ui_automation_actions import (
    list_windows as _list_windows_sync,
)
from app.perception.ui_automation_actions import (
    normalize_key as _normalize_key,
)
from app.perception.ui_automation_actions import (
    normalize_mouse_button as _normalize_mouse_button,
)
from app.perception.ui_automation_actions import (
    press_key as _press_key,
)
from app.perception.ui_automation_actions import (
    send_hotkey as _send_hotkey,
)
from app.perception.ui_automation_actions import (
    send_mouse_click as _send_mouse_click,
)
from app.perception.ui_automation_actions import (
    send_mouse_drag as _send_mouse_drag,
)
from app.perception.ui_automation_actions import (
    send_text as _send_text,
)
from app.perception.ui_automation_approval import (
    approval_args as _approval_args,
)
from app.perception.ui_automation_approval import (
    approval_binding_error as _approval_binding_error,
)
from app.perception.ui_automation_approval import (
    approval_gate_error as _approval_gate,
)
from app.perception.ui_automation_approval import review_action as _review_ui_action
from app.perception.ui_automation_approval import tool_definition as _tool_definition
from app.perception.ui_automation_elements import (
    UIAutomationElement,
    UIAutomationSelector,
)
from app.perception.ui_automation_elements import (
    coerce_selector as _coerce_selector,
)
from app.perception.ui_automation_elements import (
    control_type_name as _control_type_name,
)
from app.perception.ui_automation_elements import (
    element_from_native as _convert_native_element,
)
from app.perception.ui_automation_elements import (
    matches_selector as _matches_selector,
)
from app.perception.ui_automation_elements import (
    rect_payload as _rect_payload,
)
from app.perception.ui_automation_elements import (
    selector_from_element as _selector_from_element,
)
from app.perception.ui_automation_elements import (
    selector_has_terms as _selector_has_terms,
)
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict

logger = logging.getLogger(__name__)


_UI_ACTION_ERROR_BASE = (
    UIAutomationUnavailable,
    ctypes.ArgumentError,
    OSError,
    RuntimeError,
    AttributeError,
    TypeError,
    ValueError,
)
_OPTIONAL_UI_PROVIDER_ERRORS = (ImportError, OSError, RuntimeError)


def _ui_action_error_types() -> tuple[type[BaseException], ...]:
    return (*_UI_ACTION_ERROR_BASE, *_com_exception_types(), *_pyautogui_exception_types())


def _com_exception_types() -> tuple[type[BaseException], ...]:
    try:
        import comtypes  # type: ignore[import-not-found]
    except _OPTIONAL_UI_PROVIDER_ERRORS:
        return ()
    return tuple(candidate for candidate in (getattr(comtypes, "COMError", None),) if isinstance(candidate, type))


def _ui_provider_activation_error_types() -> tuple[type[BaseException], ...]:
    return (*_OPTIONAL_UI_PROVIDER_ERRORS, *_com_exception_types())


def _element_action_fingerprint(element: UIAutomationElement) -> dict[str, Any]:
    return {
        "runtime_id": element.properties.get("runtime_id"),
        "automation_id": element.automation_id,
        "name": element.name,
        "control_type": element.control_type,
        "class_name": element.class_name,
        "process_id": element.process_id,
        "bounding_box": element.properties.get("bounding_box"),
    }


def _pyautogui_exception_types() -> tuple[type[BaseException], ...]:
    try:
        import pyautogui  # type: ignore[import-not-found]
    except _OPTIONAL_UI_PROVIDER_ERRORS:
        return ()
    candidates = (
        getattr(pyautogui, "PyAutoGUIException", None),
        getattr(pyautogui, "FailSafeException", None),
    )
    return tuple(candidate for candidate in candidates if isinstance(candidate, type))


class UIAutomationTarget(ABC):
    """Async UIAutomation contract used by app/workflow skills."""

    @abstractmethod
    async def active_window(self) -> AppContext:
        raise NotImplementedError

    @abstractmethod
    async def observe(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        max_depth: int = 2,
        max_elements: int = 200,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def find_element(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        name: str = "",
        control_type: str = "",
        automation_id: str = "",
    ) -> UIAutomationElement | None:
        raise NotImplementedError

    @abstractmethod
    async def inspect_selector(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        max_candidates: int = 10,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def wait_for_element(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.25,
    ) -> UIAutomationElement | None:
        raise NotImplementedError

    @abstractmethod
    async def click(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def type_text(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        text: str,
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def focus(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def list_windows(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def focus_window(
        self,
        *,
        title: str = "",
        title_contains: str = "",
        class_name: str = "",
        process_id: int | None = None,
        hwnd: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def click_at(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        clicks: int = 1,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        duration_seconds: float = 0.2,
        button: str = "left",
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def key_press(
        self,
        key: str,
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def hotkey(
        self,
        keys: list[str],
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def screenshot(
        self,
        *,
        max_width: int = 1280,
        max_height: int = 720,
        quality: int = 50,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_property(
        self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any], prop: str
    ) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def get_children(
        self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]
    ) -> list[UIAutomationElement]:
        raise NotImplementedError


class WindowsCOMUIAutomationTarget(UIAutomationTarget):
    """Windows COM UIAutomation adapter with graceful degradation."""

    def __init__(
        self,
        policy_engine: PolicyEngine | None = None,
        *,
        automation: Any | None = None,
        approval_context: dict[str, Any] | None = None,
    ) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
        self.approval_context = approval_context or {}
        self._automation = automation
        self._available_error = ""
        if automation is None:
            self._automation = self._create_automation()

    @property
    def available(self) -> bool:
        return self._automation is not None and not self._available_error

    @property
    def unavailable_reason(self) -> str:
        return self._available_error

    async def active_window(self) -> AppContext:
        return await asyncio.to_thread(get_current_app_context)

    async def observe(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        max_depth: int = 2,
        max_elements: int = 200,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._observe_sync,
            _coerce_selector(selector),
            _bounded_int(max_depth, default=2, minimum=0, maximum=8),
            _bounded_int(max_elements, default=200, minimum=1, maximum=1000),
        )

    async def find_element(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        name: str = "",
        control_type: str = "",
        automation_id: str = "",
    ) -> UIAutomationElement | None:
        normalized = _coerce_selector(selector, name=name, control_type=control_type, automation_id=automation_id)
        element, match_count, search_truncated = await asyncio.to_thread(self._find_unique_element_sync, normalized)
        return element if match_count == 1 and not search_truncated else None

    async def inspect_selector(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        max_candidates: int = 10,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._inspect_selector_sync,
            _coerce_selector(selector),
            _bounded_int(max_candidates, default=10, minimum=1, maximum=100),
        )

    async def wait_for_element(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.25,
    ) -> UIAutomationElement | None:
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        poll = max(0.05, min(2.0, float(poll_interval_seconds)))
        normalized = _coerce_selector(selector)
        while True:
            element = await self.find_element(normalized)
            if element is not None:
                return element
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(poll)

    async def click(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        normalized = _selector_from_element(element)
        review = self._review_action(
            task_id,
            step_id,
            "ui_automation.click",
            {
                "selector": normalized.as_query(),
                "dry_run": False,
                "approved": approved,
                "approval_id": approval_id,
            },
            RiskLevel.R2_REVERSIBLE_MODIFY,
        )
        if review.verdict == SafetyVerdict.DENY:
            return {"ok": False, "denied": True, "reasons": review.reasons}
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            return {"ok": False, "approval_required": True, "reasons": review.reasons}
        target_element, target_error = await self._resolve_action_target(element, normalized)
        if target_error:
            return target_error
        try:
            target_element = await asyncio.to_thread(
                self._click_revalidated_sync,
                target_element,
                normalized,
                True,
            )
        except _ui_action_error_types() as exc:
            return {"ok": False, "error": str(exc), "selector": normalized.as_query()}
        return {"ok": True, "action": "click", "element": target_element.to_dict()}

    async def type_text(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        text: str,
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        normalized = _selector_from_element(element)
        review = self._review_action(
            task_id,
            step_id,
            "ui_automation.type_text",
            {
                "selector": normalized.as_query(),
                "text": text,
                "text_length": len(text),
                "dry_run": False,
                "approved": approved,
                "approval_id": approval_id,
            },
            RiskLevel.R2_REVERSIBLE_MODIFY,
        )
        if review.verdict == SafetyVerdict.DENY:
            return {"ok": False, "denied": True, "reasons": review.reasons}
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            return {"ok": False, "approval_required": True, "reasons": review.reasons}
        target_element, target_error = await self._resolve_action_target(element, normalized)
        if target_error:
            return target_error
        try:
            target_element = await asyncio.to_thread(
                self._type_text_revalidated_sync,
                target_element,
                normalized,
                True,
                text,
            )
        except _ui_action_error_types() as exc:
            return {"ok": False, "error": str(exc), "selector": normalized.as_query()}
        return {"ok": True, "action": "type_text", "characters": len(text), "element": target_element.to_dict()}

    async def focus(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]) -> dict[str, Any]:
        normalized = _selector_from_element(element)
        target_element, target_error = await self._resolve_action_target(element, normalized)
        if target_error:
            return target_error
        try:
            target_element = await asyncio.to_thread(
                self._focus_revalidated_sync,
                target_element,
                normalized,
                True,
            )
        except _ui_action_error_types() as exc:
            return {"ok": False, "error": str(exc), "selector": normalized.as_query()}
        return {"ok": True, "action": "focus", "element": target_element.to_dict()}

    async def list_windows(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(_list_windows_sync)

    async def focus_window(
        self,
        *,
        title: str = "",
        title_contains: str = "",
        class_name: str = "",
        process_id: int | None = None,
        hwnd: int | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            _focus_window_sync,
            title=title,
            title_contains=title_contains,
            class_name=class_name,
            process_id=process_id,
            hwnd=hwnd,
        )

    async def click_at(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        clicks: int = 1,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        safe_clicks = _bounded_int(clicks, default=1, minimum=1, maximum=3)
        review = self._review_action(
            task_id,
            step_id,
            "ui_automation.click_at",
            {
                "x": int(x),
                "y": int(y),
                "button": _normalize_mouse_button(button),
                "clicks": safe_clicks,
                "dry_run": False,
                "approved": approved,
                "approval_id": approval_id,
            },
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        )
        if review.verdict == SafetyVerdict.DENY:
            return {"ok": False, "denied": True, "reasons": review.reasons}
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            return {"ok": False, "approval_required": True, "reasons": review.reasons}
        try:
            await asyncio.to_thread(_send_mouse_click, int(x), int(y), _normalize_mouse_button(button), safe_clicks)
        except _ui_action_error_types() as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "action": "click_at", "x": int(x), "y": int(y), "button": button, "clicks": safe_clicks}

    async def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        duration_seconds: float = 0.2,
        button: str = "left",
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        duration = max(0.0, min(5.0, float(duration_seconds)))
        button = _normalize_mouse_button(button)
        review = self._review_action(
            task_id,
            step_id,
            "ui_automation.drag",
            {
                "start_x": int(start_x),
                "start_y": int(start_y),
                "end_x": int(end_x),
                "end_y": int(end_y),
                "duration_seconds": duration,
                "button": button,
                "dry_run": False,
                "approved": approved,
                "approval_id": approval_id,
            },
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        )
        if review.verdict == SafetyVerdict.DENY:
            return {"ok": False, "denied": True, "reasons": review.reasons}
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            return {"ok": False, "approval_required": True, "reasons": review.reasons}
        try:
            await asyncio.to_thread(
                _send_mouse_drag, int(start_x), int(start_y), int(end_x), int(end_y), duration, button
            )
        except _ui_action_error_types() as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "action": "drag",
            "start": {"x": int(start_x), "y": int(start_y)},
            "end": {"x": int(end_x), "y": int(end_y)},
            "duration_seconds": duration,
            "button": button,
        }

    async def key_press(
        self,
        key: str,
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        normalized = _normalize_key(key)
        if not normalized:
            return {"ok": False, "error": "Key is required."}
        review = self._review_action(
            task_id,
            step_id,
            "ui_automation.key_press",
            {
                "key": normalized,
                "dry_run": False,
                "approved": approved,
                "approval_id": approval_id,
            },
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        )
        if review.verdict == SafetyVerdict.DENY:
            return {"ok": False, "denied": True, "reasons": review.reasons}
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            return {"ok": False, "approval_required": True, "reasons": review.reasons}
        try:
            await asyncio.to_thread(_press_key, normalized)
        except _ui_action_error_types() as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "action": "key_press", "key": normalized}

    async def hotkey(
        self,
        keys: list[str],
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        normalized = [_normalize_key(key) for key in keys if _normalize_key(key)]
        if not normalized:
            return {"ok": False, "error": "At least one key is required."}
        review = self._review_action(
            task_id,
            step_id,
            "ui_automation.hotkey",
            {
                "keys": normalized,
                "dry_run": False,
                "approved": approved,
                "approval_id": approval_id,
            },
            RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        )
        if review.verdict == SafetyVerdict.DENY:
            return {"ok": False, "denied": True, "reasons": review.reasons}
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            return {"ok": False, "approval_required": True, "reasons": review.reasons}
        try:
            await asyncio.to_thread(_send_hotkey, normalized)
        except _ui_action_error_types() as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "action": "hotkey", "keys": normalized}

    async def screenshot(
        self,
        *,
        max_width: int = 1280,
        max_height: int = 720,
        quality: int = 50,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            _capture_screenshot_sync,
            _bounded_int(max_width, default=1280, minimum=1, maximum=7680),
            _bounded_int(max_height, default=720, minimum=1, maximum=4320),
            _bounded_int(quality, default=50, minimum=10, maximum=95),
        )

    async def get_property(
        self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any], prop: str
    ) -> Any:
        target_element = element if isinstance(element, UIAutomationElement) else await self.find_element(element)
        if target_element is None:
            return None
        if prop in target_element.properties:
            return target_element.properties[prop]
        return getattr(target_element.native, prop, None)

    async def get_children(
        self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]
    ) -> list[UIAutomationElement]:
        target_element = element if isinstance(element, UIAutomationElement) else await self.find_element(element)
        if target_element is None:
            return []
        return await asyncio.to_thread(self._children_sync, target_element.native)

    def _create_automation(self) -> Any | None:
        if sys.platform != "win32":
            self._available_error = "Windows UIAutomation COM is only available on Windows."
            return None
        try:
            import comtypes.client  # type: ignore[import-not-found]
        except _OPTIONAL_UI_PROVIDER_ERRORS as exc:  # pragma: no cover - depends on host packages.
            self._available_error = f"comtypes is not installed or unavailable: {exc}"
            return None
        try:
            return comtypes.client.CreateObject("UIAutomationClient.CUIAutomation")
        except _ui_provider_activation_error_types() as exc:  # pragma: no cover - host-specific COM errors.
            self._available_error = f"Could not create UIAutomation COM object: {exc}"
            return None

    def _observe_sync(self, selector: UIAutomationSelector, max_depth: int, max_elements: int) -> dict[str, Any]:
        app_context = get_current_app_context()
        if not self.available:
            return {
                "ok": False,
                "available": False,
                "error": self.unavailable_reason or "UIAutomation provider is unavailable.",
                "app_context": app_context.model_dump(mode="json"),
                "elements": [],
                "count": 0,
            }
        native = getattr(self._automation, "GetRootElement", lambda: None)()
        if _selector_has_terms(selector):
            found = self._find_in_tree(native, selector)
            native = found.native if found is not None else None
        if native is None:
            return {
                "ok": False,
                "available": True,
                "error": "UI element not found.",
                "selector": selector.as_query(),
                "app_context": app_context.model_dump(mode="json"),
                "elements": [],
                "count": 0,
            }
        tree, flat = self._tree_sync(native, max_depth=max_depth, max_elements=max_elements)
        return {
            "ok": True,
            "available": True,
            "app_context": app_context.model_dump(mode="json"),
            "root": tree,
            "elements": flat,
            "count": len(flat),
            "truncated": len(flat) >= max_elements,
        }

    def _find_element_sync(self, selector: UIAutomationSelector) -> UIAutomationElement | None:
        if not self.available:
            return None
        root = getattr(self._automation, "GetRootElement", lambda: None)()
        return self._find_in_tree(root, selector)

    async def _resolve_action_target(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        selector: UIAutomationSelector,
    ) -> tuple[UIAutomationElement | None, dict[str, Any] | None]:
        target, match_count, search_truncated = await asyncio.to_thread(self._find_unique_element_sync, selector)
        if search_truncated:
            return None, {
                "ok": False,
                "error": "UI selector search exceeded the safe traversal limit; refine the selector.",
                "selector": selector.as_query(),
                "match_count": match_count,
                "search_truncated": True,
            }
        if match_count > 1:
            return None, {
                "ok": False,
                "error": "UI selector matched multiple elements; refine the selector before executing the action.",
                "selector": selector.as_query(),
                "match_count": match_count,
            }
        if target is None:
            return None, {"ok": False, "error": "UI element not found.", "selector": selector.as_query()}
        target_changed = isinstance(element, UIAutomationElement) and (
            _element_action_fingerprint(target) != _element_action_fingerprint(element)
        )
        if target_changed:
            return None, {
                "ok": False,
                "error": "UI target changed after lookup; action was not performed.",
                "selector": selector.as_query(),
            }
        return target, None

    def _inspect_selector_sync(self, selector: UIAutomationSelector, max_candidates: int) -> dict[str, Any]:
        if not self.available:
            return {
                "ok": False,
                "available": False,
                "error": self.unavailable_reason or "UIAutomation provider is unavailable.",
                "selector": selector.as_query(),
                "match_count": 0,
                "candidates": [],
            }
        root = getattr(self._automation, "GetRootElement", lambda: None)()
        visited = [0, 0]
        matches = self._find_matches_in_tree(root, selector, visited=visited, limit=5000)
        search_truncated = bool(visited[1])
        candidates = [item.to_dict() for item in matches[:max_candidates]]
        payload: dict[str, Any] = {
            "ok": len(matches) == 1 and not search_truncated,
            "available": True,
            "selector": selector.as_query(),
            "match_count": len(matches),
            "candidates": candidates,
            "truncated": len(matches) > max_candidates,
            "search_truncated": search_truncated,
        }
        if search_truncated:
            payload["error"] = "UI selector search exceeded the safe traversal limit; refine the selector."
        elif len(matches) == 1:
            element = matches[0]
            payload["element"] = element.to_dict()
            payload["resource_state"] = {
                "kind": "ui_automation_element",
                "selector": selector.as_query(),
                "fingerprint": _element_action_fingerprint(element),
            }
        elif matches:
            payload["error"] = "UI selector matched multiple elements; refine the selector before continuing."
        else:
            payload["error"] = "UI element not found."
        return payload

    def _find_unique_element_sync(
        self,
        selector: UIAutomationSelector,
    ) -> tuple[UIAutomationElement | None, int, bool]:
        if not self.available:
            return None, 0, False
        root = getattr(self._automation, "GetRootElement", lambda: None)()
        visited = [0, 0]
        matches = self._find_matches_in_tree(root, selector, visited=visited, limit=5000)
        search_truncated = bool(visited[1])
        return (matches[0] if len(matches) == 1 and not search_truncated else None), len(matches), search_truncated

    def _find_matches_in_tree(
        self,
        native: Any,
        selector: UIAutomationSelector,
        *,
        depth: int = 0,
        max_depth: int = 12,
        visited: list[int] | None = None,
        limit: int = 5000,
    ) -> list[UIAutomationElement]:
        if native is None:
            return []
        visited = visited if visited is not None else [0, 0]
        visited[0] += 1
        if visited[0] > limit:
            if len(visited) < 2:
                visited.append(1)
            else:
                visited[1] = 1
            return []
        element = _element_from_native(native)
        matches = [element] if _matches_selector(element, selector) else []
        if depth >= max_depth:
            return matches
        for child in self._children_sync(native):
            matches.extend(
                self._find_matches_in_tree(
                    child.native,
                    selector,
                    depth=depth + 1,
                    max_depth=max_depth,
                    visited=visited,
                    limit=limit,
                )
            )
        return matches

    def _revalidate_action_target_sync(
        self,
        expected: UIAutomationElement,
        selector: UIAutomationSelector,
        selector_based: bool,
    ) -> UIAutomationElement:
        if selector_based:
            current, match_count, search_truncated = self._find_unique_element_sync(selector)
            if search_truncated or match_count != 1 or current is None:
                raise UIAutomationUnavailable(
                    "UI target changed before execution; the selector no longer has exactly one match."
                )
        else:
            current = _element_from_native(expected.native)
        if _element_action_fingerprint(current) != _element_action_fingerprint(expected):
            raise UIAutomationUnavailable("UI target changed before execution; action was not performed.")
        if current.properties.get("is_enabled") is False:
            raise UIAutomationUnavailable("UI target is disabled; action was not performed.")
        if current.properties.get("is_offscreen") is True:
            raise UIAutomationUnavailable("UI target is offscreen; action was not performed.")
        return current

    def _click_revalidated_sync(
        self,
        expected: UIAutomationElement,
        selector: UIAutomationSelector,
        selector_based: bool,
    ) -> UIAutomationElement:
        current = self._revalidate_action_target_sync(expected, selector, selector_based)
        self._click_sync(current.native)
        return current

    def _type_text_revalidated_sync(
        self,
        expected: UIAutomationElement,
        selector: UIAutomationSelector,
        selector_based: bool,
        text: str,
    ) -> UIAutomationElement:
        current = self._revalidate_action_target_sync(expected, selector, selector_based)
        self._type_text_sync(current.native, text)
        return current

    def _focus_revalidated_sync(
        self,
        expected: UIAutomationElement,
        selector: UIAutomationSelector,
        selector_based: bool,
    ) -> UIAutomationElement:
        current = self._revalidate_action_target_sync(expected, selector, selector_based)
        self._focus_sync(current.native)
        return current

    def _find_in_tree(
        self,
        native: Any,
        selector: UIAutomationSelector,
        *,
        depth: int = 0,
        max_depth: int = 12,
        visited: list[int] | None = None,
    ) -> UIAutomationElement | None:
        if native is None:
            return None
        visited = visited if visited is not None else [0]
        visited[0] += 1
        if visited[0] > 5000:
            return None
        element = _element_from_native(native)
        if _matches_selector(element, selector):
            return element
        if depth >= max_depth:
            return None
        for child in self._children_sync(native):
            found = self._find_in_tree(child.native, selector, depth=depth + 1, max_depth=max_depth, visited=visited)
            if found is not None:
                return found
        return None

    def _tree_sync(
        self, native: Any, *, max_depth: int, max_elements: int
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        flat: list[dict[str, Any]] = []

        def visit(current: Any, depth: int) -> dict[str, Any]:
            element = _element_from_native(current)
            payload = element.to_dict()
            flat.append(payload)
            children_payload: list[dict[str, Any]] = []
            if depth < max_depth and len(flat) < max_elements:
                for child in self._children_sync(current):
                    if len(flat) >= max_elements:
                        break
                    children_payload.append(visit(child.native, depth + 1))
            payload["children"] = children_payload
            return payload

        return visit(native, 0), flat

    def _children_sync(self, native: Any) -> list[UIAutomationElement]:
        try:
            children = native.FindAll(2, self._automation.CreateTrueCondition())
        except _ui_action_error_types():
            return []
        length = int(getattr(children, "Length", 0) or 0)
        result: list[UIAutomationElement] = []
        for index in range(length):
            try:
                result.append(_element_from_native(children.GetElement(index)))
            except _ui_action_error_types():
                continue
        return result

    def _click_sync(self, native: Any) -> None:
        try:
            pattern = native.GetCurrentPattern(10000)
            pattern.Invoke()
            return
        except _ui_action_error_types() as exc:
            logger.debug("UIA invoke pattern failed, falling back to pointer click: %s", exc, exc_info=True)
        rect = getattr(native, "CurrentBoundingRectangle", None)
        if rect is not None:
            x = int((getattr(rect, "left", 0) + getattr(rect, "right", 0)) / 2)
            y = int((getattr(rect, "top", 0) + getattr(rect, "bottom", 0)) / 2)
            native.SetFocus()
            _send_mouse_click(x, y)
            return
        native.SetFocus()

    def _focus_sync(self, native: Any) -> None:
        native.SetFocus()

    def _type_text_sync(self, native: Any, text: str) -> None:
        try:
            value_pattern = native.GetCurrentPattern(10002)
            value_pattern.SetValue(text)
            return
        except _ui_action_error_types() as exc:
            logger.debug("UIA value pattern failed, falling back to keyboard input: %s", exc, exc_info=True)
        native.SetFocus()
        _send_text(text)

    def _review_action(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
        risk_level: RiskLevel,
    ) -> SafetyReview:
        return _review_ui_action(
            self.policy_engine,
            self._approval_gate_error,
            task_id,
            step_id,
            tool_name,
            args,
            risk_level,
        )

    def _approval_gate_error(
        self,
        task_id: str,
        step_id: str | None,
        tool_name: str,
        args: dict[str, Any],
    ) -> str:
        return _approval_gate(
            policy_engine=self.policy_engine,
            approval_context=self.approval_context,
            binding_validator=_ui_automation_approval_binding_error,
            task_id=task_id,
            step_id=step_id,
            tool_name=tool_name,
            args=args,
        )


class UnavailableUIAutomationTarget(UIAutomationTarget):
    def __init__(self, reason: str = "UIAutomation provider is unavailable.") -> None:
        self.reason = reason

    async def active_window(self) -> AppContext:
        return AppContext(platform=sys.platform, error=self.reason)

    async def observe(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        max_depth: int = 2,
        max_elements: int = 200,
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False, "elements": [], "count": 0}

    async def find_element(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        name: str = "",
        control_type: str = "",
        automation_id: str = "",
    ) -> UIAutomationElement | None:
        return None

    async def inspect_selector(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        max_candidates: int = 10,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "available": False,
            "error": self.reason,
            "selector": _coerce_selector(selector).as_query(),
            "match_count": 0,
            "candidates": [],
        }

    async def wait_for_element(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.25,
    ) -> UIAutomationElement | None:
        return None

    async def click(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def type_text(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        text: str,
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def focus(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def list_windows(self) -> list[dict[str, Any]]:
        return []

    async def focus_window(
        self,
        *,
        title: str = "",
        title_contains: str = "",
        class_name: str = "",
        process_id: int | None = None,
        hwnd: int | None = None,
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def click_at(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        clicks: int = 1,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        duration_seconds: float = 0.2,
        button: str = "left",
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def key_press(
        self,
        key: str,
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def hotkey(
        self,
        keys: list[str],
        *,
        task_id: str = "",
        step_id: str | None = None,
        approved: bool = False,
        approval_id: str = "",
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def screenshot(
        self,
        *,
        max_width: int = 1280,
        max_height: int = 720,
        quality: int = 50,
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def get_property(
        self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any], prop: str
    ) -> Any:
        return None

    async def get_children(
        self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]
    ) -> list[UIAutomationElement]:
        return []


def create_ui_automation_target(
    policy_engine: PolicyEngine | None = None,
    *,
    approval_context: dict[str, Any] | None = None,
) -> UIAutomationTarget:
    target = WindowsCOMUIAutomationTarget(policy_engine=policy_engine, approval_context=approval_context)
    if target.available or sys.platform == "win32":
        return target
    return UnavailableUIAutomationTarget(target.unavailable_reason)


def _ui_automation_approval_binding_error(
    approval: Approval,
    tool_name: str,
    args: dict[str, Any],
    *,
    context: dict[str, Any] | None,
    settings: Any,
    task_id: str,
    step_id: str | None,
    allow_consumed: bool,
) -> str:
    return _approval_binding_error(
        approval,
        tool_name,
        args,
        context=context,
        settings=settings,
        task_id=task_id,
        step_id=step_id,
        allow_consumed=allow_consumed,
        approval_args=_ui_automation_approval_args,
        tool_definition=_ui_automation_tool_definition,
    )


def _ui_automation_approval_args(args: dict[str, Any]) -> dict[str, Any]:
    return _approval_args(args)


def _ui_automation_tool_definition(tool_name: str) -> Any:
    return _tool_definition(tool_name)


def _element_from_native(native: Any) -> UIAutomationElement:
    return _convert_native_element(
        native,
        rect_converter=_rect_payload,
        text_reader=_native_text,
        control_type_converter=_control_type_name,
    )


def _native_text(native: Any) -> str:
    try:
        value_pattern = native.GetCurrentPattern(10002)
        value = str(getattr(value_pattern, "CurrentValue", "") or "")
        return value or str(getattr(native, "CurrentName", "") or "")
    except _ui_action_error_types():
        return str(getattr(native, "CurrentName", "") or "")
