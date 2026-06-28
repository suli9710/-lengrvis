from __future__ import annotations

from fastapi import APIRouter

from app.commerce.entitlements import Feature, active_plan, require_feature
from app.core import audit as audit_core, db
from app.core.schemas import AuditChainVerification
from app.llm.registry import get_effective_settings
from app.policy.redaction import redact_audit_payload


router = APIRouter()


def _public_audit_events(events: list[dict]) -> list[dict]:
    result: list[dict] = []
    for event in events:
        item = dict(event)
        payload = item.get("payload")
        if isinstance(payload, dict):
            # Read-path scrub also removes local absolute paths / file names that
            # the write-path sanitizer (redact_value) intentionally leaves intact.
            item["payload"] = redact_audit_payload(payload)
        result.append(item)
    return result


@router.get("/audit")
def audit():
    require_feature(active_plan(get_effective_settings()), Feature.AUDIT_EXPORT)
    return _public_audit_events(db.fetch_many("audit_events", limit=500))


@router.get("/audit/verify-chain", response_model=AuditChainVerification)
@router.get("/audit/verify", response_model=AuditChainVerification)
def verify_audit(limit: int | None = None) -> AuditChainVerification:
    return AuditChainVerification.model_validate(audit_core.verify_chain(limit=limit))


@router.get("/audit/plan-quality/risk-consistency")
def plan_risk_consistency(limit: int = 500):
    """Offline planner-quality metric: model-supplied vs derived risk levels (playbook P5)."""
    from app.services.plan_quality_service import risk_annotation_consistency

    return risk_annotation_consistency(limit=limit)


@router.get("/audit/{task_id}")
def audit_for_task(task_id: str):
    require_feature(active_plan(get_effective_settings()), Feature.AUDIT_EXPORT)
    return _public_audit_events(db.fetch_many("audit_events", "task_id = ?", (task_id,), limit=500))
