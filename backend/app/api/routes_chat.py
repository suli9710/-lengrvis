from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.schemas import AgentMessage, ChatMessage, ChatRequest, ChatResponse
from app.orchestration.agent_bus import AgentBus
from app.orchestration.orchestrator_registry import orchestrator_registry
from app.perception.intent_predictor import IntentSuggestion
from app.security.desktop_api import close_unauthorized_desktop_websocket
from app.services import perception_suggestion_service
from app.services.notification_service import SYSTEM_TASK_ID
from app.services.task_service import handle_chat, list_chat_messages

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
    return perception_suggestion_service.current_suggestions()


@ws_router.websocket("/ws/tasks/{task_id}")
async def task_messages(websocket: WebSocket, task_id: str):
    await _stream_task_messages(websocket, task_id)


@ws_router.websocket("/ws/notifications")
async def notification_messages(websocket: WebSocket):
    await _stream_task_messages(websocket, SYSTEM_TASK_ID)


_HEARTBEAT_SECONDS = 25.0
_BUS_REBIND_POLL_SECONDS = 1.0


async def _stream_task_messages(websocket: WebSocket, task_id: str) -> None:
    if await close_unauthorized_desktop_websocket(websocket):
        return
    await websocket.accept()
    task_bus = orchestrator_registry.bus_for_task(task_id, fallback=bus)
    queue = task_bus.subscribe(task_id)
    sent_message_ids: set[str] = set()
    loop = asyncio.get_running_loop()
    last_heartbeat = loop.time()
    try:
        await websocket.send_json({"type": "connected", "task_id": task_id})
        for message in sorted(task_bus.get_messages(task_id), key=lambda item: (item.created_at, item.id)):
            sent_message_ids.add(message.id)
            await websocket.send_json(_agent_message_event(task_id, message))

        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=_BUS_REBIND_POLL_SECONDS)
                if message.id in sent_message_ids:
                    continue
                sent_message_ids.add(message.id)
                await websocket.send_json(_agent_message_event(task_id, message))
            except TimeoutError:
                # Clients can connect before the orchestrator binds its own bus
                # into the registry; re-resolve on idle so this socket follows
                # the live bus instead of the connect-time fallback (R4-M7).
                current_bus = orchestrator_registry.bus_for_task(task_id, fallback=task_bus)
                if current_bus is not task_bus:
                    task_bus.unsubscribe(task_id, queue)
                    task_bus = current_bus
                    queue = task_bus.subscribe(task_id)
                    for message in sorted(task_bus.get_messages(task_id), key=lambda item: (item.created_at, item.id)):
                        if message.id in sent_message_ids:
                            continue
                        sent_message_ids.add(message.id)
                        await websocket.send_json(_agent_message_event(task_id, message))
                if loop.time() - last_heartbeat >= _HEARTBEAT_SECONDS:
                    last_heartbeat = loop.time()
                    await websocket.send_json({"type": "heartbeat", "task_id": task_id})
    except WebSocketDisconnect:
        return
    finally:
        task_bus.unsubscribe(task_id, queue)


def _agent_message_event(task_id: str, message: AgentMessage) -> dict:
    return {
        "type": "agent_message",
        "task_id": task_id,
        "message": message.to_openai_dict(),
    }
