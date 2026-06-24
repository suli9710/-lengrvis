"""Commercialization API: plan status, license status, cloud quota, and
Team-tier audit export / policy management.

Entitlement gating is enforced per-endpoint via
:func:`app.commerce.entitlements.require_feature`, which raises an
``EntitlementError`` (HTTP 402) handled by the global AppError handler. Existing
security flows are reused unchanged: audit payloads pass through the same
read-path redaction as ``/api/audit`` and policy imports go through the same
relaxation-confirmation guard as ``/api/settings/permission-policy``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.commerce.entitlements import (
    Feature,
    active_plan,
    has_feature,
    is_high_risk,
    require_feature,
)
from app.commerce.licensing import install_license, license_status
from app.commerce.usage import quota_status
from app.core import audit as audit_core
from app.core import db
from app.llm.registry import get_effective_settings, invalidate_settings_cache
from app.policy.permissions import PermissionPolicy, PermissionStore
from app.policy.redaction import redact_audit_payload
from app.security.sensitive_confirmation import (
    permission_policy_relaxations,
    require_permission_policy_confirmation,
)

router = APIRouter()


class LicenseInstallRequest(BaseModel):
    token: str = Field(min_length=1, max_length=65536)


@router.get("/commerce/plan")
def commerce_plan() -> dict[str, Any]:
    settings = get_effective_settings()
    plan = active_plan(settings)
    return {
        "plan": plan.value,
        "remote_desktop_enabled": bool(getattr(settings, "remote_desktop_enabled", False)),
        "features": {feature.value: has_feature(plan, feature) for feature in Feature},
        "high_risk_features": [feature.value for feature in Feature if is_high_risk(feature)],
    }


@router.get("/commerce/license")
def commerce_license() -> dict[str, Any]:
    return license_status(get_effective_settings())


@router.post("/commerce/license/install")
def commerce_license_install(payload: LicenseInstallRequest) -> dict[str, Any]:
    settings = get_effective_settings()
    license_ = install_license(payload.token, settings)
    audit_core.record(
        "commerce.license.installed",
        "desktop",
        {
            "license_id": license_.license_id,
            "plan": license_.plan.value,
            "subject": license_.subject,
            "issuer": license_.issuer,
            "seats": license_.seats,
            "expires_at": license_.expires_at.isoformat() if license_.expires_at else None,
        },
    )
    invalidate_settings_cache()
    return license_status(settings)


@router.get("/commerce/usage/quota")
def commerce_usage_quota() -> dict[str, Any]:
    return quota_status(get_effective_settings())


def _public_audit_events(events: list[dict]) -> list[dict]:
    result: list[dict] = []
    for event in events:
        item = dict(event)
        payload = item.get("payload")
        if isinstance(payload, dict):
            item["payload"] = redact_audit_payload(payload)
        result.append(item)
    return result


@router.get("/commerce/audit/export")
def commerce_audit_export(limit: int = 500, task_id: str | None = None) -> dict[str, Any]:
    settings = get_effective_settings()
    plan = active_plan(settings)
    require_feature(plan, Feature.AUDIT_EXPORT)
    bounded = max(1, min(5000, int(limit)))
    if task_id:
        events = db.fetch_many("audit_events", "task_id = ?", (task_id,), limit=bounded)
    else:
        events = db.fetch_many("audit_events", limit=bounded)
    exported = _public_audit_events(events)
    return {"plan": plan.value, "count": len(exported), "events": exported}


@router.get("/commerce/policy/export")
def commerce_policy_export() -> dict[str, Any]:
    settings = get_effective_settings()
    plan = active_plan(settings)
    require_feature(plan, Feature.POLICY_MANAGEMENT)
    return {"plan": plan.value, "policy": PermissionStore().get_policy().model_dump(mode="json")}


@router.post("/commerce/policy/import")
def commerce_policy_import(payload: dict, confirmation_nonce: str = Query("")) -> dict[str, Any]:
    settings = get_effective_settings()
    plan = active_plan(settings)
    require_feature(plan, Feature.POLICY_MANAGEMENT)
    store = PermissionStore()
    new_policy = PermissionPolicy.model_validate(payload.get("policy") or payload)
    relaxations = permission_policy_relaxations(store.get_policy(), new_policy)
    require_permission_policy_confirmation(relaxations, confirmation_nonce)
    return {"plan": plan.value, "policy": store.save_policy(new_policy).model_dump(mode="json")}
