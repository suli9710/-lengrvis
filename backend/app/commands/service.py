from __future__ import annotations

from typing import Any

from app.agents.code_review_agent import CodeReviewAgent
from app.commands.registry import normalize_command_name, register_builtin_commands, registry
from app.commands.schemas import CommandExecuteRequest, CommandResult
from app.context_compaction import (
    compact_session_context,
    compact_task_context,
    load_task_messages,
    manual_compact_messages,
    manual_compact_result_to_dict,
)
from app.context_management import summarize_messages
from app.core import db
from app.core.errors import AppError
from app.core.session_context import DEFAULT_SESSION_ID, SessionContext, SessionContextStore, lineage_diagnostics_from_metadata
from app.llm.registry import get_effective_settings
from app.mcp import get_mcp_registry
from app.perception.voice_input import DeterministicFallbackTranscriber
from app.policy.approval_binding import permission_policy_version
from app.policy.permissions import PermissionStore
from app.services.skill_service import list_installed_skills
from app.services.task_service import get_task, resume_task
from app.tools.workflow_tools import run_workflow


def list_commands() -> dict[str, Any]:
    commands = [command.model_dump(mode="json") for command in register_builtin_commands().list()]
    return {"commands": commands, "count": len(commands)}


async def execute_command(request: CommandExecuteRequest) -> CommandResult:
    try:
        command = registry.get(request.command)
    except KeyError as exc:
        raise _command_not_found_error(request.command) from exc
    args = dict(request.args or {})
    handler = _HANDLERS.get(command.name)
    if handler is None:
        return CommandResult(
            ok=True,
            command=command.name,
            title=command.title,
            diagnostics=[f"{command.name} is registered but has no execution handler."],
            next_action=command.next_action,
            surface=request.surface or command.surface,
        )
    result = handler(args)
    if _is_awaitable(result):
        result = await result
    if isinstance(result, CommandResult):
        if not result.title:
            result.title = command.title
        if not result.surface:
            result.surface = request.surface or command.surface
        return result
    return CommandResult(
        ok=True,
        command=command.name,
        title=command.title,
        result=dict(result or {}),
        next_action=command.next_action,
        surface=request.surface or command.surface,
    )


def _permissions(args: dict[str, Any]) -> CommandResult:  # noqa: ARG001
    store = PermissionStore()
    policy = store.get_policy()
    updated_at = store.updated_at()
    return CommandResult(
        command="/permissions",
        result={
            "policy": policy.model_dump(mode="json"),
            "permission_policy_version": permission_policy_version(updated_at),
            "editable_via": {
                "get": "GET /api/settings/permission-policy",
                "replace": "PUT /api/settings/permission-policy",
                "upsert_rule": "POST /api/settings/permission-policy/rules",
                "delete_rule": "DELETE /api/settings/permission-policy/rules/{rule_id}",
            },
        },
        diagnostics=["Command execution is read-only; it does not add, delete, allow, deny, create approvals, or consume approvals."],
        next_action="Use the existing settings permission-policy endpoints to change rules.",
        delegated_to="PermissionStore",
    )


async def _mcp(args: dict[str, Any]) -> CommandResult:  # noqa: ARG001
    settings = get_effective_settings()
    registry_ = get_mcp_registry()
    registry_.load_from_settings(settings)
    tools = await registry_.list_all_tools()
    servers = registry_.list_servers()
    return CommandResult(
        command="/mcp",
        result={
            "servers": servers,
            "server_count": len(servers),
            "tools": tools,
            "tool_count": len(tools),
        },
        diagnostics=["MCP tool execution remains delegated to the MCP tool adapter and normal tool safety review."],
        next_action="Enable or configure MCP servers in settings if no tools are discovered.",
        delegated_to="MCPRegistry",
    )


