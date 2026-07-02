from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.perception.schemas import Rect, UIElement


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


def coerce_selector(
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


def selector_from_element(
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
    return coerce_selector(element)


def element_from_native(
    native: Any,
    *,
    rect_converter: Callable[[Any], dict[str, int] | None],
    text_reader: Callable[[Any], str],
    control_type_converter: Callable[[Any], str],
) -> UIAutomationElement:
    bounding_box = rect_converter(getattr(native, "CurrentBoundingRectangle", None))
    properties = {
        "is_enabled": getattr(native, "CurrentIsEnabled", None),
        "is_keyboard_focusable": getattr(native, "CurrentIsKeyboardFocusable", None),
        "is_offscreen": getattr(native, "CurrentIsOffscreen", None),
        "has_keyboard_focus": getattr(native, "CurrentHasKeyboardFocus", None),
        "bounding_box": bounding_box,
        "text": text_reader(native),
        "localized_control_type": getattr(native, "CurrentLocalizedControlType", None),
    }
    return UIAutomationElement(
        name=str(getattr(native, "CurrentName", "") or ""),
        automation_id=str(getattr(native, "CurrentAutomationId", "") or ""),
        control_type=control_type_converter(getattr(native, "CurrentControlType", "") or ""),
        class_name=str(getattr(native, "CurrentClassName", "") or ""),
        process_id=getattr(native, "CurrentProcessId", None),
        properties={key: value for key, value in properties.items() if value is not None},
        native=native,
    )


def matches_selector(element: UIAutomationElement, selector: UIAutomationSelector) -> bool:
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
    return selector_has_terms(selector)


def selector_has_terms(selector: UIAutomationSelector) -> bool:
    return any(value not in {"", None} for value in selector.as_query().values())


def rect_payload(rect: Any) -> dict[str, int] | None:
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


def control_type_name(value: Any) -> str:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    return _CONTROL_TYPE_NAMES.get(numeric, str(numeric))
