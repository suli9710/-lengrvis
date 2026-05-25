from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Header, HTTPException, Query, WebSocket

from app.llm.registry import get_effective_settings

TOKEN_AUDIENCE = "mavris-mobile"
TOKEN_ISSUER = "mavris-backend"
TOKEN_SCOPE = "mobile:approval"


def issue_mobile_token(*, device_id: str, device_name: str, expires_in_seconds: int = 60 * 60 * 24 * 30) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "aud": TOKEN_AUDIENCE,
        "device_id": device_id,
        "device_name": device_name,
        "exp": now + timedelta(seconds=expires_in_seconds),
        "iat": now,
        "iss": TOKEN_ISSUER,
        "scope": TOKEN_SCOPE,
        "sub": f"mobile:{device_id}",
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def decode_mobile_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Mobile token expired") from None
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid mobile token") from None

    if payload.get("scope") != TOKEN_SCOPE:
        raise HTTPException(status_code=403, detail="Mobile token scope is not allowed")
    if not payload.get("device_id"):
        raise HTTPException(status_code=401, detail="Invalid mobile token") from None
    return payload


def require_mobile_token(authorization: str = Header(default="")) -> dict[str, Any]:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing mobile bearer token")
    return decode_mobile_token(token)


def mobile_token_from_query(token: str = Query(default="")) -> dict[str, Any]:
    if not token:
        raise HTTPException(status_code=401, detail="Missing mobile token")
    return decode_mobile_token(token)


async def accept_or_close_mobile_websocket(websocket: WebSocket, token: str) -> dict[str, Any] | None:
    try:
        return decode_mobile_token(token)
    except HTTPException as exc:
        await websocket.accept()
        await websocket.send_json({"type": "error", "code": "unauthorized", "message": str(exc.detail)})
        await websocket.close(code=1008)
        return None


def new_device_id() -> str:
    return f"mobile_{secrets.token_hex(8)}"


def _secret() -> str:
    settings = get_effective_settings()
    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail="Mobile JWT secret is not configured")
    return settings.jwt_secret
