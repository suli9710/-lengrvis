from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Coroutine
from typing import Any

from app.agents.delegation_metadata import build_task_delegation_metadata
from app.agents.delegation_rules import FILE_ACTION_TERMS, UNINSTALL_TERMS, contains_any
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.path_detection import find_explicit_path
from app.agents.supervisor_agent import SupervisorAgent, SupervisorDecision
from app.core import db
from app.core.audit import record
from app.core.schemas import ChatMessage, ChatResponse, OpenAIMessageRole, RunEngine, RunPhase, Task, TaskStatus
from app.orchestration.engine_router import route_engine
from app.orchestration.orchestrator_registry import orchestrator_registry
from app.orchestration.state_machine import safe_transition
from app.orchestration.task_phase import TaskPhase
from app.services.task_pool import get_pool

# The event loop only keeps weak references to tasks; fire-and-forget tasks
# must be held here until done or they can be garbage collected mid-run.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_background(coro: Coroutine[Any, Any, Any]) -> None:
    spawned = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(spawned)
    spawned.add_done_callback(_BACKGROUND_TASKS.discard)


async def create_task(message: str, mode: str) -> ChatResponse:
    orchestrator = OrchestratorAgent()
    task = await orchestrator.handle_user_goal(message, mode)
    orchestrator_registry.bind(task_id=task.id, orchestrator=orchestrator)
    return ChatResponse(
        task_id=task.id,
        status=task.status,
        message="我已经把这件事分配给对应 Agent 处理，结果会持续同步到任务时间线。",
        delegated=True,
        agent="OrchestratorAgent",
    )


async def handle_chat(message: str, mode: str) -> ChatResponse:
    user_message = ChatMessage(role=OpenAIMessageRole.USER, author="你", content=message)
    db.upsert_model("chat_messages", user_message)

    route = route_engine(message, "auto")
    if route.rule == "system_diagnostics":
        return await _delegate_system_diagnostics_run(message, mode)

    supervisor = SupervisorAgent()
    decision = await supervisor.decide(message, mode)
    # Deterministic safety overrides for explicit destructive intents. These run
    # regardless of decision.delegate: an LLM that delegates an explicit
    # "delete C:\\..." to the wrong worker (e.g. BrowserAgent) must still be
    # corrected to FileAgent, not just rescued when it declined to delegate.
    if _is_explicit_file_path_request(message) and decision.agent_hint != "FileAgent":
        decision = SupervisorDecision(
            delegate=True,
            reply=supervisor._delegation_reply("FileAgent", message.lower()),
            agent_hint="FileAgent",
        )
    if _is_uninstall_request(message) and decision.agent_hint != "AppAgent":
        decision = SupervisorDecision(
            delegate=True,
            reply=supervisor._delegation_reply("AppAgent", message.lower()),
            agent_hint="AppAgent",
        )
    if not decision.delegate:
        assistant_message = ChatMessage(
            role=OpenAIMessageRole.ASSISTANT,
            author="主管 Agent",
            content=decision.reply,
        )
        db.upsert_model("chat_messages", assistant_message)
        return ChatResponse(message=decision.reply, delegated=False, agent="SupervisorAgent")

    return await _delegate_task(message, mode, decision)


async def _delegate_system_diagnostics_run(message: str, mode: str) -> ChatResponse:
    from app.services import run_service

    run = await run_service.create_run(message, mode, RunEngine.AUTO, agent_hint="ComputerAgent")
    if not run.task_id:
        # create_run failed or the backend is not accepting new runs: no task
        # exists, so do not alias the run id as a task_id (clients would 404 on
        # /api/tasks/{id}). Surface an honest non-delegated error instead.
        fail_reply = "抱歉，系统诊断任务暂时无法启动，请稍后再试。"
        db.upsert_model(
            "chat_messages",
            ChatMessage(role=OpenAIMessageRole.ASSISTANT, author="主管 Agent", content=fail_reply),
        )
        return ChatResponse(task_id=None, status=None, message=fail_reply, delegated=False, agent="ComputerAgent")
    reply = "好的，这个任务和电脑/系统有关，我将分配给电脑 Agent。"
    assistant_message = ChatMessage(
        role=OpenAIMessageRole.ASSISTANT,
        author="主管 Agent",
        content=reply,
    )
    db.upsert_model("chat_messages", assistant_message)
    return ChatResponse(
        task_id=run.task_id,
        status=_task_phase_from_run_phase(run.phase),
        message=reply,
        delegated=True,
        agent="ComputerAgent",
    )


def _task_phase_from_run_phase(phase: RunPhase) -> TaskPhase:
    if phase == RunPhase.COMPLETED:
        return TaskPhase.COMPLETED
    if phase in {RunPhase.FAILED, RunPhase.DENIED}:
        return TaskPhase.FAILED
    if phase == RunPhase.CANCELLED:
        return TaskPhase.CANCELLED
    if phase in {RunPhase.RUNNING, RunPhase.AWAITING_APPROVAL, RunPhase.PAUSED}:
        return TaskPhase.EXECUTION
    return TaskPhase.PLANNING


