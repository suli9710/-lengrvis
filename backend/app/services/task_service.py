from __future__ import annotations

import asyncio
import re

from app.agents.supervisor_agent import SupervisorAgent, SupervisorDecision
from app.agents.orchestrator_agent import OrchestratorAgent
from app.core import db
from app.core.audit import record
from app.core.schemas import ChatMessage, ChatResponse, OpenAIMessageRole, Task, TaskStatus
from app.llm.local_provider import LocalBackendUnavailable
from app.orchestration.state_machine import safe_transition
from app.services.task_pool import get_pool


PATH_ACTION_RE = re.compile(r"[A-Za-z]:[\\/][^\r\n\"<>|?*]+")
FILE_ACTION_TERMS = (
    "删除",
    "删掉",
    "移除",
    "清理",
    "复制",
    "移动",
    "重命名",
    "读取",
    "打开",
    "delete",
    "remove",
    "trash",
    "copy",
    "move",
    "rename",
    "open",
)
UNINSTALL_TERMS = ("卸载", "uninstall")


async def create_task(message: str, mode: str) -> ChatResponse:
    task = await OrchestratorAgent().handle_user_goal(message, mode)
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

    supervisor = SupervisorAgent()
    quick_decision = supervisor.quick_decision(message)
    if not quick_decision.delegate and _is_explicit_file_path_request(message):
        quick_decision = SupervisorDecision(
            delegate=True,
            reply=supervisor._delegation_reply("FileAgent", message.lower()),
            agent_hint="FileAgent",
        )
    if not quick_decision.delegate and _is_uninstall_request(message):
        quick_decision = SupervisorDecision(
            delegate=True,
            reply=supervisor._delegation_reply("AppAgent", message.lower()),
            agent_hint="AppAgent",
        )
    if quick_decision.delegate:
        asyncio.create_task(_run_supervisor_background(supervisor, message, mode))
        return _delegate_task(message, mode, quick_decision)
    decision = await supervisor.decide(message, mode)
    if not decision.delegate and _is_explicit_file_path_request(message):
        decision = SupervisorDecision(
            delegate=True,
            reply=supervisor._delegation_reply("FileAgent", message.lower()),
            agent_hint="FileAgent",
        )
    if not decision.delegate and _is_uninstall_request(message):
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

    return _delegate_task(message, mode, decision)


def _is_explicit_file_path_request(message: str) -> bool:
    normalized = message.lower()
    return bool(PATH_ACTION_RE.search(message)) and any(term in normalized for term in FILE_ACTION_TERMS)


def _is_uninstall_request(message: str) -> bool:
    normalized = message.lower()
    return any(term in normalized for term in UNINSTALL_TERMS)


def _delegate_task(message: str, mode: str, decision: SupervisorDecision) -> ChatResponse:
    orchestrator = OrchestratorAgent()
    task = orchestrator.create_task_shell(message, mode)
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
    pool = get_pool()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(pool.submit(task, _run_task_through_orchestrator))
        else:
            asyncio.create_task(_run_task_background(task))
    except RuntimeError:
        asyncio.create_task(_run_task_background(task))
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
        agent=decision.agent_hint or "OrchestratorAgent",
    )


async def _run_task_through_orchestrator(task: Task) -> Task:
    try:
        return await OrchestratorAgent().run_task(task)
    except Exception as exc:
        task.final_summary = f"任务执行失败：{exc}"
        safe_transition(task, TaskStatus.FAILED, actor="TaskService")
        record("task.background_failed", "OrchestratorAgent", {"error": str(exc)}, task_id=task.id)
        raise


async def _run_supervisor_background(supervisor: SupervisorAgent, message: str, mode: str) -> None:
    try:
        await supervisor.decide(message, mode)
    except Exception as exc:
        record("supervisor.background_failed", "SupervisorAgent", {"error": str(exc)})


async def _run_task_background(task: Task) -> None:
    try:
        await OrchestratorAgent().run_task(task)
    except Exception as exc:
        task.final_summary = f"任务执行失败：{exc}"
        safe_transition(task, TaskStatus.FAILED, actor="TaskService")
        record("task.background_failed", "OrchestratorAgent", {"error": str(exc)}, task_id=task.id)


def list_chat_messages() -> list[ChatMessage]:
    return [
        ChatMessage.model_validate(item)
        for item in reversed(db.fetch_many("chat_messages", limit=500))
    ]


def list_tasks() -> list[Task]:
    return [Task.model_validate(item) for item in db.fetch_many("tasks")]


def get_task(task_id: str) -> Task:
    data = db.fetch_one("tasks", task_id)
    if not data:
        raise KeyError(task_id)
    return Task.model_validate(data)


def set_task_status(task_id: str, status: TaskStatus) -> Task:
    task = get_task(task_id)
    return safe_transition(task, status, actor="TaskService")
