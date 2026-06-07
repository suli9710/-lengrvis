from __future__ import annotations

from fastapi import APIRouter

from app.core import audit as audit_core, db
from app.core.schemas import AuditChainVerification


router = APIRouter()


@router.get("/audit")
def audit():
    return db.fetch_many("audit_events", limit=500)


@router.get("/audit/verify-chain", response_model=AuditChainVerification)
@router.get("/audit/verify", response_model=AuditChainVerification)
def verify_audit(limit: int | None = None) -> AuditChainVerification:
    return AuditChainVerification.model_validate(audit_core.verify_chain(limit=limit))


@router.get("/audit/{task_id}")
def audit_for_task(task_id: str):
    return db.fetch_many("audit_events", "task_id = ?", (task_id,), limit=500)