# URLs like "https://host/path" contain a "s://..." substring that
# WINDOWS_PATH_RE ("[A-Za-z]:[\\/]...") matches, which would misclassify a
# browser request as an explicit local-file-path request. Strip URLs first.
_URL_RE = re.compile(r"[a-z][a-z0-9+.-]*://\S*", re.IGNORECASE)


def _is_explicit_file_path_request(message: str) -> bool:
    normalized = message.lower()
    without_urls = _URL_RE.sub(" ", message)
    return find_explicit_path(without_urls) is not None and contains_any(normalized, FILE_ACTION_TERMS)


def _is_uninstall_request(message: str) -> bool:
    normalized = message.lower()
    return contains_any(normalized, UNINSTALL_TERMS)


async def _delegate_task(
    message: str, mode: str, decision: SupervisorDecision, *, metadata: dict[str, Any] | None = None
) -> ChatResponse:
    orchestrator = OrchestratorAgent()
    merged_metadata = build_task_delegation_metadata(agent_hint=decision.agent_hint, extra=metadata)
    agent_hint = str(merged_metadata.get("supervisor_agent_hint") or "")
    task = orchestrator.create_task_shell(message, mode, metadata=merged_metadata)
    orchestrator_registry.bind(task_id=task.id, orchestrator=orchestrator)
    record(
        "supervisor.decision",
        "SupervisorAgent",
        {
            "delegate": True,
            "reply": decision.reply,
            "agent_hint": decision.agent_hint or "OrchestratorAgent",
            "mode": mode,
            "goal": message,
        },
        task_id=task.id,
    )
    # Callers (handle_chat, mobile task routes) are async handlers, so a
    # running loop is guaranteed; all runs go through the TaskPool to respect
    # its concurrency bound.
    _spawn_background(get_pool().submit(task, _run_task_through_orchestrator))
    reply = decision.reply or "收到，我会交给对应 Agent 执行，并把进展反馈给你。"
    assistant_message = ChatMessage(
        role=OpenAIMessageRole.ASSISTANT,
        author="主管 Agent",
        content=reply,
    )
    db.upsert_model("chat_messages", assistant_message)
    return ChatResponse(
        task_id=task.id,
        status=task.status,
        message=reply,
        delegated=True,
        agent=agent_hint or decision.agent_hint or "OrchestratorAgent",
    )


async def _run_task_through_orchestrator(task: Task) -> Task:
    try:
        orchestrator = orchestrator_registry.get_or_create_for_task(task.id, OrchestratorAgent)
        task = get_task(task.id)
        return await orchestrator.run_task(task)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        task.final_summary = f"任务执行失败：{exc}"
        safe_transition(task, TaskStatus.FAILED, actor="TaskService")
        record("task.background_failed", "OrchestratorAgent", {"error": str(exc)}, task_id=task.id)
        raise


def list_chat_messages() -> list[ChatMessage]:
    return [ChatMessage.model_validate(item) for item in reversed(db.fetch_many_by_fields("chat_messages", limit=500))]


def list_tasks() -> list[Task]:
    return [Task.model_validate(item) for item in db.fetch_many_by_fields("tasks")]


def get_task(task_id: str) -> Task:
    data = db.fetch_one("tasks", task_id)
    if not data:
        raise KeyError(task_id)
    return Task.model_validate(data)


def set_task_status(task_id: str, status: TaskStatus, *, strict: bool | None = None) -> Task:
    task = get_task(task_id)
    return safe_transition(task, status, actor="TaskService", strict=strict)


def resume_task(task_id: str, *, strict: bool | None = None) -> Task:
    task = set_task_status(task_id, TaskStatus.EXECUTING_STEP, strict=strict)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _start_resume_thread(task)
    else:
        _spawn_background(get_pool().submit(task, _resume_task_through_orchestrator))
    record("task.resume_requested", "TaskService", {"task_id": task.id}, task_id=task.id)
    return task


async def _resume_task_through_orchestrator(task: Task) -> Task:
    try:
        await _run_existing_plan(task)
        return task
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        task.final_summary = f"Task resume failed: {exc}"
        safe_transition(task, TaskStatus.FAILED, actor="TaskService")
        record("task.resume_failed", "OrchestratorAgent", {"error": str(exc)}, task_id=task.id)
        raise


async def _resume_task_background(task: Task) -> None:
    try:
        await _run_existing_plan(task)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        task.final_summary = f"Task resume failed: {exc}"
        safe_transition(task, TaskStatus.FAILED, actor="TaskService")
        record("task.resume_failed", "OrchestratorAgent", {"error": str(exc)}, task_id=task.id)


def _start_resume_thread(task: Task) -> None:
    thread = threading.Thread(
        target=lambda: asyncio.run(_resume_task_background(task)),
        name=f"task-resume-{task.id}",
        daemon=True,
    )
    thread.start()


async def _run_existing_plan(task: Task) -> None:
    orchestrator = orchestrator_registry.get_or_create_for_task(task.id, OrchestratorAgent)
    plan = orchestrator._latest_plan_for_task(task.id)
    await orchestrator._process_steps(task, plan)
    await orchestrator.completion_handler.finalize(task, plan)