def _compact(args: dict[str, Any]) -> CommandResult:
    task_id = str(args.get("task_id") or "").strip()
    session_id = str(args.get("session_id") or "").strip()
    effective_session_id = session_id or DEFAULT_SESSION_ID
    messages = args.get("messages")
    custom_instructions = str(args.get("custom_instructions") or "")
    recent_limit = _optional_int(args.get("recent_message_limit"))
    persist_session = _bool_arg(args, "persist_session_context", True)
    persist_boundary = _bool_arg(args, "persist_agent_boundary", True)

    if task_id:
        result = compact_task_context(
            task_id,
            custom_instructions=custom_instructions,
            recent_message_limit=recent_limit,
            session_id=effective_session_id,
            persist_session_context=persist_session,
            persist_agent_boundary=persist_boundary,
        )
        next_action = "Use the returned boundary_message and persisted_message_id to continue from compacted context."
    elif isinstance(messages, list):
        if persist_session:
            result = compact_session_context(
                [item for item in messages if isinstance(item, dict)],
                custom_instructions=custom_instructions,
                recent_message_limit=recent_limit,
                session_id=effective_session_id,
            )
        else:
            result = manual_compact_messages(
                [item for item in messages if isinstance(item, dict)],
                custom_instructions=custom_instructions,
                recent_message_limit=recent_limit,
            )
        next_action = "Use the returned compacted messages as the new conversation payload."
    else:
        diagnostics_context = _load_session_context(session_id or None)
        return CommandResult(
            command="/compact",
            result={
                "accepted_args": [
                    "task_id",
                    "session_id",
                    "messages",
                    "custom_instructions",
                    "recent_message_limit",
                    "persist_session_context",
                    "persist_agent_boundary",
                ],
                "route": "POST /api/context/compact",
                "lineage": _lineage_payload(diagnostics_context),
            },
            diagnostics=["No task_id or messages were supplied, so compaction was not executed."],
            next_action="Pass task_id to compact stored task context, or messages to compact an ad hoc conversation.",
            delegated_to="context_compaction",
        )

    payload = manual_compact_result_to_dict(result)
    payload["lineage"] = _compact_result_lineage(payload, session_id=effective_session_id)
    return CommandResult(
        command="/compact",
        result=payload,
        next_action=next_action,
        delegated_to="context_compaction",
    )


def _resume(args: dict[str, Any]) -> CommandResult:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        session_id = str(args.get("session_id") or "").strip()
        boundary_id = str(args.get("boundary_id") or args.get("resumed_from_boundary_id") or "").strip()
        if session_id or boundary_id or _bool_arg(args, "include_compacted_context", False):
            context, diagnostics = _resolve_session_context(session_id=session_id or None, boundary_id=boundary_id or None)
            if context is None:
                return _session_context_not_found_result("/resume", session_id=session_id, boundary_id=boundary_id, diagnostics=diagnostics)
            compacted_context = _compacted_context_payload(context)
            return CommandResult(
                command="/resume",
                result={
                    "session_id": context.id,
                    "parent_session_id": context.parent_session_id,
                    "resumed_from_task_id": context.resumed_from_task_id,
                    "resumed_from_boundary_id": context.resumed_from_boundary_id,
                    "active_task_ids": list(context.active_task_ids),
                    "summary_anchor": context.last_summarized_message_id,
                    "lineage": _lineage_payload(context),
                    "session_context": context.context_for_planning(),
                    "compacted_context": compacted_context,
                    "has_compacted_context": bool(compacted_context.get("content")),
                },
                diagnostics=diagnostics,
                next_action="Use compacted_context as the continuity payload, or pass task_id to resume a paused task.",
                delegated_to="SessionContextStore",
            )
        tasks = db.fetch_many("tasks", limit=20)
        resumable = [task for task in tasks if str(task.get("status") or task.get("phase") or "") in {"paused", "execution"}]
        return CommandResult(
            command="/resume",
            result={"resumable_tasks": resumable, "count": len(resumable)},
            diagnostics=["No task_id supplied; task state was not changed."],
            next_action="Pass task_id to call the existing task resume path.",
            delegated_to="TaskService",
        )
    context, diagnostics = _resolve_session_context(
        session_id=str(args.get("session_id") or "").strip() or None,
        boundary_id=str(args.get("boundary_id") or args.get("resumed_from_boundary_id") or "").strip() or None,
    )
    if context is None:
        return _session_context_not_found_result(
            "/resume",
            session_id=str(args.get("session_id") or "").strip(),
            boundary_id=str(args.get("boundary_id") or args.get("resumed_from_boundary_id") or "").strip(),
            diagnostics=diagnostics,
        )
    task = resume_task(task_id)
    lineage = _lineage_payload(context)
    if not lineage.get("resumed_from_task_id"):
        lineage["resumed_from_task_id"] = task_id
    return CommandResult(
        command="/resume",
        result={
            "task": task.model_dump(mode="json"),
            "session_id": context.id,
            "parent_session_id": context.parent_session_id,
            "resumed_from_task_id": lineage.get("resumed_from_task_id", ""),
            "resumed_from_boundary_id": context.resumed_from_boundary_id,
            "active_task_ids": list(context.active_task_ids),
            "summary_anchor": context.last_summarized_message_id,
            "lineage": lineage,
        },
        diagnostics=diagnostics,
        next_action=f"Watch /api/tasks/{task_id}/timeline or the task websocket for progress.",
        delegated_to="TaskService",
    )


