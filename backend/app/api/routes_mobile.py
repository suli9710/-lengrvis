from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.agents.supervisor_agent import SupervisorDecision
from app.api import routes_approvals
from app.core.errors import StateTransitionError
from app.security.mobile_jwt import (
    TOKEN_SCOPE,
    decode_mobile_token,
    mobile_token_from_websocket,
    require_mobile_or_remote_input_token,
    require_mobile_token,
    validate_mobile_claims_active,
)
from app.core.schemas import ChatResponse, Task, TaskStatus
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.task_phase import TaskPhase
from app.product_task_templates import get_task_starter_template, task_starter_prompt
from app.services import mobile_pairing_service
from app.services.approval_event_service import get_approval_event_bus
from app.services.task_service import _delegate_task as delegate_task
from app.services.task_service import get_task, list_tasks, resume_task, set_task_status


router = APIRouter()
ws_router = APIRouter()


class MobileApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|denied)$")
    note: str = ""


class MobileTaskCreateRequest(BaseModel):
    template_id: str = Field(
        pattern=(
            "^(clean-downloads|summarize-document|find-large-files|check-computer|document-qa|"
            "organize_downloads|summarize_local_docs|find_large_files|check_computer_status|document_qa)$"
        )
    )
    user_input: str = Field(default="", max_length=2000)
    mode: str = Field(default="hybrid", pattern="^(efficiency|privacy|hybrid)$")


class MobileTaskFollowUpRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    mode: str = Field(default="", pattern="^(|efficiency|privacy|hybrid)$")


MOBILE_TASK_TEMPLATES = {
    "organize_downloads": {
        "label": "整理下载目录",
        "agent_hint": "FileAgent",
        "prompt": "整理下载目录或用户指定目录。先扫描、分组并提出清理建议；不要直接删除、移动或改名，任何修改都必须经过现有 dry-run 与审批策略。",
    },
    "summarize_local_docs": {
        "label": "总结本地文档",
        "agent_hint": "DocumentAgent",
        "prompt": "总结用户指定的本地文档或目录。保留引用线索；如果缺少文件范围，请先在任务中说明需要用户补充，不要猜测私人路径。",
    },
    "find_large_files": {
        "label": "查找大文件",
        "agent_hint": "FileAgent",
        "prompt": "查找大文件并给出整理建议。只做扫描和建议，不要删除或移动文件，所有修改都必须经过审批。",
    },
    "check_computer_status": {
        "label": "检查电脑状态",
        "agent_hint": "ComputerAgent",
        "prompt": "检查电脑状态，汇总 CPU、内存、磁盘、电池、启动项和明显风险；只读收集信息。",
    },
    "document_qa": {
        "label": "文档问答",
        "agent_hint": "DocumentAgent",
        "prompt": "围绕用户指定的本地文档回答问题。使用可引用的文档片段；如果缺少问题或文件范围，请先说明需要补充。",
    },
}

MOBILE_TASK_TEMPLATE_ALIASES = {
    "organize_downloads": "clean-downloads",
    "summarize_local_docs": "summarize-document",
    "find_large_files": "find-large-files",
    "check_computer_status": "check-computer",
    "document_qa": "document-qa",
}

MOBILE_TASK_AGENT_HINTS = {
    "clean-downloads": "FileAgent",
    "summarize-document": "DocumentAgent",
    "find-large-files": "FileAgent",
    "check-computer": "ComputerAgent",
    "document-qa": "DocumentAgent",
}


@router.get("/mobile/approvals/pending")
def pending_mobile_approvals(token: dict = Depends(require_mobile_token)) -> list[dict]:
    return mobile_pairing_service.list_pending_approvals(token)


