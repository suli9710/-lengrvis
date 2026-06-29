"""Public subscription activation endpoint for the activation server."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.commerce.activation import (
    ActivationError,
    ActivationRefreshRequest,
    ActivationRequest,
    activate_subscription_key,
    enforce_activation_rate_limit,
    record_activation_audit,
    refresh_subscription_license,
)

router = APIRouter()


class ActivationApiRequest(BaseModel):
    activation_key: str = Field(min_length=1, max_length=256)
    device_id: str = Field(min_length=1, max_length=128)
    device_fingerprint: str = Field(default="", max_length=128)
    device_profile: dict[str, Any] = Field(default_factory=dict)
    app_version: str = Field(default="", max_length=64)
    nonce: str = Field(min_length=16, max_length=128)


class LicenseRefreshApiRequest(BaseModel):
    license_token: str = Field(min_length=1, max_length=65536)
    device_id: str = Field(min_length=1, max_length=128)
    device_fingerprint: str = Field(default="", max_length=128)
    device_profile: dict[str, Any] = Field(default_factory=dict)
    app_version: str = Field(default="", max_length=64)
    nonce: str = Field(min_length=16, max_length=128)


@router.post("/v1/activations")
def create_activation(payload: ActivationApiRequest, request: Request) -> dict[str, Any]:
    client_host = request.client.host if request.client else "unknown"
    client_ref = _client_ref(client_host)
    try:
        enforce_activation_rate_limit(client_host)
        result = activate_subscription_key(
            ActivationRequest(
                activation_key=payload.activation_key,
                device_id=payload.device_id,
                device_fingerprint=payload.device_fingerprint,
                device_profile=payload.device_profile,
                app_version=payload.app_version,
                nonce=payload.nonce,
            )
        )
    except ActivationError as exc:
        record_activation_audit("activation.license.failed", code=exc.code, client_ref=client_ref)
        raise
    record_activation_audit("activation.license.issued", result=result, client_ref=client_ref)
    return {
        "license_token": result.license_token,
        "license_id": result.license_id,
        "plan": result.plan.value,
        "subscription_id": result.subscription_id,
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
        "renews_at": result.renews_at.isoformat() if result.renews_at else None,
        "reused_device": result.reused_device,
    }


@router.post("/v1/licenses/refresh")
def refresh_license(payload: LicenseRefreshApiRequest, request: Request) -> dict[str, Any]:
    client_host = request.client.host if request.client else "unknown"
    client_ref = _client_ref(client_host)
    try:
        enforce_activation_rate_limit(f"refresh:{client_host}")
        result = refresh_subscription_license(
            ActivationRefreshRequest(
                license_token=payload.license_token,
                device_id=payload.device_id,
                device_fingerprint=payload.device_fingerprint,
                device_profile=payload.device_profile,
                app_version=payload.app_version,
                nonce=payload.nonce,
            )
        )
    except ActivationError as exc:
        record_activation_audit("activation.license.refresh_failed", code=exc.code, client_ref=client_ref)
        raise
    record_activation_audit("activation.license.refreshed", result=result, client_ref=client_ref)
    return {
        "license_token": result.license_token,
        "license_id": result.license_id,
        "plan": result.plan.value,
        "subscription_id": result.subscription_id,
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
        "renews_at": result.renews_at.isoformat() if result.renews_at else None,
        "reused_device": result.reused_device,
    }


def _client_ref(client_host: str) -> str:
    normalized = str(client_host or "unknown").strip().lower() or "unknown"
    return "client_" + sha256(normalized.encode("utf-8")).hexdigest()[:16]
