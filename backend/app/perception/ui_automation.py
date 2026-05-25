from __future__ import annotations

import asyncio
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.schemas import SafetyReview
from app.perception.schemas import Rect, UIElement
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict


class UIAutomationUnavailable(RuntimeError):
    """Raised when the local UIAutomation provider cannot operate."""


@dataclass(slots=True)
class UIAutomationSelector:
    automation_id: str = ""
    name: str = ""
    control_type: str = ""
    class_name: str = ""
    process_id: int | None = None

    def as_query(self) -> dict[str, Any]:
        return {
            "automation_id": self.automation_id,
            "name": self.name,
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
    async def click(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        *,
        task_id: str = "",
        step_id: str | None = None,
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
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_property(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any], prop: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def get_children(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]) -> list[UIAutomationElement]:
        raise NotImplementedError


class WindowsCOMUIAutomationTarget(UIAutomationTarget):
    """Windows COM UIAutomation adapter with graceful degradation."""

    def __init__(self, policy_engine: PolicyEngine | None = None, *, automation: Any | None = None) -> None:
        self.policy_engine = policy_engine or PolicyEngine()
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

    async def click(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        *,
        task_id: str = "",
        step_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = _selector_from_element(element)
        review = self._review_action(task_id, step_id, "ui_automation.click", {"selector": normalized.as_query()})
        if review.verdict == SafetyVerdict.DENY:
            return {"ok": False, "denied": True, "reasons": review.reasons}
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            return {"ok": False, "approval_required": True, "reasons": review.reasons}
        target_element = element if isinstance(element, UIAutomationElement) else await self.find_element(normalized)
        if target_element is None:
            return {"ok": False, "error": "UI element not found.", "selector": normalized.as_query()}
        try:
            await asyncio.to_thread(self._click_sync, target_element.native)
        except Exception as exc:  # noqa: BLE001 - COM exceptions vary by provider.
            return {"ok": False, "error": str(exc), "selector": normalized.as_query()}
        return {"ok": True, "action": "click", "element": target_element.to_dict()}

    async def type_text(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        text: str,
        *,
        task_id: str = "",
        step_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = _selector_from_element(element)
        review = self._review_action(
            task_id,
            step_id,
            "ui_automation.type_text",
            {"selector": normalized.as_query(), "text_length": len(text), "dry_run": False},
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
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "selector": normalized.as_query()}
        return {"ok": True, "action": "type_text", "characters": len(text), "element": target_element.to_dict()}

    async def get_property(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any], prop: str) -> Any:
        target_element = element if isinstance(element, UIAutomationElement) else await self.find_element(element)
        if target_element is None:
            return None
        if prop in target_element.properties:
            return target_element.properties[prop]
        return getattr(target_element.native, prop, None)

    async def get_children(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]) -> list[UIAutomationElement]:
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
        except Exception as exc:  # pragma: no cover - depends on host packages.
            self._available_error = f"comtypes is not installed or unavailable: {exc}"
            return None
        try:
            return comtypes.client.CreateObject("UIAutomationClient.CUIAutomation")
        except Exception as exc:  # pragma: no cover - depends on host COM.
            self._available_error = f"Could not create UIAutomation COM object: {exc}"
            return None

    def _find_element_sync(self, selector: UIAutomationSelector) -> UIAutomationElement | None:
        if not self.available:
            return None
        root = getattr(self._automation, "GetRootElement", lambda: None)()
        return self._find_in_tree(root, selector)

    def _find_in_tree(self, native: Any, selector: UIAutomationSelector) -> UIAutomationElement | None:
        if native is None:
            return None
        element = _element_from_native(native)
        if _matches_selector(element, selector):
            return element
        for child in self._children_sync(native):
            found = self._find_in_tree(child.native, selector)
            if found is not None:
                return found
        return None

    def _children_sync(self, native: Any) -> list[UIAutomationElement]:
        try:
            children = native.FindAll(2, self._automation.CreateTrueCondition())
        except Exception:
            return []
        length = int(getattr(children, "Length", 0) or 0)
        result: list[UIAutomationElement] = []
        for index in range(length):
            try:
                result.append(_element_from_native(children.GetElement(index)))
            except Exception:
                continue
        return result

    def _click_sync(self, native: Any) -> None:
        try:
            pattern = native.GetCurrentPattern(10000)
            pattern.Invoke()
            return
        except Exception:
            pass
        rect = getattr(native, "CurrentBoundingRectangle", None)
        if rect is not None:
            x = int((getattr(rect, "left", 0) + getattr(rect, "right", 0)) / 2)
            y = int((getattr(rect, "top", 0) + getattr(rect, "bottom", 0)) / 2)
            native.SetFocus()
            _send_mouse_click(x, y)
            return
        native.SetFocus()

    def _type_text_sync(self, native: Any, text: str) -> None:
        try:
            value_pattern = native.GetCurrentPattern(10002)
            value_pattern.SetValue(text)
            return
        except Exception:
            pass
        native.SetFocus()
        _send_text(text)

    def _review_action(self, task_id: str, step_id: str | None, tool_name: str, args: dict[str, Any]) -> SafetyReview:
        return self.policy_engine.review_tool_call(
            task_id or "ui_automation",
            step_id,
            tool_name,
            args,
            RiskLevel.R2_REVERSIBLE_MODIFY,
        )


class UnavailableUIAutomationTarget(UIAutomationTarget):
    def __init__(self, reason: str = "UIAutomation provider is unavailable.") -> None:
        self.reason = reason

    async def find_element(
        self,
        selector: UIAutomationSelector | dict[str, Any] | None = None,
        *,
        name: str = "",
        control_type: str = "",
        automation_id: str = "",
    ) -> UIAutomationElement | None:
        return None

    async def click(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        *,
        task_id: str = "",
        step_id: str | None = None,
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def type_text(
        self,
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any],
        text: str,
        *,
        task_id: str = "",
        step_id: str | None = None,
    ) -> dict[str, Any]:
        return {"ok": False, "error": self.reason, "available": False}

    async def get_property(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any], prop: str) -> Any:
        return None

    async def get_children(self, element: UIAutomationElement | UIAutomationSelector | dict[str, Any]) -> list[UIAutomationElement]:
        return []


