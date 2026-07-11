from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.agents.supervisor_agent import SupervisorDecision
from app.api import routes_approvals
from app.core import db
from app.core.errors import StateTransitionError
from app.core.schemas import ChatResponse, Task
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.task_phase import TaskPhase
from app.policy.redaction import redact_public_text, redact_value
from app.product_task_templates import get_task_starter_template, task_starter_prompt
from app.security.lan import is_secure_mobile_transport
from app.security.mobile_jwt import (
    TOKEN_SCOPE,
    decode_mobile_token,
    mobile_token_from_websocket,
    require_mobile_or_remote_input_token,
    require_mobile_token,
    validate_mobile_claims_active,
)
from app.services import mobile_pairing_service
from app.services.approval_event_service import get_approval_event_bus
from app.services.task_explain_service import build_task_completion_evidence
from app.services.task_service import _delegate_task as delegate_task
from app.services.task_service import cancel_task, get_task, list_tasks, pause_task, resume_task

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


class MobilePushSubscriptionRequest(BaseModel):
    provider: str = Field(pattern="^expo$")
    token: str = Field(
        min_length=20,
        max_length=256,
        pattern=r"^(?:Expo|Exponent)PushToken\[[A-Za-z0-9_-]{1,200}\]$",
    )


class MobileSessionRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=40, max_length=512)


