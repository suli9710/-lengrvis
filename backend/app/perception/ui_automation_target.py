"""Async UIAutomation contract and provider-unavailable fallback."""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from typing import Any

from app.perception.schemas import AppContext
from app.perception.ui_automation_elements import UIAutomationElement, UIAutomationSelector
from app.perception.ui_automation_elements import coerce_selector as _coerce_selector


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
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any] | None = None,
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
        element: UIAutomationElement | UIAutomationSelector | dict[str, Any] | None = None,
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
