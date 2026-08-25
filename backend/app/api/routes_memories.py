from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents.memory_agent import MemoryAgent
from app.core.memory_namespace import (
    DEFAULT_MEMORY_DOMAIN_SCOPE,
    DEFAULT_MEMORY_PRINCIPAL_ID,
    DEFAULT_MEMORY_WORKSPACE_ID,
    MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
)
from app.core.schemas import Memory, MemoryConflictStatus

router = APIRouter()


class RememberRequest(BaseModel):
    content: str
    principal_id: str = Field(
        default=DEFAULT_MEMORY_PRINCIPAL_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    )
    workspace_id: str = Field(
        default=DEFAULT_MEMORY_WORKSPACE_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    )
    domain_scope: str = Field(
        default=DEFAULT_MEMORY_DOMAIN_SCOPE,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    )
    kind: str = "fact"
    version: int | None = Field(default=None, ge=1)
    supersedes: str = ""
    conflict_status: MemoryConflictStatus = MemoryConflictStatus.NONE
    tags: list[str] = Field(default_factory=list)
    task_id: str = ""
    source: str = "user"
    ttl_seconds: int | None = None


class RecallQuery(BaseModel):
    query: str
    principal_id: str = Field(
        default=DEFAULT_MEMORY_PRINCIPAL_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    )
    workspace_id: str = Field(
        default=DEFAULT_MEMORY_WORKSPACE_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    )
    domain_scope: str = Field(
        default=DEFAULT_MEMORY_DOMAIN_SCOPE,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    )
    k: int = 5
    kind: str | None = None
    tags: list[str] = Field(default_factory=list)


class MemoryReviewRequest(BaseModel):
    reviewed_by: str = "user"
    principal_id: str = Field(
        default=DEFAULT_MEMORY_PRINCIPAL_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    )
    workspace_id: str = Field(
        default=DEFAULT_MEMORY_WORKSPACE_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    )
    domain_scope: str = Field(
        default=DEFAULT_MEMORY_DOMAIN_SCOPE,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    )
    conflict_status: MemoryConflictStatus | None = None


_agent_singleton: MemoryAgent | None = None


def _agent() -> MemoryAgent:
    global _agent_singleton
    if _agent_singleton is None:
        _agent_singleton = MemoryAgent()
    return _agent_singleton


@router.get("/memories")
def list_memories(
    principal_id: str = Query(
        default=DEFAULT_MEMORY_PRINCIPAL_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    ),
    workspace_id: str = Query(
        default=DEFAULT_MEMORY_WORKSPACE_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    ),
    domain_scope: str = Query(
        default=DEFAULT_MEMORY_DOMAIN_SCOPE,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    ),
    kind: str | None = Query(default=None),
) -> list[Memory]:
    return _agent().list_all(
        principal_id=principal_id,
        workspace_id=workspace_id,
        domain_scope=domain_scope,
        kind=kind,
    )


@router.post("/memories")
async def remember(payload: RememberRequest) -> Memory:
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="content cannot be empty")
    try:
        return await _agent().remember(
            payload.content,
            task_id=payload.task_id,
            kind=payload.kind,
            version=payload.version,
            supersedes=payload.supersedes,
            conflict_status=payload.conflict_status,
            tags=payload.tags,
            source=payload.source,
            user_confirmed=True,
            ttl_seconds=payload.ttl_seconds,
            principal_id=payload.principal_id,
            workspace_id=payload.workspace_id,
            domain_scope=payload.domain_scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/memories/recall")
async def recall(payload: RecallQuery) -> list[Memory]:
    return await _agent().recall(
        payload.query,
        k=payload.k,
        tags=payload.tags or None,
        kind=payload.kind,
        principal_id=payload.principal_id,
        workspace_id=payload.workspace_id,
        domain_scope=payload.domain_scope,
    )


@router.post("/memories/{memory_id}/promote")
def promote(memory_id: str, payload: MemoryReviewRequest | None = None) -> Memory:
    review = payload or MemoryReviewRequest()
    memory = _agent().promote(
        memory_id,
        reviewed_by=review.reviewed_by,
        conflict_status=review.conflict_status,
        principal_id=review.principal_id,
        workspace_id=review.workspace_id,
        domain_scope=review.domain_scope,
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.post("/memories/{memory_id}/revoke")
def revoke(memory_id: str, payload: MemoryReviewRequest | None = None) -> Memory:
    review = payload or MemoryReviewRequest()
    memory = _agent().revoke(
        memory_id,
        reviewed_by=review.reviewed_by,
        principal_id=review.principal_id,
        workspace_id=review.workspace_id,
        domain_scope=review.domain_scope,
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.delete("/memories/{memory_id}")
def forget(
    memory_id: str,
    principal_id: str = Query(
        default=DEFAULT_MEMORY_PRINCIPAL_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    ),
    workspace_id: str = Query(
        default=DEFAULT_MEMORY_WORKSPACE_ID,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    ),
    domain_scope: str = Query(
        default=DEFAULT_MEMORY_DOMAIN_SCOPE,
        min_length=1,
        max_length=MAX_MEMORY_NAMESPACE_COMPONENT_LENGTH,
    ),
) -> dict:
    ok = _agent().forget(
        memory_id,
        principal_id=principal_id,
        workspace_id=workspace_id,
        domain_scope=domain_scope,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True, "id": memory_id}