MOBILE_TASK_TEMPLATES = {
    "organize_downloads": {
        "label": "整理下载目录",
        "agent_hint": "FileAgent",
        "prompt": (
            "整理下载目录或用户指定目录。先扫描、分组并提出清理建议；不要直接删除、移动或改名，"
            "任何修改都必须经过现有 dry-run 与审批策略。"
        ),
    },
    "summarize_local_docs": {
        "label": "总结本地文档",
        "agent_hint": "DocumentAgent",
        "prompt": (
            "总结用户指定的本地文档或目录。保留引用线索；如果缺少文件范围，"
            "请先在任务中说明需要用户补充，不要猜测私人路径。"
        ),
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

MOBILE_TASK_TITLE_FALLBACK = "Lengrvis 任务"
MOBILE_TASK_PRIVACY_TITLE = "隐私任务"
MOBILE_TASK_PRIVACY_SUMMARY = "隐私模式：请在电脑端查看任务详情。"
MOBILE_TASK_METADATA_SOURCE = "mobile_companion"
MOBILE_TASK_EVIDENCE_PLACEHOLDER = "[desktop evidence hidden]"
MOBILE_LOCAL_PATH_RE = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/][^\s,;'\"<>\uFF0C\u3002\uFF1B\u3001]+|"
    r"(?:/Users|/home|/tmp|/var|/private)/[^\s,;'\"<>\uFF0C\u3002\uFF1B\u3001]+|"
    r"~[\\/][^\s,;'\"<>\uFF0C\u3002\uFF1B\u3001]+)"
)
MOBILE_TASK_EVIDENCE_LOCATOR_RE = re.compile(
    r"(?ix)(?:"
    r"(?:https?://[^\s,;'\"<>]+)?/api/tasks/[^\s,;'\"<>]+/"
    r"(?:timeline|replay|recordings/[^\s,;'\"<>]+)"
    r"|\brec_[A-Za-z0-9_-]{8,}\b"
    r"|\b[A-Za-z0-9_.-]+-[A-Za-z0-9_.-]+-\d{8}T\d{6}\d{1,6}Z\.png\b"
    r")"
)
MOBILE_TASK_EVIDENCE_FIELD_RE = re.compile(
    r"(?i)\b(?:recording(?:_id|_url)?|file_name|timeline_url|replay_url)\s*[:=]\s*['\"]?[^\s,;'\"<>]+['\"]?"
)
MOBILE_TASK_STRUCTURED_EVIDENCE_RE = re.compile(
    r"(?i)['\"]?\b(?:model_action|task_metadata|metadata|tool[_ -]?(?:args?|arguments?|results?|calls?)|"
    r"tool_call(?:_id)?)\b['\"]?\s*[:=]"
)
MOBILE_TERMINAL_TASK_PHASES = {TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED}


@router.post("/mobile/session/refresh")
def refresh_mobile_session(request: MobileSessionRefreshRequest) -> dict:
    return mobile_pairing_service.refresh_mobile_session_token(request.refresh_token)


@router.put("/mobile/push-subscription")
def register_mobile_push_subscription(
    request: MobilePushSubscriptionRequest,
    token: dict = Depends(require_mobile_token),
) -> dict:
    return mobile_pairing_service.register_mobile_push_subscription(
        token,
        provider=request.provider,
        push_token=request.token,
    )


@router.delete("/mobile/push-subscription")
def unregister_mobile_push_subscription(token: dict = Depends(require_mobile_token)) -> dict:
    return mobile_pairing_service.unregister_mobile_push_subscription(token)


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
    tasks = sorted(
        [task for task in list_tasks() if _mobile_task_allowed(task, token)],
        key=lambda item: item.updated_at,
        reverse=True,
    )
    return {"tasks": [_mobile_task_payload(task) for task in tasks[:20]]}


@router.get("/mobile/tasks/{task_id}")
def get_mobile_task_status(task_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    try:
        task = get_task(task_id)
        _raise_if_mobile_task_disallowed(task, token)
        return _mobile_task_payload(task)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None


@router.post("/mobile/tasks", status_code=201)
async def create_mobile_task(request: MobileTaskCreateRequest, token: dict = Depends(require_mobile_token)) -> dict:
    goal = _mobile_task_template_goal(request)
    template_id = _normalize_mobile_template_id(request.template_id)
    template = get_task_starter_template(template_id)
    metadata = _mobile_task_source_metadata(token, action="template", template_id=template_id)
    response = _delegate_mobile_task_or_error(
        goal,
        request.mode,
        reply=f"已从手机 Companion 发起「{template['label']}」任务，电脑端会继续执行并保留审批边界。",
        agent_hint=MOBILE_TASK_AGENT_HINTS.get(template_id, "OrchestratorAgent"),
        metadata=metadata,
    )
    return _mobile_task_created_response(response, metadata=metadata)


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
    _raise_if_mobile_task_disallowed(source_task, token)
    instruction = request.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=422, detail="Missing follow-up instruction")
    mode = request.mode or source_task.mode or "hybrid"
    metadata = _mobile_task_source_metadata(token, action="follow_up", source_task_id=source_task.id)
    response = _delegate_mobile_task_or_error(
        _mobile_task_follow_up_goal(source_task, instruction),
        mode,
        reply="已从手机 Companion 添加补充指令，电脑端会作为相关任务继续处理。",
        agent_hint="OrchestratorAgent",
        metadata=metadata,
    )
    return _mobile_task_created_response(response, metadata=metadata, source_task_id=source_task.id)


@router.post("/mobile/tasks/{task_id}/pause")
async def pause_mobile_task(task_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    try:
        task = get_task(task_id)
        _raise_if_mobile_task_disallowed(task, token)
        _ensure_mobile_task_pauseable(task)
        return _mobile_task_payload(await pause_task(task_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/mobile/tasks/{task_id}/resume")
async def resume_mobile_task(task_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    try:
        _raise_if_mobile_task_disallowed(get_task(task_id), token)
        return _mobile_task_payload(resume_task(task_id, strict=True))
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    except StateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/mobile/tasks/{task_id}/cancel")
async def cancel_mobile_task(task_id: str, token: dict = Depends(require_mobile_token)) -> dict:
    try:
        _raise_if_mobile_task_disallowed(get_task(task_id), token)
        return _mobile_task_payload(await cancel_task(task_id, strict=True))
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
    client_host = websocket.client.host if websocket.client else ""
    if not is_secure_mobile_transport(client_host, websocket.url.scheme):
        await websocket.close(code=1008)
        return
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
                "remote_input_grants": mobile_pairing_service.list_active_remote_input_grants_for_claims(claims),
                "tasks": [
                    _mobile_task_payload(task)
                    for task in sorted(
                        [item for item in list_tasks() if _mobile_task_allowed(item, claims)],
                        key=lambda item: item.updated_at,
                        reverse=True,
                    )[:20]
                ],
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
                    await websocket.send_json(_safe_mobile_event(event, claims=claims))
                    await websocket.close(code=1008)
                    return
                if await _close_if_mobile_claims_inactive(websocket, claims):
                    return
                await websocket.send_json(
                    _safe_mobile_event(event, notification_alias=notification_alias, claims=claims)
                )
            except TimeoutError:
                if await _close_if_mobile_claims_inactive(websocket, claims):
                    return
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        return
    finally:
        get_approval_event_bus().unsubscribe(queue)


async def _close_if_mobile_claims_inactive(websocket: WebSocket, claims: dict) -> bool:
    try:
        validate_mobile_claims_active(claims, scope_exp_scopes={TOKEN_SCOPE})
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


def _safe_mobile_event(event: dict, *, notification_alias: bool = False, claims: dict | None = None) -> dict:
    event_type = str(event.get("type") or "")
    if event_type in {"approval_created", "approval_decided"}:
        payload_type = (
            "approval_notification" if notification_alias and event_type == "approval_created" else event_type
        )
        approval = event.get("approval")
        safe_approval = (
            mobile_pairing_service.safe_approval_payload(approval, claims) if isinstance(approval, dict) else {}
        )
        return {"type": payload_type, "approval": safe_approval}
    if event_type in {"remote_input_grant_created", "remote_input_grant_revoked"}:
        return {
            "type": event_type,
            "device_id": str(event.get("device_id") or ""),
            "grant": _safe_remote_input_grant_event(event.get("grant")),
        }
    if event_type == "mobile_device_revoked":
        device = event.get("device") if isinstance(event.get("device"), dict) else {}
        return {
            "type": event_type,
            "device_id": str(event.get("device_id") or device.get("device_id") or ""),
            "device": {
                "device_id": str(device.get("device_id") or event.get("device_id") or ""),
                "device_name": str(device.get("device_name") or "Android device"),
                "status": str(device.get("status") or "revoked"),
                "revoked_at": str(device.get("revoked_at") or ""),
                "updated_at": str(device.get("updated_at") or ""),
            },
        }
    if event_type == "heartbeat":
        return {"type": "heartbeat"}
    return {"type": event_type or "event"}


def _safe_remote_input_grant_event(value: object) -> dict:
    grant = value if isinstance(value, dict) else {}
    return {
        "id": str(grant.get("id") or grant.get("grant_id") or ""),
        "status": str(grant.get("status") or "active"),
        "scope": str(grant.get("scope") or "remote:input"),
        "created_at": str(grant.get("created_at") or ""),
        "expires_at": str(grant.get("expires_at") or ""),
        "revoked_at": str(grant.get("revoked_at") or ""),
        "binding_ref": str(grant.get("binding_ref") or ""),
    }


def _mobile_task_payload(task: Task) -> dict:
    title, summary, content_redacted = _mobile_task_text(task)
    status = _mobile_task_status(task)
    available_actions = _mobile_task_available_actions(task)
    completion_evidence = _mobile_task_completion_evidence(task)
    privacy_redacted = task.mode == "privacy"
    return {
        "id": task.id,
        "title": title,
        "status": status,
        "status_label": _mobile_task_status_label(status),
        "status_detail": _mobile_task_status_detail(status),
        "mode": task.mode,
        "summary": summary,
        "available_actions": available_actions,
        "can_pause": "pause" in available_actions,
        "can_resume": "resume" in available_actions,
        "can_cancel": "cancel" in available_actions,
        "can_follow_up": "follow_up" in available_actions,
        "is_terminal": _mobile_task_is_terminal(task),
        "content_redacted": content_redacted,
        "privacy_redacted": privacy_redacted,
        "completion_evidence": completion_evidence,
        "result_verified": completion_evidence["result_verified"],
        "evidence_verified": completion_evidence["result_verified"],
        "credibility": _mobile_task_credibility(completion_evidence, privacy_redacted=privacy_redacted),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _mobile_task_completion_evidence(task: Task) -> dict:
    evidence = build_task_completion_evidence(task)
    level = str(evidence.get("level") or "submission")
    result_verified = (
        level == "completed_result" and evidence.get("result_verified") is True and evidence.get("signoff") is False
    )
    missing = evidence.get("missing") if isinstance(evidence.get("missing"), list) else []
    return {
        "level": level,
        "result_verified": result_verified,
        "signoff": False,
        "missing_count": 0 if result_verified else len(missing),
    }


def _mobile_task_credibility(completion_evidence: dict, *, privacy_redacted: bool) -> str:
    if privacy_redacted:
        return "redacted"
    if completion_evidence.get("result_verified") is True:
        return "verified"
    level = str(completion_evidence.get("level") or "")
    if level == "safe_failure":
        return "failed"
    if level in {"visible_progress", "task_created"}:
        return "partial"
    return "unverified"


def _mobile_task_status(task: Task) -> str:
    if task.execution_stage == ExecutionStage.PAUSED:
        return "paused"
    if task.execution_stage == ExecutionStage.AWAITING_APPROVAL:
        return "waiting_approval"
    return str(task.status.value if hasattr(task.status, "value") else task.status)


def _mobile_task_text(task: Task) -> tuple[str, str, bool]:
    if task.mode == "privacy":
        return MOBILE_TASK_PRIVACY_TITLE, MOBILE_TASK_PRIVACY_SUMMARY, True
    title, title_redacted = _safe_mobile_task_text(task.user_goal, limit=120, fallback=MOBILE_TASK_TITLE_FALLBACK)
    summary, summary_redacted = _safe_mobile_task_text(task.final_summary, limit=240, fallback="")
    return title, summary, title_redacted or summary_redacted


def _safe_mobile_task_text(value: object, *, limit: int, fallback: str) -> tuple[str, bool]:
    raw = str(value or "").strip()
    if not raw:
        return fallback, False
    redacted = redact_value(raw)
    safe = str(redacted if redacted is not None else "").strip()
    safe = redact_public_text(safe)
    safe = (
        safe.replace("[REDACTED_LOCAL_PATH]", "[本地路径]")
        .replace("[REDACTED_FILE_NAME]", "[文件名]")
        .replace("[REDACTED_PROMPT]", "[已隐藏]")
    )
    safe = MOBILE_LOCAL_PATH_RE.sub("[本地路径]", safe)
    safe, evidence_redacted = _redact_mobile_task_evidence(safe, fallback=fallback)
    safe = " ".join(safe.split())
    truncated = len(safe) > limit
    safe = safe[:limit].strip() or fallback
    return safe, safe != raw or truncated or evidence_redacted


def _redact_mobile_task_evidence(value: str, *, fallback: str) -> tuple[str, bool]:
    if not value:
        return value, False
    if MOBILE_TASK_STRUCTURED_EVIDENCE_RE.search(value):
        return fallback or MOBILE_TASK_EVIDENCE_PLACEHOLDER, True
    redacted = MOBILE_TASK_EVIDENCE_FIELD_RE.sub(MOBILE_TASK_EVIDENCE_PLACEHOLDER, value)
    redacted = MOBILE_TASK_EVIDENCE_LOCATOR_RE.sub(MOBILE_TASK_EVIDENCE_PLACEHOLDER, redacted)
    return redacted, redacted != value


def _mobile_task_available_actions(task: Task) -> list[str]:
    actions: list[str] = []
    if _mobile_task_can_pause(task):
        actions.append("pause")
    if _mobile_task_can_resume(task):
        actions.append("resume")
    if _mobile_task_can_cancel(task):
        actions.append("cancel")
    if not _mobile_task_is_terminal(task):
        actions.append("follow_up")
    return actions


def _mobile_task_can_pause(task: Task) -> bool:
    return task.status == TaskPhase.EXECUTION and task.execution_stage == ExecutionStage.STEP_RUNNING


def _mobile_task_can_resume(task: Task) -> bool:
    return task.status == TaskPhase.EXECUTION and task.execution_stage == ExecutionStage.PAUSED


def _mobile_task_can_cancel(task: Task) -> bool:
    return task.status not in MOBILE_TERMINAL_TASK_PHASES


def _mobile_task_is_terminal(task: Task) -> bool:
    return task.status in MOBILE_TERMINAL_TASK_PHASES


def _mobile_task_status_label(status: str) -> str:
    return {
        "created": "已创建",
        "goal_analysis": "分析目标",
        "planning": "规划中",
        "consultation": "协作中",
        "plan_review": "计划确认",
        "execution": "运行中",
        "waiting_approval": "等待审批",
        "paused": "已暂停",
        "final_review": "最终检查",
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
    }.get(status, status or "未知状态")


def _mobile_task_status_detail(status: str) -> str:
    return {
        "created": "电脑端已收到任务，正在排队准备。",
        "goal_analysis": "电脑端正在理解目标。",
        "planning": "电脑端正在规划执行步骤。",
        "consultation": "多个 Agent 正在协作确认路径。",
        "plan_review": "电脑端正在检查计划边界。",
        "execution": "电脑端正在执行任务，可从手机暂停或取消。",
        "waiting_approval": "电脑端正在等待审批，请在手机审批区处理。",
        "paused": "任务已暂停，可从手机继续。",
        "final_review": "电脑端正在整理最终结果。",
        "completed": "任务已结束，可查看电脑端结果。",
        "failed": "任务执行失败，请回到电脑端查看详情。",
        "cancelled": "任务已取消。",
    }.get(status, "任务状态已同步。")


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
        source_summary, redacted = _safe_mobile_task_text(
            source_task.user_goal,
            limit=160,
            fallback="已关联电脑端原任务",
        )
        suffix = "（已脱敏）" if redacted else ""
        source_context = f"原任务 ID: {source_task.id}；原任务上下文{suffix}：{source_summary}"
    return "\n".join(
        [
            "来自手机 Companion 的补充指令。",
            source_context,
            f"补充指令：{instruction}",
            "请作为相关电脑任务继续处理；所有文件、系统或应用变更仍必须经过现有 dry-run 与审批策略。",
        ]
    )


def _delegate_mobile_task(
    goal: str,
    mode: str,
    *,
    reply: str,
    agent_hint: str,
    metadata: dict | None = None,
) -> ChatResponse:
    return delegate_task(
        goal, mode, SupervisorDecision(delegate=True, reply=reply, agent_hint=agent_hint), metadata=metadata
    )


def _delegate_mobile_task_or_error(
    goal: str,
    mode: str,
    *,
    reply: str,
    agent_hint: str,
    metadata: dict | None = None,
) -> ChatResponse:
    try:
        return _delegate_mobile_task(goal, mode, reply=reply, agent_hint=agent_hint, metadata=metadata)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        raise HTTPException(
            status_code=503,
            detail="Computer task service is unavailable. Please retry from the desktop task workspace.",
        ) from exc


def _mobile_task_created_response(
    response: ChatResponse,
    *,
    metadata: dict | None = None,
    source_task_id: str = "",
) -> dict:
    if not response.delegated or not response.task_id:
        raise HTTPException(
            status_code=409, detail=response.message or "Mobile task request was not delegated to a computer task."
        )
    try:
        task = get_task(response.task_id)
    except KeyError:
        raise HTTPException(
            status_code=409, detail="Mobile task request was accepted but no computer task was created."
        ) from None
    if metadata:
        task = _attach_mobile_task_metadata(task, metadata)
    return {
        "task": _mobile_task_payload(task),
        "message": response.message,
        "source_task_id": source_task_id,
    }


def _ensure_mobile_task_pauseable(task: Task) -> None:
    if task.status == TaskPhase.EXECUTION and task.execution_stage == ExecutionStage.STEP_RUNNING:
        return
    raise HTTPException(status_code=409, detail="Only actively running tasks can be paused.")


def _mobile_task_source_metadata(
    claims: dict,
    *,
    action: str,
    template_id: str = "",
    source_task_id: str = "",
) -> dict:
    device_id = str(claims.get("device_id") or "").strip()
    metadata = {
        "source": MOBILE_TASK_METADATA_SOURCE,
        "source_device_id": device_id,
        "allowed_device_ids": [device_id] if device_id else [],
        "action": action,
    }
    if template_id:
        metadata["template_id"] = template_id
    if source_task_id:
        metadata["source_task_id"] = source_task_id
    return {"mobile_companion": metadata}


def _attach_mobile_task_metadata(task: Task, metadata: dict) -> Task:
    merged = dict(task.metadata or {})
    for key, value in metadata.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    task.metadata = merged
    db.upsert_model("tasks", task)
    return task


def _raise_if_mobile_task_disallowed(task: Task, claims: dict) -> None:
    if not _mobile_task_allowed(task, claims):
        raise HTTPException(status_code=403, detail="Mobile token is not allowed to access this task.")


def _mobile_task_allowed(task: Task, claims: dict) -> bool:
    device_id = str(claims.get("device_id") or "").strip()
    if not device_id:
        return False
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    companion = metadata.get("mobile_companion") if isinstance(metadata.get("mobile_companion"), dict) else {}
    if str(companion.get("source") or "") != MOBILE_TASK_METADATA_SOURCE:
        return False
    allowed = set(_mobile_text_list(companion.get("allowed_device_ids")))
    source_device_id = str(companion.get("source_device_id") or "").strip()
    if source_device_id:
        allowed.add(source_device_id)
    return device_id in allowed


def _mobile_text_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    if isinstance(value, list | tuple | set):
        items: list[str] = []
        for item in value:
            items.extend(_mobile_text_list(item))
        return items
    text = str(value or "").strip()
    return [text] if text else []
