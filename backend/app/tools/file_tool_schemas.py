"""JSON input contracts for built-in file tools."""

from typing import Any


def input_schema(name: str) -> dict[str, Any]:
    schemas: dict[str, dict[str, Any]] = {
        "file.search_by_name": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
        "file.search_full_text": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "max_scanned": {"type": "integer"},
                "max_file_bytes": {"type": "integer"},
                "max_chars_per_file": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "file.semantic_search": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "file.list_directory": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.get_metadata": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.hash_file": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.read_text": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.find_duplicates": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
                "max_scanned": {"type": "integer"},
                "max_file_bytes": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "file.cleanup_scan": _cleanup_plan_schema(read_only=True),
        "file.cleanup_plan": _cleanup_plan_schema(read_only=True),
        "file.dedupe_plan": _cleanup_plan_schema(read_only=True),
        "file.cleanup_execute": {
            "type": "object",
            "properties": {
                "roots": {"type": "array", "items": {"type": "string"}},
                "plan_id": {"type": "string"},
                "content_hash": {"type": "string"},
                "selected_item_ids": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean"},
                "approved": {"type": "boolean"},
                "approval_id": {"type": "string"},
                "threshold_mb": {"type": "number"},
                "older_than_days": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["roots", "plan_id", "content_hash", "selected_item_ids"],
            "additionalProperties": False,
        },
        "file.cleanup_rollback": {
            "type": "object",
            "properties": {
                "rollback_info": {"type": "object"},
                "dry_run": {"type": "boolean"},
                "approved": {"type": "boolean"},
                "approval_id": {"type": "string"},
            },
            "required": ["rollback_info"],
            "additionalProperties": False,
        },
        "file.preview_batch_operation": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "operation": {"type": "string"},
                "target_folder": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "file.create_folder": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.copy": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["source", "destination"],
            "additionalProperties": False,
        },
        "file.move": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["source", "destination"],
            "additionalProperties": False,
        },
        "file.rename": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "new_name": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["source", "new_name"],
            "additionalProperties": False,
        },
        "file.trash": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "file.write_text": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "text": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["path", "text"],
            "additionalProperties": False,
        },
        "file.edit_text": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
            "additionalProperties": False,
        },
        "file.generate_markdown_report": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }
    return schemas.get(name, {"type": "object", "properties": {}, "additionalProperties": False})


def _cleanup_plan_schema(*, read_only: bool) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "roots": {"type": "array", "items": {"type": "string"}},
            "threshold_mb": {"type": "number"},
            "older_than_days": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    if not read_only:
        schema["properties"]["dry_run"] = {"type": "boolean"}
    return schema
