"""Model-facing schemas and runtime validation for Windows desktop tools."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache, partial
from typing import Any, Literal

from pydantic import AliasChoices, ConfigDict, Field, JsonValue, ValidationError, create_model

SELECTOR_FIELDS = (
    "name",
    "name_contains",
    "text_contains",
    "control_type",
    "automation_id",
    "class_name",
    "process_id",
)
PROPERTY_NAMES = (
    "text",
    "name",
    "Name",
    "automation_id",
    "control_type",
    "class_name",
    "process_id",
    "is_enabled",
    "is_password",
    "is_keyboard_focusable",
    "is_offscreen",
    "has_keyboard_focus",
    "bounding_box",
    "runtime_id",
    "localized_control_type",
    "CurrentName",
    "CurrentAutomationId",
    "CurrentControlType",
    "CurrentClassName",
    "CurrentProcessId",
    "CurrentIsEnabled",
    "CurrentIsPassword",
    "CurrentIsKeyboardFocusable",
    "CurrentIsOffscreen",
    "CurrentHasKeyboardFocus",
    "CurrentBoundingRectangle",
    "CurrentLocalizedControlType",
)
_SELECTED_ACTIONS = {"find_element", "wait_for_element", "click", "type_text", "focus", "get_property", "get_children"}
_WRITES = {"click", "type_text", "click_at", "drag", "key_press", "hotkey"}


def _field(annotation: Any, default: Any = None, *, alias: str = "", **kwargs: Any) -> tuple[Any, Any]:
    if alias:
        kwargs["validation_alias"] = AliasChoices(*alias.split("|"))
    return annotation, Field(default=default, **kwargs)


def _selector_fields() -> dict[str, Any]:
    descriptions = {
        "name": "Exact accessible name. Combine with process_id from list_windows to scope the application.",
        "name_contains": "Case-insensitive substring of the accessible name.",
        "text_contains": "Case-insensitive substring of the control text.",
        "control_type": "UIA control type, for example Button, Edit, Document or Window.",
        "automation_id": "Exact automation id obtained from observe or find_element.",
        "class_name": "Exact native control class name.",
    }
    result = {
        name: _field(str, alias=f"{name}|{_camel(name)}", min_length=1, max_length=4096, description=description)
        for name, description in descriptions.items()
    }
    result["process_id"] = _field(
        int, alias="process_id|processId", ge=1, description="Owning process id from list_windows."
    )
    return result


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.title() for part in rest)


@lru_cache(maxsize=32)
def _model(action: str):
    fields: dict[str, Any] = {}
    if action in _SELECTED_ACTIONS | {"observe", "locate_on_screen", "hotkey"}:
        fields.update(_selector_fields())
    if action in _WRITES:
        fields["dry_run"] = _field(
            bool, True, description="Preview by default; live input requires runtime-validated approval."
        )
    if action == "observe":
        fields.update(
            max_depth=_field(
                int, 2, alias="max_depth|maxDepth", ge=0, le=8, description="Subtree depth; 0 reads only its root."
            ),
            max_elements=_field(int, 200, alias="max_elements|maxElements", ge=1, le=1000),
        )
    if action == "find_element":
        fields["max_candidates"] = _field(int, 10, alias="max_candidates|maxCandidates", ge=1, le=100)
    if action == "wait_for_element":
        fields.update(
            timeout_seconds=_field(float, 5, alias="timeout_seconds|timeoutSeconds", ge=0, le=120),
            poll_interval_seconds=_field(float, 0.25, alias="poll_interval_seconds|pollIntervalSeconds", ge=0.05, le=2),
        )
    if action == "type_text":
        fields["text"] = _field(
            str,
            ...,
            max_length=65536,
            description=(
                "Text to enter. ValuePattern replaces the edit value; verify by reading text afterward. "
                "Keyboard fallback inserts at the selection and only allows multiline text in Document controls."
            ),
        )
    if action == "focus_window":
        fields.update(
            title=_field(str, min_length=1, description="Exact title from list_windows."),
            title_contains=_field(str, alias="title_contains|titleContains", min_length=1),
            class_name=_field(str, alias="class_name|className", min_length=1),
            process_id=_field(int, alias="process_id|processId", ge=1),
            hwnd=_field(int, ge=1, description="Exact native window handle from list_windows."),
        )
    if action in {"click_at", "drag"}:
        coordinates = ("x", "y") if action == "click_at" else ("start_x", "start_y", "end_x", "end_y")
        for name in coordinates:
            fields[name] = _field(
                int,
                ...,
                alias=f"{name}|{_camel(name)}",
                description="Absolute desktop pixel coordinate; may be negative on another monitor.",
            )
        fields["button"] = _field(Literal["left", "right", "middle"], "left")
        if action == "click_at":
            fields["clicks"] = _field(int, 1, ge=1, le=3)
        else:
            fields["duration_seconds"] = _field(float, 0.2, alias="duration_seconds|durationSeconds", ge=0, le=10)
    if action == "key_press":
        fields["key"] = _field(
            str, ..., min_length=1, max_length=64, description="A key name, such as enter, tab, esc or down."
        )
    if action == "hotkey":
        fields["keys"] = _field(
            list[str] | str,
            ...,
            min_length=1,
            max_length=128,
            description=(
                'Keys pressed together, e.g. ["ctrl", "s"] or ctrl+s. '
                "Include a selector for the target Edit/Document to bind approval and verify focus before input."
            ),
        )
    if action == "screenshot":
        fields.update(
            max_width=_field(int, 1280, alias="max_width|maxWidth", ge=1, le=7680),
            max_height=_field(int, 720, alias="max_height|maxHeight", ge=1, le=4320),
            quality=_field(int, 50, ge=10, le=95),
        )
    if action == "locate_on_screen":
        fields["target"] = _field(
            str,
            alias="target|description",
            min_length=1,
            max_length=4096,
            description="Visual description for screenshot grounding when semantic lookup fails.",
        )
    if action == "get_property":
        fields["prop"] = _field(
            Literal[PROPERTY_NAMES],
            ...,
            alias="prop|property",
            description="Read text for cross-app transfer or inspect a property. Native methods are not callable.",
        )
        fields["expected_value"] = _field(
            JsonValue,
            description=(
                "Optional exact expected JSON value. A mismatch fails the step. For cross-app text transfer, "
                "bind this to the source read's value and make verification depend on both the read and write."
            ),
        )
        fields["text_comparison"] = _field(
            Literal["exact", "normalize_newlines"],
            "exact",
            description=(
                "For a text expected_value only: exact by default. normalize_newlines treats CRLF, CR and LF "
                "as equivalent for controls that normalize line endings; all other characters stay exact. "
                "The returned value is unchanged. Verify saved files separately."
            ),
        )
    return create_model(f"UIAutomation_{action}", __config__=ConfigDict(extra="forbid", strict=True), **fields)


def _required_selection(action: str) -> tuple[str, ...]:
    if action in _SELECTED_ACTIONS:
        return SELECTOR_FIELDS
    if action == "focus_window":
        return ("title", "title_contains", "class_name", "process_id", "hwnd")
    if action == "locate_on_screen":
        return (*SELECTOR_FIELDS, "target")
    return ()


def input_schema(action: str) -> dict[str, Any]:
    schema = deepcopy(_model(action).model_json_schema())
    for field in schema.get("properties", {}).values():
        # Optional omission is valid, explicit null is not an accepted selector.
        if field.get("default", ...) is None:
            field.pop("default")
    required_selection = _required_selection(action)
    if required_selection:
        schema["anyOf"] = [{"required": [field]} for field in required_selection]
    return schema


def validate_arguments(action: str, args: dict[str, Any], context: dict[str, Any]) -> None:  # noqa: ARG001
    # These two values are inserted by the validated runtime after model-input
    # validation. They remain subject to the existing approval marker/binding.
    parameters = {key: value for key, value in args.items() if key not in {"approved", "approval_id"}}
    try:
        model = _model(action).model_validate(parameters)
    except ValidationError as exc:
        issue = exc.errors(include_input=False)[0]
        path = ".".join(str(part) for part in issue["loc"]) or "arguments"
        raise ValueError(f"Invalid ui_automation.{action} parameter '{path}': {issue['type']}.") from None
    values = model.model_dump(exclude_none=True)
    if action == "get_property" and values.get("text_comparison") == "normalize_newlines":
        if values.get("prop") != "text" or not isinstance(values.get("expected_value"), str):
            raise ValueError("Newline comparison requires prop=text and a string expected_value.")
    selection = _required_selection(action)
    if selection and not any(values.get(field) for field in selection):
        raise ValueError(f"ui_automation.{action} requires a selector: {', '.join(selection)}.")
    if action == "hotkey":
        keys = values["keys"]
        keys = keys.split("+") if isinstance(keys, str) else keys
        if not keys or len(keys) > 8 or any(not key.strip() for key in keys):
            raise ValueError("Hotkey requires between 1 and 8 nonempty key names.")


def validator(action: str):
    return partial(validate_arguments, action)


def output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "error": {"type": "string"},
            "error_code": {"type": "string"},
            "next_actions": {"type": "array", "items": {"type": "string"}},
            "element": {"type": ["object", "null"]},
            "elements": {"type": "array", "items": {"type": "object"}},
            "value": {"description": "The requested property value, including text or a bounding rectangle."},
            "verified": {
                "type": "boolean",
                "description": "Whether the observed value matches expected_value under the explicit comparison rule.",
            },
            "text_comparison": {"type": "string", "enum": ["exact", "normalize_newlines"]},
            "dry_run": {"type": "boolean"},
        },
        "required": ["ok"],
        "additionalProperties": True,
    }
