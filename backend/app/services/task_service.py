from __future__ import annotations

import asyncio
import re
from typing import Any

from app.agents.delegation_metadata import build_task_delegation_metadata
from app.agents.delegation_rules import FILE_ACTION_TERMS, UNINSTALL_TERMS, contains_any
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.path_detection import find_explicit_path
from app.agents.supervisor_agent import SupervisorAgent, SupervisorDecision
from app.core import db
from app.core.audit import record
from app.core.errors import AppError, StateTransitionError
from app.core.schemas import ChatMessage, ChatResponse, OpenAIMessageRole, RunEngine, RunPhase, Task, TaskStatus
from app.orchestration.engine_router import route_engine
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.orchestrator_registry import orchestrator_registry
from app.orchestration.state_machine import safe_transition
from app.orchestration.task_phase import TaskPhase
from app.services.task_pool import get_pool


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
    get_pool().submit_nowait(task, _run_task_through_orchestrator)
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
        # The worker may have been cancelled after its initial task snapshot
        # was taken. Reload before persisting a failure so a late exception
        # cannot turn a user-requested pause/cancel back into FAILED.
        _persist_resume_failure_if_active(task.id, exc)
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


async def pause_task(task_id: str) -> Task:
    task = get_task(task_id)
    # Persist the user-visible stop state before cancellation so any worker
    # that reaches a write boundary can observe it and fail closed.
    if task.status == TaskPhase.EXECUTION and task.execution_stage == ExecutionStage.PAUSED:
        paused = task
    else:
        paused = safe_transition(task, TaskStatus.PAUSED, actor="TaskService", strict=True)
    # A repeated pause is deliberately convergent: a prior cancellation may
    # have raced registration, so always fan out cancellation to both worker
    # owners even if the persisted task already says paused.
    await get_pool().cancel(task_id)
    from app.services import run_service

    run_service.pause_runs_for_task(task_id)
    return get_task(paused.id)


async def cancel_task(task_id: str, *, strict: bool | None = None) -> Task:
    get_task(task_id)
    await get_pool().cancel(task_id)
    from app.services import run_service

    run_service.cancel_runs_for_task(task_id, active_grace_seconds=0.0)
    task = get_task(task_id)
    if task.status == TaskPhase.CANCELLED:
        return task
    return safe_transition(task, TaskStatus.CANCELLED, actor="TaskService", strict=strict)


def resume_task(task_id: str, *, strict: bool | None = None) -> Task:
    pool = get_pool()
    if pool.active_task(task_id):
        task = get_task(task_id)
        record("task.resume_duplicate_ignored", "TaskService", {"task_id": task.id}, task_id=task.id)
        return task
    task = get_task(task_id)
    if task.status != TaskPhase.EXECUTION or task.execution_stage != ExecutionStage.PAUSED:
        raise StateTransitionError(
            f"{task.status.value}:{task.execution_stage.value}",
            f"{TaskPhase.EXECUTION.value}:{ExecutionStage.STEP_RUNNING.value}",
        )
    try:
        asyncio.get_running_loop()
    except RuntimeError as exc:
        raise AppError(
            code="task_runtime_unavailable",
            message="Task resume requires the managed asynchronous runtime.",
            status_code=503,
        ) from exc
    # Resuming is an execution boundary, so it must fail closed even when the
    # application's general state-machine compatibility mode is non-strict.
    # Otherwise a terminal task is returned unchanged and still scheduled.
    task = set_task_status(task.id, TaskStatus.EXECUTING_STEP, strict=True)
    from app.services import run_service

    # A task created through the run API has a persistent Run owner. Resume
    # that owner instead of also creating a TaskPool worker for the same plan;
    # two independent executors could otherwise perform the same step twice.
    bound_runs = db.fetch_many("runs", "task_id = ?", (task.id,), limit=100)
    has_nonterminal_bound_run = any(
        RunPhase(item.get("phase", RunPhase.PENDING.value)) not in run_service.TERMINAL_PHASES
        for item in bound_runs
    )
    if has_nonterminal_bound_run:
        run_service.resume_runs_for_task(task.id)
        record("task.resume_requested", "TaskService", {"task_id": task.id, "owner": "run"}, task_id=task.id)
        return get_task(task.id)

    pool.submit_nowait(task, _resume_task_through_orchestrator)
    record("task.resume_requested", "TaskService", {"task_id": task.id, "owner": "task_pool"}, task_id=task.id)
    return task


async def _resume_task_through_orchestrator(task: Task) -> Task:
    try:
        await _run_existing_plan(task)
        return task
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        _persist_resume_failure_if_active(task.id, exc)
        record("task.resume_failed", "OrchestratorAgent", {"error": str(exc)}, task_id=task.id)
        raise


def _persist_resume_failure_if_active(task_id: str, exc: Exception) -> Task:
    latest = get_task(task_id)
    if latest.status in {TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED} or (
        latest.status == TaskPhase.EXECUTION and latest.execution_stage == ExecutionStage.PAUSED
    ):
        record(
            "task.late_resume_failure_ignored",
            "TaskService",
            {"persisted_status": latest.status.value, "error": str(exc)},
            task_id=task_id,
        )
        return latest
    latest.final_summary = f"Task resume failed: {exc}"
    return safe_transition(latest, TaskStatus.FAILED, actor="TaskService", strict=True)


async def _run_existing_plan(task: Task) -> None:
    orchestrator = orchestrator_registry.get_or_create_for_task(task.id, OrchestratorAgent)
    plan = orchestrator._latest_plan_for_task(task.id)
    await orchestrator._process_steps(task, plan)
    await orchestrator.completion_handler.finalize(task, plan)