def _summary(args: dict[str, Any]) -> CommandResult:
    session_id = str(args.get("session_id") or "").strip()
    task_id = str(args.get("task_id") or "").strip()
    boundary_id = str(args.get("boundary_id") or args.get("resumed_from_boundary_id") or "").strip()
    messages = args.get("messages")
    context, diagnostics = _resolve_session_context(session_id=session_id or None, boundary_id=boundary_id or None)
    if context is None:
        return _session_context_not_found_result("/summary", session_id=session_id, boundary_id=boundary_id, diagnostics=diagnostics)
    updated = False
    if task_id or isinstance(messages, list):
        raw_messages = load_task_messages(task_id) if task_id else [item for item in messages or [] if isinstance(item, dict)]
        summary_messages = _messages_after_summary_anchor(raw_messages, context.last_summarized_message_id)
        if summary_messages and not _has_unclosed_tool_call(summary_messages):
            settings = get_effective_settings()
            new_summary = summarize_messages(summary_messages, settings)
            if new_summary:
                summary = _merge_summary(context.conversation_summary, new_summary)
                last_message_id = _last_message_id(summary_messages)
                store = SessionContextStore(session_id=context.id)
                store.load()
                context = store.remember_summary(
                    summary,
                    last_message_id=last_message_id,
                    token_stats={
                        "strategy": "summary",
                        "summarized_messages": len(summary_messages),
                        "last_summary_task_id": task_id,
                    },
                    resumed_from_task_id=task_id,
                )
                updated = True
        elif summary_messages:
            diagnostics.append("Summary update skipped because messages contain an unclosed tool call.")
    planning_context = context.context_for_planning()
    lineage = _lineage_payload(context)
    return CommandResult(
        command="/summary",
        result={
            "session_id": context.id,
            "parent_session_id": context.parent_session_id,
            "resumed_from_task_id": context.resumed_from_task_id,
            "resumed_from_boundary_id": context.resumed_from_boundary_id,
            "active_task_ids": list(context.active_task_ids),
            "updated": updated,
            "summary": context.conversation_summary,
            "summary_anchor": context.last_summarized_message_id,
            "last_summarized_message_id": context.last_summarized_message_id,
            "token_stats": context.token_stats,
            "compact_metadata": context.token_stats.get("compact_metadata") or {},
            "lineage": lineage,
            "session_context": planning_context,
            "compacted_context": _compacted_context_payload(context),
        },
        diagnostics=diagnostics,
        next_action="Use /resume with the same session_id to continue from compacted context.",
        delegated_to="SessionContextStore",
    )


