from __future__ import annotations

import ntpath
from collections.abc import Mapping, Sequence
from typing import Any

from app.perception.ui_automation_elements import UIAutomationElement
from app.policy.approval_binding import hmac_digest

UI_AUTOMATION_IDENTITY_VERSION = "ui-automation-identity/v2"
UI_AUTOMATION_RESOURCE_KIND = "ui_automation_element"


def element_action_fingerprint(element: UIAutomationElement) -> dict[str, str]:
    """Bind action identity without persisting accessibility text or selectors."""

    return {
        "identity_hmac": hmac_digest(
            {
                "runtime_id": element.properties.get("runtime_id"),
                "automation_id": element.automation_id,
                "name": element.name,
                "control_type": element.control_type,
                "class_name": element.class_name,
                "process_id": element.process_id,
                "bounding_box": element.properties.get("bounding_box"),
            },
            prefix="ui-element",
        )
    }


def process_identity_fingerprint(
    *,
    process_id: int,
    created_at: float,
    executable: str,
) -> dict[str, str] | None:
    """Normalize sampled process facts into a privacy-preserving identity."""

    if process_id <= 0:
        return None
    try:
        normalized_executable = ntpath.normcase(ntpath.normpath(str(executable or "").strip()))
        created_at_microseconds = round(float(created_at) * 1_000_000)
    except (TypeError, ValueError):
        return None
    if not normalized_executable or normalized_executable == ".":
        return None
    return {
        "executable_hmac": hmac_digest(
            {"executable": normalized_executable},
            prefix="ui-process-executable",
        ),
        "instance_hmac": hmac_digest(
            {
                "process_id": process_id,
                "created_at_microseconds": created_at_microseconds,
            },
            prefix="ui-process-instance",
        ),
    }


def accessibility_identity(
    element: UIAutomationElement,
    *,
    native_window_handle: int | None,
) -> dict[str, Any]:
    """Build ephemeral HMAC material for one accessibility ancestor."""

    return {
        "runtime_id": element.properties.get("runtime_id"),
        "native_window_handle": native_window_handle,
        "name": element.name,
        "automation_id": element.automation_id,
        "control_type": element.control_type,
        "class_name": element.class_name,
        "process_id": element.process_id,
    }


def window_action_fingerprint(
    element: UIAutomationElement,
    *,
    parent_chain: Sequence[Mapping[str, Any]],
    process_identity: Mapping[str, str],
    native_window_handle: int | None,
) -> dict[str, Any]:
    """Return the persisted, privacy-preserving owning-window identity."""

    identity = accessibility_identity(
        element,
        native_window_handle=native_window_handle,
    )
    return {
        "runtime_id": element.properties.get("runtime_id"),
        "native_window_handle": native_window_handle,
        "process_id": element.process_id,
        **dict(process_identity),
        "parent_chain_depth": len(parent_chain),
        "parent_chain_hmac": hmac_digest(parent_chain, prefix="ui-parent-chain"),
        "identity_hmac": hmac_digest(identity, prefix="ui-window"),
    }


def semantic_resource_state(
    *,
    selector: Mapping[str, Any],
    element: UIAutomationElement,
    target_window: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Create the private approval state without raw accessibility labels."""

    state: dict[str, Any] = {
        "kind": UI_AUTOMATION_RESOURCE_KIND,
        "identity_version": UI_AUTOMATION_IDENTITY_VERSION,
        "selector_hmac": hmac_digest(selector, prefix="ui-selector"),
        "fingerprint": element_action_fingerprint(element),
    }
    if target_window is not None:
        state["target_window"] = dict(target_window)
    return state


def expected_approved_window_fingerprint(states: Any) -> dict[str, Any] | None:
    if not isinstance(states, list):
        return None
    for state in states:
        if not isinstance(state, dict) or state.get("kind") != UI_AUTOMATION_RESOURCE_KIND:
            continue
        if state.get("identity_version") != UI_AUTOMATION_IDENTITY_VERSION:
            continue
        target_window = state.get("target_window")
        if isinstance(target_window, dict):
            return dict(target_window)
    return None


def has_approved_semantic_resource_state(states: Any) -> bool:
    return isinstance(states, list) and any(
        isinstance(state, dict) and state.get("kind") == UI_AUTOMATION_RESOURCE_KIND for state in states
    )


def approved_window_identity(states: Any) -> tuple[dict[str, Any] | None, bool]:
    return (
        expected_approved_window_fingerprint(states),
        has_approved_semantic_resource_state(states),
    )
