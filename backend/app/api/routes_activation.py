"""Public subscription activation endpoint for the activation server."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.commerce.activation import (
    ActivationRequest,
    activate_subscription_key,
    enforce_activation_rate_limit,
)

router = APIRouter()


class ActivationApiRequest(BaseModel):
    activation_key: str = Field(min_length=1, max_length=256)
    device_id: str = Field(min_length=1, max_length=128)
    device_fingerprint: str = Field(default="", max_length=128)
    device_profile: dict[str, Any] = Field(default_factory=dict)
    app_version: str = Field(default="", max_length=64)
    nonce: str = Field(default="", max_length=128)


@router.post("/v1/activations")
def create_activation(payload: ActivationApiRequest, request: Request) -> dict[str, Any]:
    client_host = request.client.host if request.client else "unknown"
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
    return {
        "license_token": result.license_token,
        "license_id": result.license_id,
        "plan": result.plan.value,
        "subscription_id": result.subscription_id,
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
        "renews_at": result.renews_at.isoformat() if result.renews_at else None,
        "reused_device": result.reused_device,
    }