def _skills(args: dict[str, Any]) -> CommandResult:  # noqa: ARG001
    return CommandResult(
        command="/skills",
        result=list_installed_skills(),
        diagnostics=["Imports and refreshes remain on /api/skills/import and /api/skills/refresh."],
        next_action="Use the existing skills endpoints for install or runtime registry refresh.",
        delegated_to="SkillService",
    )


def _workflows(args: dict[str, Any]) -> CommandResult:
    workflow = args.get("workflow") or args.get("definition")
    if not isinstance(workflow, dict):
        return CommandResult(
            command="/workflows",
            result={"accepted_args": ["workflow"]},
            diagnostics=["No workflow definition supplied; no preview was generated."],
            next_action="Pass a Workflow object to preview it with dry_run=true.",
            delegated_to="workflow.run",
        )
    preview = run_workflow({"workflow": workflow, "dry_run": True}, {})
    return CommandResult(
        ok=bool(preview.get("ok", False)),
        command="/workflows",
        result=preview,
        diagnostics=["Workflow execution was not performed. Cross-app workflow execution still requires approval."],
        next_action="Submit the workflow through normal agent/tool execution to request approval before running it.",
        delegated_to="workflow.run",
    )


def _review(args: dict[str, Any]) -> CommandResult:
    if "changed_files" in args:
        report = CodeReviewAgent().review(
            args.get("changed_files") or [],
            review_notes=args.get("review_notes", ""),
            test_evidence=args.get("test_evidence"),
            copied_source_flags=args.get("copied_source_flags"),
        )
        return CommandResult(
            ok=report.verdict == "allow",
            command="/review",
            result=report.model_dump(),
            next_action="Address blocking findings before merging, or attach stronger test evidence and rerun review.",
            delegated_to="CodeReviewAgent",
        )

    task_id = str(args.get("task_id") or "").strip()
    if task_id:
        get_task(task_id)
        reviews = db.fetch_many("safety_reviews", "task_id = ?", (task_id,), limit=500)
        return CommandResult(
            command="/review",
            result={"task_id": task_id, "reviews": reviews, "count": len(reviews)},
            next_action="Use changed_files plus test_evidence to run deterministic code review.",
            delegated_to="safety_reviews",
        )

    return CommandResult(
        command="/review",
        result={"accepted_args": ["changed_files", "test_evidence", "review_notes", "copied_source_flags", "task_id"]},
        diagnostics=["No changed_files or task_id supplied; no review was run."],
        next_action="Pass changed_files to run CodeReviewAgent, or task_id to inspect existing safety reviews.",
        delegated_to="CodeReviewAgent",
    )


def _voice(args: dict[str, Any]) -> CommandResult:
    audio_text = args.get("audio_text")
    if isinstance(audio_text, str):
        result = DeterministicFallbackTranscriber().transcribe(
            audio_text.encode("utf-8"),
            language=str(args.get("language") or "") or None,
        )
        return CommandResult(
            command="/voice",
            result={
                "transcript": result.text,
                "confidence": result.confidence,
                "language": result.language,
                "metadata": result.metadata,
            },
            diagnostics=["Used deterministic fallback transcription for text-like audio input."],
            next_action="Route accepted transcript text through /api/chat when the user submits it.",
            delegated_to="VoiceInput",
        )
    return CommandResult(
        command="/voice",
        result={
            "available": True,
            "transcriber": "VoiceInputProcessor",
            "auto_submit_route": "POST /api/chat",
        },
        diagnostics=["No binary audio endpoint exists in the command layer yet."],
        next_action="Use the perception voice input pipeline for real audio capture; command execution currently reports capability.",
        delegated_to="VoiceInput",
    )