@router.get("/mobile/approvals/{approval_id}")
def mobile_approval_detail(approval_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.get_approval_detail(approval_id, token)


@router.post("/mobile/approvals/{approval_id}/approve")
async def approve_mobile_approval(approval_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    approval = routes_approvals.approval_for_execution(approval_id, token)
    approval = await routes_approvals._execute_approved_step(approval)
    return routes_approvals.approval_execution_response(approval)


@router.post("/mobile/approvals/{approval_id}/reject")
def reject_mobile_approval(approval_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    approval = mobile_pairing_service.reject_approval(approval_id, token)
    routes_approvals._deny_rejected_step(approval)
    routes_approvals._reconcile_runs(approval.task_id)
    return mobile_pairing_service.safe_approval_payload(approval)


@router.post("/mobile/approvals/{approval_id}/decision")
async def decide_mobile_approval(
    approval_id: str,
    request: MobileApprovalDecision,
    token: dict = Depends(require_mobile_or_remote_input_token),
) -> dict:
    if request.decision == "approved":
        return await approve_mobile_approval(approval_id, token)
    return reject_mobile_approval(approval_id, token)


@router.get("/mobile/devices")
def list_mobile_devices(token: dict = Depends(require_mobile_token)) -> dict:
    return {"devices": mobile_pairing_service.list_mobile_devices(token)}


@router.delete("/mobile/devices/{device_id}")
def revoke_mobile_device(device_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.revoke_own_mobile_device(device_id, token)


@router.get("/mobile/tasks")
def list_mobile_tasks(token: dict = Depends(require_mobile_token)) -> dict:
    tasks = sorted(list_tasks(), key=lambda item: item.updated_at, reverse=True)
    return {"tasks": [_mobile_task_payload(task) for task in tasks[:20]]}


@router.post("/mobile/tasks", status_code=201)
async def create_mobile_task(request: MobileTaskCreateRequest, token: dict = Depends(require_mobile_token)) -> dict:
    goal = _mobile_task_template_goal(request)
    template_id = _normalize_mobile_template_id(request.template_id)
    template = get_task_starter_template(template_id)
    response = _delegate_mobile_task_or_error(
        goal,
        request.mode,
        reply=f"已从手机 Companion 发起「{template['label']}」任务，电脑端会继续执行并保留审批边界。",
        agent_hint=MOBILE_TASK_AGENT_HINTS.get(template_id, "OrchestratorAgent"),
    )
    return _mobile_task_created_response(response)


@router.post("/mobile/tasks/{task_id}/follow-up", status_code=201)
async def create_mobile_task_follow_up(
    task_id: str,
    request: MobileTaskFollowUpRequest,
    token: dict = Depends(require_mobile_token),
) -> dict:
    try:
        source_task = get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    instruction = request.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="Missing follow-up instruction")
    mode = request.mode or source_task.mode or "hybrid"
    response = _delegate_mobile_task_or_error(
        _mobile_task_follow_up_goal(source_task, instruction),
        mode,
        reply="已从手机 Companion 添加补充指令，电脑端会作为相关任务继续处理。",
        agent_hint="OrchestratorAgent",
    )
    return _mobile_task_created_response(response, source_task_id=source_task.id)


@router.post("/mobile/tasks/{task_id}/pause")
def pause_mobile_task(task_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    try:
        _ensure_mobile_task_pauseable(get_task(task_id))
        return _mobile_task_payload(set_task_status(task_id, TaskStatus.PAUSED, strict=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/mobile/tasks/{task_id}/resume")
def resume_mobile_task(task_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    try:
        return _mobile_task_payload(resume_task(task_id, strict=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/mobile/tasks/{task_id}/cancel")
def cancel_mobile_task(task_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    try:
        return _mobile_task_payload(set_task_status(task_id, TaskStatus.CANCELLED, strict=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/mobile/remote-input-grants/{grant_id}/token")
def claim_remote_input_grant_token(grant_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.claim_remote_input_grant_token(grant_id, token)


@router.delete("/mobile/remote-input-grants/{grant_id}")
def revoke_own_remote_input_grant(grant_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    device_id = str(token.get("device_id") or "")
    if not device_id:
        raise HTTPException(status_code=401, detail="Mobile token is missing a device binding")
    return mobile_pairing_service.revoke_remote_input_grant(device_id, grant_id)


@ws_router.websocket("/ws/mobile/notifications")
async def mobile_notifications(websocket: WebSocket, token: str = ""):
    await _mobile_notifications(websocket, token, notification_alias=True)


@ws_router.websocket("/ws/mobile/approvals")
async def mobile_approval_events_legacy(websocket: WebSocket, token: str = ""):
    await _mobile_notifications(websocket, token)


async def _mobile_notifications(websocket: WebSocket, token: str = "", *, notification_alias: bool = False):
    try:
        claims = decode_mobile_token(mobile_token_from_websocket(websocket, token), allowed_scopes={TOKEN_SCOPE})
    except HTTPException:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue = get_approval_event_bus().subscribe()
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "device_id": claims.get("device_id"),
                "pending": mobile_pairing_service.list_pending_approvals(claims),
            }
        )
        while True:
            if await _close_if_mobile_claims_inactive(websocket, claims):
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25)
                if not _mobile_event_allowed(event, claims):
                    continue
                if event.get("type") == "mobile_device_revoked":
                    await websocket.send_json(event)
                    await websocket.close(code=1008)
                    return
                if await _close_if_mobile_claims_inactive(websocket, claims):
                    return
                if notification_alias and event.get("type") == "approval_created":
                    await websocket.send_json({"type": "approval_notification", "approval": event.get("approval")})
                else:
                    await websocket.send_json(event)
            except asyncio.TimeoutError:
                if await _close_if_mobile_claims_inactive(websocket, claims):
                    return
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        return
    finally:
        get_approval_event_bus().unsubscribe(queue)


async def _close_if_mobile_claims_inactive(websocket: WebSocket, claims: dict) -> bool:
    try:
        validate_mobile_claims_active(claims)
    except HTTPException:
        await websocket.close(code=1008)
        return True
    return False


def _mobile_event_allowed(event: dict, claims: dict) -> bool:
    if event.get("type") in {"remote_input_grant_created", "remote_input_grant_revoked", "mobile_device_revoked"}:
        return str(event.get("device_id") or "") == str(claims.get("device_id") or "")
    approval = event.get("approval")
    if not isinstance(approval, dict):
        return True
    return mobile_pairing_service.mobile_claims_can_access_approval(approval, claims)


def _mobile_task_payload(task: Task) -> dict:
    title, summary = _mobile_task_text(task)
    return {
        "id": task.id,
        "title": title,
        "status": _mobile_task_status(task),
        "mode": task.mode,
        "summary": summary,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _mobile_task_status(task: Task) -> str:
    if task.execution_stage == ExecutionStage.PAUSED:
        return "paused"
    if task.execution_stage == ExecutionStage.AWAITING_APPROVAL:
        return "waiting_approval"
    return str(task.status.value if hasattr(task.status, "value") else task.status)


def _mobile_task_text(task: Task) -> tuple[str, str]:
    if task.mode == "privacy":
        return "隐私任务", "隐私模式：请在电脑端查看任务详情。"
    return (task.user_goal[:120] or "Lengrvis 任务", task.final_summary[:240])


def _mobile_task_template_goal(request: MobileTaskCreateRequest) -> str:
    template_id = _normalize_mobile_template_id(request.template_id)
    template = get_task_starter_template(template_id)
    parts = [
        f"来自手机 Companion 的任务模板：{template['label']}。",
        task_starter_prompt(template_id),
        "必须仍通过 Lengrvis 电脑端任务系统执行；不要绕过现有安全策略、dry-run、审批、权限和审计机制。",
    ]
    user_input = request.user_input.strip()
    if user_input:
        parts.append(f"用户补充：{user_input}")
    return "\n".join(parts)


def _normalize_mobile_template_id(template_id: str) -> str:
    return MOBILE_TASK_TEMPLATE_ALIASES.get(template_id, template_id)


def _mobile_task_follow_up_goal(source_task: Task, instruction: str) -> str:
    if source_task.mode == "privacy":
        source_context = f"原任务是隐私模式任务（ID: {source_task.id}），不要在手机侧或新任务摘要中复述原任务正文。"
    else:
        source_context = f"原任务 ID: {source_task.id}；原任务摘要：{source_task.user_goal[:240]}"
    return "\n".join(
        [
            "来自手机 Companion 的补充指令。",
            source_context,
            f"补充指令：{instruction}",
            "请作为相关电脑任务继续处理；所有文件、系统或应用变更仍必须经过现有 dry-run 与审批策略。",
        ]
    )


def _delegate_mobile_task(goal: str, mode: str, *, reply: str, agent_hint: str) -> ChatResponse:
    return delegate_task(goal, mode, SupervisorDecision(delegate=True, reply=reply, agent_hint=agent_hint))


def _delegate_mobile_task_or_error(goal: str, mode: str, *, reply: str, agent_hint: str) -> ChatResponse:
    try:
        return _delegate_mobile_task(goal, mode, reply=reply, agent_hint=agent_hint)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Computer task service is unavailable. Please retry from the desktop task workspace.",
        ) from exc


def _mobile_task_created_response(response: ChatResponse, *, source_task_id: str = "") -> dict:
    if not response.delegated or not response.task_id:
        raise HTTPException(status_code=409, detail=response.message or "Mobile task request was not delegated to a computer task.")
    try:
        task = get_task(response.task_id)
    except KeyError:
        raise HTTPException(status_code=409, detail="Mobile task request was accepted but no computer task was created.") from None
    return {
        "task": _mobile_task_payload(task),
        "message": response.message,
        "source_task_id": source_task_id,
    }


def _ensure_mobile_task_pauseable(task: Task) -> None:
    if task.status == TaskPhase.EXECUTION and task.execution_stage == ExecutionStage.STEP_RUNNING:
        return
    raise HTTPException(status_code=409, detail="Only actively running tasks can be paused.")
