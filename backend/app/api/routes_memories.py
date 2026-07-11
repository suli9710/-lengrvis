from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.memory_agent import MemoryAgent
from app.core.schemas import Memory

router = APIRouter()


class RememberRequest(BaseModel):
    content: str
    kind: str = "fact"
    tags: list[str] = Field(default_factory=list)
    task_id: str = ""
    source: str = "user"
    ttl_seconds: int | None = None


class RecallQuery(BaseModel):
    query: str
    k: int = 5
    tags: list[str] = Field(default_factory=list)


class MemoryReviewRequest(BaseModel):
    reviewed_by: str = "user"


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
        user_confirmed=True,
        ttl_seconds=payload.ttl_seconds,
    )


@router.post("/memories/recall")
async def recall(payload: RecallQuery) -> list[Memory]:
    return await _agent().recall(payload.query, k=payload.k, tags=payload.tags or None)


@router.post("/memories/{memory_id}/promote")
def promote(memory_id: str, payload: MemoryReviewRequest | None = None) -> Memory:
    memory = _agent().promote(memory_id, reviewed_by=payload.reviewed_by if payload else "user")
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.post("/memories/{memory_id}/revoke")
def revoke(memory_id: str, payload: MemoryReviewRequest | None = None) -> Memory:
    memory = _agent().revoke(memory_id, reviewed_by=payload.reviewed_by if payload else "user")
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.delete("/memories/{memory_id}")
def forget(memory_id: str) -> dict:
    ok = _agent().forget(memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True, "id": memory_id}