_HANDLERS = {
    "/compact": _compact,
    "/mcp": _mcp,
    "/permissions": _permissions,
    "/resume": _resume,
    "/review": _review,
    "/skills": _skills,
    "/summary": _summary,
    "/voice": _voice,
    "/workflows": _workflows,
}


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return max(1, int(value))


def _bool_arg(args: dict[str, Any], key: str, default: bool) -> bool:
    if key not in args:
        return default
    value = args.get(key)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _load_session_context(session_id: str | None = None):
    return SessionContextStore(session_id=session_id or DEFAULT_SESSION_ID).load()


def _resolve_session_context(*, session_id: str | None = None, boundary_id: str | None = None) -> tuple[SessionContext | None, list[str]]:
    diagnostics: list[str] = []
    if session_id:
        context = SessionContextStore(session_id=session_id).load()
        if boundary_id and not context.matches_boundary_id(boundary_id):
            diagnostics.append(f"Requested boundary_id {boundary_id} was not found on session {context.id}; no global session fallback was used.")
            return None, diagnostics
        diagnostics.append(f"Loaded session context by explicit session_id {context.id}.")
        return context, diagnostics
    if boundary_id:
        store = SessionContextStore()
        context = store.load_by_boundary_id(boundary_id)
        if context:
            diagnostics.append(f"Loaded session context by explicit boundary_id {boundary_id}.")
            return context, diagnostics
        diagnostics.append(f"Requested boundary_id {boundary_id} was not found; no session context was loaded and no global latest fallback was used.")
        return None, diagnostics
    context = _load_session_context(None)
    diagnostics.append(f"Loaded default session context {context.id}; global latest session was not used.")
    return context, diagnostics


def _session_context_not_found_result(command: str, *, session_id: str, boundary_id: str, diagnostics: list[str]) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command,
        result={
            "session_id": session_id,
            "boundary_id": boundary_id,
            "has_compacted_context": False,
            "lineage": {
                "session_id": session_id,
                "parent_session_id": "",
                "resumed_from_task_id": "",
                "resumed_from_boundary_id": boundary_id,
                "active_task_ids": [],
                "summary_anchor": "",
                "summary_anchor_message_id": "",
                "latest_boundary_id": boundary_id,
                "latest_boundary_count": 0,
                "preserved_tail_message_count": 0,
                "preserved_tail_message_ids": [],
                "summarized_message_count": 0,
                "summary_chars": 0,
            },
        },
        diagnostics=diagnostics,
        error="session_context_not_found",
        next_action="Pass a known session_id or boundary_id from /summary or /compact diagnostics.",
        delegated_to="SessionContextStore",
    )


def _compacted_context_payload(context) -> dict[str, Any]:  # noqa: ANN001
    summary = str(context.conversation_summary or "").strip()
    compact_metadata = context.token_stats.get("compact_metadata") or {}
    lineage = _lineage_payload(context)
    preserved_messages = _preserved_segment_messages(compact_metadata if isinstance(compact_metadata, dict) else {})
    return {
        "role": "system",
        "summary": summary,
        "content": summary,
        "messages": [
            {
                "role": "system",
                "content": summary,
                "metadata": {
                    "context_boundary": "session_summary",
                    "compact_boundary": bool(summary),
                    "session_id": context.id,
                    "last_summarized_message_id": context.last_summarized_message_id,
                    "lineage": lineage,
                },
            },
            *[dict(message) for message in preserved_messages if isinstance(message, dict)],
        ]
        if summary or preserved_messages
        else [],
        "metadata": {
            "context_boundary": "session_summary",
            "compact_boundary": bool(summary),
            "summary": summary,
            "session_id": context.id,
            "last_summarized_message_id": context.last_summarized_message_id,
            "token_stats": context.token_stats,
            "compact_metadata": compact_metadata,
            "lineage": lineage,
        },
    }


def _lineage_payload(context: SessionContext) -> dict[str, Any]:
    return context.lineage_diagnostics()


