from __future__ import annotations

from app.commands.schemas import CommandDefinition


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandDefinition] = {}
        self._aliases: dict[str, str] = {}

    def register(self, command: CommandDefinition) -> None:
        self._commands[command.name] = command
        for alias in command.aliases:
            self._aliases[alias] = command.name

    def get(self, name: str) -> CommandDefinition:
        normalized = normalize_command_name(name)
        resolved = self._aliases.get(normalized, normalized)
        if resolved not in self._commands:
            raise KeyError(f"Command not registered: {normalized}")
        return self._commands[resolved]

    def list(self) -> list[CommandDefinition]:
        return sorted(self._commands.values(), key=lambda command: command.name)


def normalize_command_name(name: str) -> str:
    text = str(name or "").strip()
    return text if text.startswith("/") else f"/{text}"


registry = CommandRegistry()


def register_builtin_commands() -> CommandRegistry:
    registry._commands.clear()
    registry._aliases.clear()
    for command in _builtin_commands():
        registry.register(command)
    return registry


def _builtin_commands() -> list[CommandDefinition]:
    return [
        CommandDefinition(
            name="/compact",
            summary="Compact provided messages or an existing task context through the context compaction service.",
            kind="action",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "messages": {"type": "array", "items": {"type": "object"}},
                    "custom_instructions": {"type": "string"},
                    "recent_message_limit": {"type": "integer", "minimum": 1},
                    "persist_session_context": {"type": "boolean"},
                    "persist_agent_boundary": {"type": "boolean"},
                },
                "additionalProperties": True,
            },
            related_routes=["POST /api/context/compact"],
            requires_approval=False,
            next_action="Pass task_id or messages to execute compaction; omit both for diagnostics only.",
        ),
        CommandDefinition(
            name="/mcp",
            summary="Inspect configured MCP servers and currently discoverable MCP tools.",
            related_routes=["GET /api/mcp/servers", "GET /api/mcp/tools"],
            next_action="Configure MCP servers in settings before expecting tool discovery.",
        ),
        CommandDefinition(
            name="/permissions",
            summary="Inspect the current permission policy and route callers to the existing policy endpoints for edits.",
            related_routes=[
                "GET /api/settings/permission-policy",
                "PUT /api/settings/permission-policy",
                "POST /api/settings/permission-policy/rules",
                "DELETE /api/settings/permission-policy/rules/{rule_id}",
            ],
            next_action="Use the settings permission-policy endpoints to mutate rules; command execution is read-only.",
        ),
        CommandDefinition(
            name="/resume",
            summary="Resume a paused task via the existing task route when task_id is supplied.",
            kind="action",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "session_id": {"type": "string"},
                    "include_compacted_context": {"type": "boolean"},
                },
                "additionalProperties": True,
            },
            related_routes=["POST /api/tasks/{task_id}/resume", "GET /api/tasks/{task_id}"],
            next_action="Pass task_id to resume a paused task, or session_id to load compacted session context.",
        ),
        CommandDefinition(
            name="/summary",
            summary="Return persisted session summary, compact metadata, and compacted context for resume.",
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "messages": {"type": "array", "items": {"type": "object"}},
                },
                "additionalProperties": True,
            },
            related_routes=["POST /api/commands/execute"],
            next_action="Use /resume with the same session_id to continue from compacted context.",
        ),
        CommandDefinition(
            name="/review",
            summary="Run the deterministic code review agent when change evidence is supplied; otherwise report review status.",
            input_schema={
                "type": "object",
                "properties": {
                    "changed_files": {"type": "array"},
                    "review_notes": {},
                    "test_evidence": {},
                    "copied_source_flags": {},
                    "task_id": {"type": "string"},
                },
                "additionalProperties": True,
            },
            related_routes=["GET /api/tasks/{task_id}/safety-reviews"],
            next_action="Pass changed_files and test_evidence to run CodeReviewAgent, or task_id to inspect safety reviews.",
        ),
        CommandDefinition(
            name="/skills",
            summary="List installed skills and configured skill directories.",
            related_routes=["GET /api/skills", "POST /api/skills/import", "POST /api/skills/refresh"],
            next_action="Use /api/skills/import or /api/skills/refresh for mutations.",
        ),
        CommandDefinition(
            name="/voice",
            summary="Report voice input capability and optionally transcribe text-like audio through the voice processor.",
            input_schema={
                "type": "object",
                "properties": {
                    "audio_text": {"type": "string"},
                    "language": {"type": "string"},
                },
                "additionalProperties": True,
            },
            related_routes=["POST /api/chat"],
            next_action="Attach real audio support in a future route; this command currently supports diagnostics and text-like fallback checks.",
        ),
        CommandDefinition(
            name="/workflows",
            aliases=["/workflow"],
            summary="Preview a workflow DAG through the existing workflow tool without executing cross-app actions.",
            kind="workflow",
            input_schema={
                "type": "object",
                "properties": {
                    "workflow": {"type": "object"},
                },
                "additionalProperties": True,
            },
            related_routes=["workflow.run tool"],
            requires_approval=True,
            next_action="Pass workflow to receive a dry-run preview; execution still requires the normal approval flow.",
        ),
    ]


register_builtin_commands()
