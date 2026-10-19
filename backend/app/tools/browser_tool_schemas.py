"""Managed browser tool input contracts shared by registration and planning."""

from __future__ import annotations

from typing import Any


def input_schema(name: str) -> dict[str, Any]:
    url = {
        "type": "string",
        "description": "HTTP(S) URL of the page to access.",
    }
    session_id = {
        "type": "string",
        "description": "Optional managed browser session identifier.",
    }
    dry_run = {
        "type": "boolean",
        "description": "Preview the browser write without executing it (default true).",
    }
    session_scope = {
        "type": "object",
        "description": "Task/account-bound browser session scope; values cannot be broadened after start.",
        "properties": {
            "task_id": {"type": "string"},
            "account_id": {"type": "string"},
            "allowed_origins": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact HTTP(S) origins permitted for this session.",
            },
            "allowed_actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Browser action kinds permitted for this session.",
            },
        },
    }
    schemas: dict[str, dict[str, Any]] = {
        "browser.session_start": {
            "type": "object",
            "properties": {
                "url": url,
                "mode": {"type": "string"},
                **session_scope["properties"],
            },
            "required": [],
        },
        "browser.read_page": {
            "type": "object",
            "properties": {
                "url": url,
                "session_id": session_id,
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional maximum number of page-text characters to return.",
                },
                "selector": {
                    "type": "string",
                    "description": "Optional unique non-sensitive control selector; reads its current value.",
                },
            },
            "required": ["url"],
        },
        "browser.fill_form": {
            "type": "object",
            "properties": {
                "url": url,
                "session_id": session_id,
                "fields": {
                    "type": "object",
                    "description": "Mapping of non-sensitive field selectors or names to values.",
                    "additionalProperties": True,
                },
                "dry_run": dry_run,
            },
            "required": ["url", "fields"],
        },
        "browser.submit_form": {
            "type": "object",
            "properties": {
                "url": url,
                "session_id": session_id,
                "task_id": {"type": "string"},
                "account_id": {
                    "type": "string",
                    "description": "Explicit website/account identity bound to the approved submission.",
                },
                **session_scope["properties"],
                "selector": {
                    "type": "string",
                    "description": "Optional CSS selector for the form (default: form).",
                },
                "fields": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Optional ordinary fields to fill and submit together under one concrete approval.",
                },
                "dry_run": dry_run,
            },
            "required": ["url"],
        },
    }
    # Expose the real managed-session contract to both planner and worker.
    # Approval ids/markers are runtime-only and are never model parameters.
    action = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["navigate", "observe", "click", "fill", "submit", "scroll", "wait", "screenshot"],
            },
            "url": url,
            "selector": {"type": "string", "description": "Unique CSS selector from a fresh observation's controls."},
            "text": {"type": "string", "description": "Ordinary text for one non-sensitive field."},
            "fields": {"type": "object", "additionalProperties": {"type": "string"}},
            "selectors": {
                "type": "object",
                "maxProperties": 32,
                "additionalProperties": {"type": "string"},
                "description": ("Labeled selectors for fresh, unique receipt fields; returns extracted[label].text."),
            },
            "max_chars": {"type": "integer", "minimum": 1, "maximum": 65536},
            "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 30000},
            "delta_y": {"type": "integer"},
        },
        "required": ["kind"],
    }
    schemas["browser.act"] = {
        "type": "object",
        "properties": {"session_id": session_id, "action": action, "dry_run": dry_run},
        "required": ["action"],
    }
    for tool_name in ("browser.observe", "browser.wait_for_selector", "browser.click_element", "browser.navigate"):
        schemas[tool_name] = {
            "type": "object",
            "properties": {
                "session_id": session_id,
                **{key: value for key, value in action["properties"].items() if key != "kind"},
                "dry_run": dry_run,
            },
            "required": ["url"] if tool_name != "browser.observe" else [],
        }
    for tool_name in ("browser.session_info", "browser.session_close", "browser.events", "browser.replay_export"):
        schemas[tool_name] = {"type": "object", "properties": {"session_id": session_id}, "required": ["session_id"]}
    return schemas.get(name, {})
