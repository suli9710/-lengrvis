from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.schemas import AgentMessage, ChatMessage, ChatRequest, ChatResponse
from app.orchestration.agent_bus import AgentBus
from app.security.lan import allow_lan_desktop_api, is_loopback_host
from app.services.notification_service import SYSTEM_TASK_ID
from app.services.task_service import handle_chat, list_chat_messages
from app.agents.supervisor_agent import SupervisorAgent
from app.perception.intent_predictor import IntentSuggestion


router = APIRouter()
ws_router = APIRouter()
bus = AgentBus()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    return await handle_chat(request.message, request.mode)


@router.get("/chat/messages", response_model=list[ChatMessage])
def chat_messages() -> list[ChatMessage]:
    return list_chat_messages()


@router.get("/chat/proactive-suggestions", response_model=list[IntentSuggestion])
def proactive_suggestions() -> list[IntentSuggestion]:
    return SupervisorAgent().proactive_suggestions()


@ws_router.websocket("/ws/tasks/{task_id}")
async def task_messages(websocket: WebSocket, task_id: str):
    await _stream_task_messages(websocket, task_id)


@ws_router.websocket("/ws/notifications")
async def notification_messages(websocket: WebSocket):
    await _stream_task_messages(websocket, SYSTEM_TASK_ID)


async def _stream_task_messages(websocket: WebSocket, task_id: str) -> None:
    client_host = websocket.client.host if websocket.client else ""
    if not is_loopback_host(client_host) and not allow_lan_desktop_api():
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue = bus.subscribe(task_id)
    sent_message_ids: set[str] = set()
    try:
        await websocket.send_json({"type": "connected", "task_id": task_id})
        for message in sorted(bus.get_messages(task_id), key=lambda item: (item.created_at, item.id)):
            sent_message_ids.add(message.id)
            await websocket.send_json(_agent_message_event(task_id, message))

        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=25)
                if message.id in sent_message_ids:
                    continue
                sent_message_ids.add(message.id)
                await websocket.send_json(_agent_message_event(task_id, message))
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat", "task_id": task_id})
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(task_id, queue)


def _agent_message_event(task_id: str, message: AgentMessage) -> dict:
    return {
        "type": "agent_message",
        "task_id": task_id,
        "message": message.to_openai_dict(),
    }
