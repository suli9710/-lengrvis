from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.memory_agent import MemoryAgent
from app.core.schemas import Memory


router = APIRouter()


class RememberRequest(BaseModel):
    content: str
    kind: str = "fact"
    tags: list[str] = []
    task_id: str = ""
    source: str = "user"


class RecallQuery(BaseModel):
    query: str
    k: int = 5
    tags: list[str] = []


_agent_singleton: MemoryAgent | None = None


def _agent() -> MemoryAgent:
    global _agent_singleton
    if _agent_singleton is None:
        _agent_singleton = MemoryAgent()
    return _agent_singleton


@router.get("/memories")
def list_memories() -> list[Memory]:
    return _agent().list_all()


@router.post("/memories")
async def remember(payload: RememberRequest) -> Memory:
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="content cannot be empty")
    return await _agent().remember(
        payload.content,
        task_id=payload.task_id,
        kind=payload.kind,
        tags=payload.tags,
        source=payload.source,
    )


@router.post("/memories/recall")
async def recall(payload: RecallQuery) -> list[Memory]:
    return await _agent().recall(payload.query, k=payload.k, tags=payload.tags or None)


@router.delete("/memories/{memory_id}")
def forget(memory_id: str) -> dict:
    ok = _agent().forget(memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True, "id": memory_id}
