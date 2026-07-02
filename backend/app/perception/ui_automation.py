from __future__ import annotations

import asyncio
import ctypes
import hmac
import logging
import sqlite3
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.core import db
from app.core.schemas import Approval, ApprovalStatus, SafetyReview, now_iso
from app.perception.app_context import get_current_app_context
from app.perception.schemas import AppContext, Rect, UIElement
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
from app.policy.approval_binding import args_binding_hmac, permission_policy_version, preview_hmac, settings_fingerprint
from app.policy.execution_marker import execution_is_marked_approved
from app.policy.permissions import PermissionStore
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


@dataclass(slots=True)
class UIAutomationSelector:
    automation_id: str = ""
    name: str = ""
    name_contains: str = ""
    text_contains: str = ""
    control_type: str = ""
    class_name: str = ""
    process_id: int | None = None

    def as_query(self) -> dict[str, Any]:
        return {
            "automation_id": self.automation_id,
            "name": self.name,
            "name_contains": self.name_contains,
            "text_contains": self.text_contains,
            "control_type": self.control_type,
            "class_name": self.class_name,
            "process_id": self.process_id,
        }


@dataclass(slots=True)
class UIAutomationElement:
    name: str = ""
    automation_id: str = ""
    control_type: str = ""
    class_name: str = ""
    process_id: int | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    native: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "automation_id": self.automation_id,
            "control_type": self.control_type,
            "class_name": self.class_name,
            "process_id": self.process_id,
            "properties": self.properties,
        }

    def to_perception_element(self) -> UIElement:
        rect = self.properties.get("bounding_box")
        bounding_box = Rect.model_validate(rect) if isinstance(rect, dict) else None
        return UIElement(
            role=self.control_type,
            name=self.name,
            text=str(self.properties.get("text") or ""),
            bounding_box=bounding_box,
            attributes={
                "automation_id": self.automation_id,
                "class_name": self.class_name,
                "process_id": self.process_id,
                **self.properties,
            },
        )


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
        return await asyncio.to_thread(
            self._find_element_sync,
            _coerce_selector(selector, name=name, control_type=control_type, automation_id=automation_id),
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
        target_element = element if isinstance(element, UIAutomationElement) else await self.find_element(normalized)
        if target_element is None:
            return {"ok": False, "error": "UI element not found.", "selector": normalized.as_query()}
        try:
            await asyncio.to_thread(self._click_sync, target_element.native)
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
        target_element = element if isinstance(element, UIAutomationElement) else await self.find_element(normalized)
        if target_element is None:
            return {"ok": False, "error": "UI element not found.", "selector": normalized.as_query()}
        try:
            await asyncio.to_thread(self._type_text_sync, target_element.native, text)
        except _ui_action_error_types() as exc:
            return {"ok": False, "error": str(exc), "selector": normalized.as_query()}
        return {"ok": True, "action": "type_text", "characters": len(text), "element": target_element.to_dict()}

    async def focus(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]) -> dict[str, Any]:
        normalized = _selector_from_element(element)
        target_element = element if isinstance(element, UIAutomationElement) else await self.find_element(normalized)
        if target_element is None:
            return {"ok": False, "error": "UI element not found.", "selector": normalized.as_query()}
        try:
            await asyncio.to_thread(self._focus_sync, target_element.native)
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
        if args.get("approved") and args.get("approval_id"):
            review = self.policy_engine.review_tool_call(
                task_id or "ui_automation",
                step_id,
                tool_name,
                args,
                risk_level,
            )
            if review.verdict == SafetyVerdict.DENY:
                return review
            approval_error = self._approval_gate_error(task_id, step_id, tool_name, args)
            if approval_error:
                return SafetyReview(
                    task_id=task_id or "ui_automation",
                    step_id=step_id,
                    target_type="tool_call",
                    verdict=SafetyVerdict.DENY,
                    risk_level=risk_level,
                    reasons=[approval_error],
                )
            return SafetyReview(
                task_id=task_id or "ui_automation",
                step_id=step_id,
                target_type="tool_call",
                verdict=SafetyVerdict.ALLOW,
                risk_level=risk_level,
                reasons=["Approved UIAutomation action may proceed."],
            )
        return self.policy_engine.review_tool_call(
            task_id or "ui_automation",
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
        if execution_is_marked_approved(self.approval_context):
            return ""
        approval_id = str(args.get("approval_id") or "").strip()
        if not approval_id:
            return "UIAutomation live execution requires a valid approved approval_id."
        try:
            data = db.fetch_one("approvals", approval_id)
        except (sqlite3.Error, db.SensitiveRecordIntegrityError) as exc:
            return f"UIAutomation approval storage lookup failed: {exc}"
        if not data:
            return "UIAutomation approval id was not found in the approval database."
        try:
            approval = Approval.model_validate(data)
        except ValidationError as exc:
            return f"UIAutomation approval record is invalid: {exc}"
        binding_error = _ui_automation_approval_binding_error(
            approval,
            tool_name,
            args,
            context=self.approval_context,
            settings=getattr(self.policy_engine, "settings", None),
            task_id=task_id,
            step_id=step_id,
            allow_consumed=False,
        )
        if binding_error:
            return binding_error
        try:
            claimed = db.claim_approval_for_execution(approval.id, now_iso())
        except (sqlite3.Error, db.SensitiveRecordIntegrityError) as exc:
            return f"UIAutomation approval claim failed: {exc}"
        if not claimed:
            return "UIAutomation approval has already been consumed or is no longer approved."
        try:
            claimed_approval = Approval.model_validate(claimed)
        except ValidationError as exc:
            return f"UIAutomation claimed approval record is invalid: {exc}"
        return _ui_automation_approval_binding_error(
            claimed_approval,
            tool_name,
            args,
            context=self.approval_context,
            settings=getattr(self.policy_engine, "settings", None),
            task_id=task_id,
            step_id=step_id,
            allow_consumed=True,
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
    if approval.approval_type != "tool_call":
        return "UIAutomation approval is not bound to a tool call."
    if approval.status != ApprovalStatus.APPROVED:
        return f"UIAutomation approval status is {approval.status}; expected approved."
    if approval.consumed_at and not allow_consumed:
        return "UIAutomation approval has already been consumed."
    tool = _ui_automation_tool_definition(tool_name)
    if approval.tool_name != tool_name:
        return "UIAutomation approval tool name does not match this action."
    if task_id and approval.task_id != task_id:
        return "UIAutomation approval task does not match this action."
    if step_id and approval.step_id != step_id:
        return "UIAutomation approval step does not match this action."
    missing = [
        key
        for key, value in {
            "tool_name": approval.tool_name,
            "args_binding_hmac": approval.args_binding_hmac,
            "preview_hmac": approval.preview_hmac,
            "settings_fingerprint": approval.settings_fingerprint,
            "permission_policy_version": approval.permission_policy_version,
            "tool_version": approval.tool_version,
        }.items()
        if not value
    ]
    if missing:
        return f"UIAutomation approval lacks binding metadata: {', '.join(missing)}."
    if approval.risk_level and approval.risk_level != tool.risk_level.value:
        return "UIAutomation approval risk level does not match this tool."
    if approval.tool_version != getattr(tool, "tool_version", "1"):
        return "UIAutomation approval tool version does not match this tool."
    expected_args = args_binding_hmac(
        tool_name,
        _ui_automation_approval_args(args),
        task_id=approval.task_id,
        step_id=approval.step_id,
    )
    if not hmac.compare_digest(str(approval.args_binding_hmac or ""), str(expected_args or "")):
        return "UIAutomation approval arguments do not match this action."
    expected_preview = preview_hmac(approval.diff_preview)
    if not hmac.compare_digest(str(approval.preview_hmac or ""), str(expected_preview or "")):
        return "UIAutomation approval preview was modified after review."
    runtime_context = context or {}
    runtime_settings = runtime_context.get("settings") or settings
    allowed_directories = list(
        runtime_context.get("allowed_directories") or getattr(runtime_settings, "allowed_directories", []) or []
    )
    expected_settings = settings_fingerprint(runtime_settings, allowed_directories=allowed_directories)
    if not hmac.compare_digest(str(approval.settings_fingerprint or ""), str(expected_settings or "")):
        return "UIAutomation runtime settings changed after approval preview."
    expected_policy = permission_policy_version(PermissionStore().updated_at())
    if not hmac.compare_digest(str(approval.permission_policy_version or ""), str(expected_policy or "")):
        return "UIAutomation permission policy changed after approval preview."
    return ""


def _ui_automation_approval_args(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if key not in {"approved", "approval_id", "dry_run"}}


def _ui_automation_tool_definition(tool_name: str) -> Any:
    from app.tools.registry import register_all_tools, registry

    if not registry.list():
        register_all_tools()
    return registry.get(tool_name)


def _coerce_selector(
    selector: UIAutomationSelector | dict[str, Any] | None = None,
    *,
    name: str = "",
    control_type: str = "",
    automation_id: str = "",
) -> UIAutomationSelector:
    if isinstance(selector, UIAutomationSelector):
        return selector
    selector = selector or {}
    return UIAutomationSelector(
        automation_id=str(selector.get("automation_id") or selector.get("automationId") or automation_id or ""),
        name=str(selector.get("name") or name or ""),
        name_contains=str(selector.get("name_contains") or selector.get("nameContains") or ""),
        text_contains=str(selector.get("text_contains") or selector.get("textContains") or selector.get("text") or ""),
        control_type=str(selector.get("control_type") or selector.get("controlType") or control_type or ""),
        class_name=str(selector.get("class_name") or selector.get("className") or ""),
        process_id=int(selector.get("process_id") or selector.get("processId"))
        if selector.get("process_id") is not None or selector.get("processId") is not None
        else None,
    )


def _selector_from_element(
    element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
) -> UIAutomationSelector:
    if isinstance(element, UIAutomationElement):
        return UIAutomationSelector(
            automation_id=element.automation_id,
            name=element.name,
            control_type=element.control_type,
            class_name=element.class_name,
            process_id=element.process_id,
        )
    return _coerce_selector(element)


def _element_from_native(native: Any) -> UIAutomationElement:
    bounding_box = _rect_payload(getattr(native, "CurrentBoundingRectangle", None))
    value_text = _native_text(native)
    properties = {
        "is_enabled": getattr(native, "CurrentIsEnabled", None),
        "is_keyboard_focusable": getattr(native, "CurrentIsKeyboardFocusable", None),
        "is_offscreen": getattr(native, "CurrentIsOffscreen", None),
        "has_keyboard_focus": getattr(native, "CurrentHasKeyboardFocus", None),
        "bounding_box": bounding_box,
        "text": value_text,
        "localized_control_type": getattr(native, "CurrentLocalizedControlType", None),
    }
    return UIAutomationElement(
        name=str(getattr(native, "CurrentName", "") or ""),
        automation_id=str(getattr(native, "CurrentAutomationId", "") or ""),
        control_type=_control_type_name(getattr(native, "CurrentControlType", "") or ""),
        class_name=str(getattr(native, "CurrentClassName", "") or ""),
        process_id=getattr(native, "CurrentProcessId", None),
        properties={key: value for key, value in properties.items() if value is not None},
        native=native,
    )


def _matches_selector(element: UIAutomationElement, selector: UIAutomationSelector) -> bool:
    if selector.automation_id and element.automation_id != selector.automation_id:
        return False
    if selector.name and element.name != selector.name:
        return False
    if selector.name_contains and selector.name_contains.casefold() not in element.name.casefold():
        return False
    if selector.text_contains:
        text = str(element.properties.get("text") or "")
        if selector.text_contains.casefold() not in text.casefold():
            return False
    if selector.control_type and element.control_type.casefold() != selector.control_type.casefold():
        return False
    if selector.class_name and element.class_name.casefold() != selector.class_name.casefold():
        return False
    if selector.process_id is not None and element.process_id != selector.process_id:
        return False
    return any(value not in {"", None} for value in selector.as_query().values())


def _selector_has_terms(selector: UIAutomationSelector) -> bool:
    return any(value not in {"", None} for value in selector.as_query().values())


def _rect_payload(rect: Any) -> dict[str, int] | None:
    if rect is None:
        return None
    if isinstance(rect, dict):
        try:
            return Rect.model_validate(rect).model_dump()
        except ValidationError:
            return None
    left = getattr(rect, "left", None)
    top = getattr(rect, "top", None)
    right = getattr(rect, "right", None)
    bottom = getattr(rect, "bottom", None)
    if None not in {left, top, right, bottom}:
        return {
            "x": int(left),
            "y": int(top),
            "width": max(0, int(right) - int(left)),
            "height": max(0, int(bottom) - int(top)),
        }
    if isinstance(rect, list | tuple) and len(rect) >= 4:
        left, top, right, bottom = rect[:4]
        return {
            "x": int(left),
            "y": int(top),
            "width": max(0, int(right) - int(left)),
            "height": max(0, int(bottom) - int(top)),
        }
    return None


def _native_text(native: Any) -> str:
    try:
        value_pattern = native.GetCurrentPattern(10002)
        value = str(getattr(value_pattern, "CurrentValue", "") or "")
        return value or str(getattr(native, "CurrentName", "") or "")
    except _ui_action_error_types():
        return str(getattr(native, "CurrentName", "") or "")


_CONTROL_TYPE_NAMES = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
    50039: "SemanticZoom",
    50040: "AppBar",
}


def _control_type_name(value: Any) -> str:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    return _CONTROL_TYPE_NAMES.get(numeric, str(numeric))