def create_ui_automation_target(policy_engine: PolicyEngine | None = None) -> UIAutomationTarget:
    target = WindowsCOMUIAutomationTarget(policy_engine=policy_engine)
    if target.available:
        return target
    return UnavailableUIAutomationTarget(target.unavailable_reason)


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
        control_type=str(selector.get("control_type") or selector.get("controlType") or control_type or ""),
        class_name=str(selector.get("class_name") or selector.get("className") or ""),
        process_id=int(selector.get("process_id") or selector.get("processId"))
        if selector.get("process_id") is not None or selector.get("processId") is not None
        else None,
    )


def _selector_from_element(element: UIAutomationElement | UIAutomationSelector | dict[str, Any]) -> UIAutomationSelector:
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
    properties = {
        "is_enabled": getattr(native, "CurrentIsEnabled", None),
        "is_keyboard_focusable": getattr(native, "CurrentIsKeyboardFocusable", None),
    }
    return UIAutomationElement(
        name=str(getattr(native, "CurrentName", "") or ""),
        automation_id=str(getattr(native, "CurrentAutomationId", "") or ""),
        control_type=str(getattr(native, "CurrentControlType", "") or ""),
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
    if selector.control_type and element.control_type != selector.control_type:
        return False
    if selector.class_name and element.class_name != selector.class_name:
        return False
    if selector.process_id is not None and element.process_id != selector.process_id:
        return False
    return any(value not in {"", None} for value in selector.as_query().values())


def _send_mouse_click(x: int, y: int) -> None:
    try:
        import pyautogui  # type: ignore[import-not-found]
    except Exception as exc:
        raise UIAutomationUnavailable("pyautogui is required for coordinate click fallback.") from exc
    pyautogui.click(x, y)


def _send_text(text: str) -> None:
    try:
        import pyautogui  # type: ignore[import-not-found]
    except Exception as exc:
        raise UIAutomationUnavailable("pyautogui is required for text input fallback.") from exc
    pyautogui.write(text)
