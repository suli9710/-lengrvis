from __future__ import annotations

import re

DATA_TABLES = frozenset(
    {
        "approvals",
        "agent_messages",
        "audit_events",
        "audit_chain_heads",
        "chat_messages",
        "document_chunks",
        "goals",
        "indexed_files",
        "llm_usage_events",
        "memories",
        "mobile_devices",
        "mobile_pairings",
        "perception_observations",
        "perception_suggestions",
        "permission_policies",
        "plans",
        "run_events",
        "runs",
        "safety_reviews",
        "scheduled_tasks",
        "session_contexts",
        "task_recordings",
        "tasks",
        "tool_calls",
        "tool_results",
        "wakeups",
    }
)
UNSAFE_WHERE_TOKENS = (";", "--", "/*", "*/", "\x00")
WHERE_CONDITION_JOINER_RE = re.compile(r"\s+AND\s+", re.IGNORECASE)
WHERE_OR_RE = re.compile(r"\bOR\b", re.IGNORECASE)
WHERE_COMPARISON_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(=|>=|>|<=|<)\s*(\?|[0-9]+)$", re.IGNORECASE)
WHERE_IN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+IN\s*\(\s*\?(?:\s*,\s*\?)*\s*\)$", re.IGNORECASE)
WHERE_NULL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+IS\s+(?:NOT\s+)?NULL$", re.IGNORECASE)
WHERE_ALLOWED_COLUMNS: dict[str, frozenset[str]] = {
    "approvals": frozenset({"id", "task_id", "step_id", "status", "created_at"}),
    "agent_messages": frozenset({"id", "task_id", "step_id", "created_at"}),
    "audit_events": frozenset({"id", "task_id", "event_type", "actor", "sequence", "created_at"}),
    "chat_messages": frozenset({"id", "created_at"}),
    "document_chunks": frozenset({"id", "file_id", "chunk_index"}),
    "goals": frozenset({"id", "scope", "parent_goal_id", "status", "depth", "created_at", "updated_at"}),
    "indexed_files": frozenset(
        {"id", "normalized_path", "sha256", "name", "extension", "size", "modified_at", "indexed_at"}
    ),
    "llm_usage_events": frozenset({"id", "provider", "model", "mode", "task", "purpose", "created_at"}),
    "memories": frozenset({"id", "kind", "task_id", "created_at", "last_used_at"}),
    "mobile_devices": frozenset({"id", "created_at", "updated_at"}),
    "mobile_pairings": frozenset({"id", "status", "created_at", "expires_at", "used_at", "updated_at"}),
    "perception_observations": frozenset({"id", "task_id", "event_id", "event_type", "suppressed", "created_at"}),
    "perception_suggestions": frozenset(
        {"id", "task_id", "suggestion_id", "status", "severity", "suppressed", "created_at"}
    ),
    "permission_policies": frozenset({"id", "updated_at"}),
    "plans": frozenset({"id", "task_id", "created_at"}),
    "run_events": frozenset({"id", "run_id", "name", "sequence", "created_at"}),
    "runs": frozenset({"id", "task_id", "engine", "phase", "created_at", "updated_at"}),
    "safety_reviews": frozenset({"id", "task_id", "step_id", "created_at"}),
    "scheduled_tasks": frozenset({"id", "enabled", "next_run_at", "last_run_at", "created_at", "updated_at"}),
    "session_contexts": frozenset({"id", "created_at", "updated_at"}),
    "task_recordings": frozenset({"id", "task_id", "step_id", "phase", "captured_at", "created_at"}),
    "tasks": frozenset({"id", "created_at", "updated_at"}),
    "tool_calls": frozenset({"id", "task_id", "step_id", "created_at"}),
    "tool_results": frozenset({"id", "tool_call_id", "created_at"}),
    "wakeups": frozenset({"id", "source", "source_id", "status", "due_at", "created_at", "updated_at"}),
}

# P1-6 fix: Validate column names in _ensure_columns to prevent SQL injection.
# ALTER TABLE ... ADD COLUMN does not support parameterized identifiers in SQLite.
# We validate the column name and definition against strict whitelists instead.
_ENSURE_COLUMNS_TABLES = frozenset(
    {
        "audit_events",
        "llm_usage_events",
        "perception_suggestions",
    }
)
_SAFE_COLUMN_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_COLUMN_DEFINITION_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\s+NOT\s+NULL)?(\s+DEFAULT\s+('(?:[^']|'')*'|[0-9.-]+|NULL))?(\s+NOT\s+NULL)?$",
    re.IGNORECASE,
)
