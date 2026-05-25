from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.schemas import ChatMessage, ChatRequest, ChatResponse
from app.orchestration.agent_bus import AgentBus
from app.security.lan import allow_lan_desktop_api, is_loopback_host
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


@ws_router.websocket("/ws/tasks/{task_id}")
async def task_messages(websocket: WebSocket, task_id: str):
    client_host = websocket.client.host if websocket.client else ""
    if not is_loopback_host(client_host) and not allow_lan_desktop_api():
        await websocket.close(code=1008)
        return
    await websocket.accept()
    queue = bus.subscribe(task_id)
    try:
        await websocket.send_json({"type": "connected", "task_id": task_id})
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=25)
                await websocket.send_json(
                    {
                        "type": "agent_message",
                        "task_id": task_id,
                        "message": message.to_openai_dict(),
                    }
                )
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat", "task_id": task_id})
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(task_id, queue)
