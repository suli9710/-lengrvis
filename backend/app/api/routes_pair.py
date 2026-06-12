from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.services import mobile_pairing_service


router = APIRouter()

# Keep the request-schema length/pattern in sync with the generated code entropy.
_PAIR_CODE_LENGTH = mobile_pairing_service.PAIR_CODE_HEX_LENGTH * 2


class PairRedeemRequest(BaseModel):
    code: str = Field(
        min_length=_PAIR_CODE_LENGTH,
        max_length=_PAIR_CODE_LENGTH,
        pattern=rf"^[a-f0-9]{{{_PAIR_CODE_LENGTH}}}$",
    )
    device_name: str = Field(default="Android device", max_length=80)


class RemoteInputGrantRequest(BaseModel):
    expires_in: int = Field(default=mobile_pairing_service.REMOTE_INPUT_GRANT_TTL_SECONDS, ge=1)


@router.post("/pair/request")
def create_pairing_code() -> dict:
    return _with_transport_metadata(mobile_pairing_service.create_pairing_request())


@router.post("/pair/confirm")
def confirm_pairing(payload: PairRedeemRequest, request: Request) -> dict:
    client_host = request.client.host if request.client else ""
    return _with_transport_metadata(
        mobile_pairing_service.confirm_pairing(
            code=payload.code,
            device_name=payload.device_name,
            client_host=client_host,
        )
    )


@router.get("/pair/devices")
def list_paired_devices() -> dict:
    return {"devices": mobile_pairing_service.list_mobile_devices()}


@router.delete("/pair/devices/{device_id}")
def revoke_paired_device(device_id: str) -> dict:
    return mobile_pairing_service.revoke_mobile_device(device_id)


@router.post("/pair/devices/{device_id}/remote-input-grants")
def create_remote_input_grant(device_id: str, payload: RemoteInputGrantRequest | None = None) -> dict:
    expires_in = payload.expires_in if payload else mobile_pairing_service.REMOTE_INPUT_GRANT_TTL_SECONDS
    return mobile_pairing_service.create_remote_input_grant(device_id, expires_in_seconds=expires_in)


@router.delete("/pair/devices/{device_id}/remote-input-grants/{grant_id}")
def revoke_remote_input_grant(device_id: str, grant_id: str) -> dict:
    return mobile_pairing_service.revoke_remote_input_grant(device_id, grant_id)


@router.post("/pair/code")
def create_pairing_code_legacy() -> dict:
    return _with_transport_metadata(mobile_pairing_service.create_pairing_request())


@router.post("/pair")
def pair(payload: PairRedeemRequest, request: Request) -> dict:
    client_host = request.client.host if request.client else ""
    return _with_transport_metadata(
        mobile_pairing_service.confirm_pairing(
            code=payload.code,
            device_name=payload.device_name,
            client_host=client_host,
        )
    )


def _with_transport_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    server = result.get("server") if isinstance(result.get("server"), dict) else {}
    transport = server.get("transport_security") if isinstance(server.get("transport_security"), dict) else None
    if transport is None:
        transport = mobile_pairing_service.lan_transport_security()
    result.setdefault("transport_security", transport)
    result.setdefault("https_enabled", bool(transport.get("https_enabled")))
    result.setdefault("trust_required", bool(transport.get("trust_required")))
    result.setdefault("server_origin", str(server.get("origin") or transport.get("origin") or ""))
    return result
