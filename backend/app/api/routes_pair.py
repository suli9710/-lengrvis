from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import mobile_pairing_service


router = APIRouter()


class PairRedeemRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)
    device_name: str = Field(default="Android device", max_length=80)


@router.post("/pair/request")
def create_pairing_code() -> dict:
    return mobile_pairing_service.create_pairing_request()


@router.post("/pair/confirm")
def confirm_pairing(request: PairRedeemRequest) -> dict:
    return mobile_pairing_service.confirm_pairing(code=request.code, device_name=request.device_name)


@router.get("/pair/devices")
def list_paired_devices() -> dict:
    return {"devices": mobile_pairing_service.list_mobile_devices()}


@router.post("/pair/code")
def create_pairing_code_legacy() -> dict:
    return mobile_pairing_service.create_pairing_request()


@router.post("/pair")
def pair(request: PairRedeemRequest) -> dict:
    return mobile_pairing_service.confirm_pairing(code=request.code, device_name=request.device_name)