def _compact_result_lineage(payload: dict[str, Any], *, session_id: str) -> dict[str, Any]:
    compact_metadata = payload.get("compact_metadata") if isinstance(payload.get("compact_metadata"), dict) else {}
    session_context = payload.get("session_context")
    if isinstance(session_context, dict):
        lineage = session_context.get("lineage")
        if isinstance(lineage, dict):
            result = dict(lineage)
            if not result.get("session_id"):
                result["session_id"] = session_id
            if not result.get("resumed_from_task_id") and payload.get("task_id"):
                result["resumed_from_task_id"] = str(payload.get("task_id") or "")
            return result
        return lineage_diagnostics_from_metadata(
            session_id=str(session_context.get("session_id") or session_id or ""),
            parent_session_id=str(session_context.get("parent_session_id") or ""),
            resumed_from_task_id=str(session_context.get("resumed_from_task_id") or payload.get("task_id") or ""),
            resumed_from_boundary_id=str(session_context.get("resumed_from_boundary_id") or ""),
            active_task_ids=list(session_context.get("active_task_ids") or []),
            summary_anchor=str(session_context.get("last_summarized_message_id") or ""),
            compact_metadata=compact_metadata,
            summary=str(payload.get("summary") or ""),
            updated_at=str(session_context.get("updated_at") or ""),
        )
    boundary = payload.get("boundary_message") if isinstance(payload.get("boundary_message"), dict) else {}
    boundary_id = str(boundary.get("id") or payload.get("persisted_message_id") or "")
    return lineage_diagnostics_from_metadata(
        session_id=session_id,
        resumed_from_task_id=str(payload.get("task_id") or ""),
        resumed_from_boundary_id=boundary_id,
        summary_anchor=str(compact_metadata.get("last_pre_compact_id") or ""),
        compact_metadata=compact_metadata,
        summary=str(payload.get("summary") or ""),
    )


def _preserved_segment_messages(compact_metadata: dict[str, Any]) -> list[dict[str, Any]]:
    preserved_segment = compact_metadata.get("preserved_segment") or compact_metadata.get("preservedSegment")
    if isinstance(preserved_segment, dict):
        messages = preserved_segment.get("messages")
        return [dict(message) for message in messages if isinstance(message, dict)] if isinstance(messages, list) else []
    if isinstance(preserved_segment, list):
        return [dict(message) for message in preserved_segment if isinstance(message, dict)]
    return []


def _messages_after_summary_anchor(messages: list[dict[str, Any]], anchor_id: str) -> list[dict[str, Any]]:
    if not anchor_id:
        return list(messages)
    for index, message in enumerate(messages):
        if str(message.get("id") or "").strip() == anchor_id:
            return list(messages[index + 1 :])
    return list(messages)


def _has_unclosed_tool_call(messages: list[dict[str, Any]]) -> bool:
    open_ids: set[str] = set()
    seen_results: set[str] = set()
    for message in messages:
        for tool_call in message.get("tool_calls") or []:
            if isinstance(tool_call, dict) and str(tool_call.get("id") or "").strip():
                open_ids.add(str(tool_call.get("id")).strip())
        if str(message.get("role") or "") == "tool" and str(message.get("tool_call_id") or "").strip():
            seen_results.add(str(message.get("tool_call_id")).strip())
    return bool(open_ids - seen_results)


def _last_message_id(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        message_id = str(message.get("id") or "").strip()
        if message_id:
            return message_id
    return ""


def _merge_summary(existing: str, new_summary: str) -> str:
    existing_text = str(existing or "").strip()
    new_text = str(new_summary or "").strip()
    if existing_text and new_text:
        return f"{existing_text}\n\n{new_text}"
    return existing_text or new_text


def _is_awaitable(value: Any) -> bool:
    return hasattr(value, "__await__")


def _command_not_found_error(name: str) -> AppError:
    return AppError("command_not_found", f"Command not registered: {normalize_command_name(name)}", status_code=404)
