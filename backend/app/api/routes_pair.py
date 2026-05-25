from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter

from app.services import mobile_pairing_service


router = APIRouter()


class PairRedeemRequest(BaseModel):
    code: str = Field(min_length=6, max_length=12)
    device_name: str = Field(default="Android device", max_length=80)


@router.post("/pair/code")
def create_pairing_code() -> dict:
    return mobile_pairing_service.create_pairing_code()


@router.post("/pair")
def pair(request: PairRedeemRequest) -> dict:
    return mobile_pairing_service.redeem_pairing_code(code=request.code, device_name=request.device_name)
