from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import threading
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException, Query, WebSocket

TOKEN_AUDIENCE = "mavris-mobile"
TOKEN_ISSUER = "mavris-backend"
_secret_lock = threading.RLock()


def issue_mobile_token(*, device_id: str, device_name: str, expires_in_seconds: int = 60 * 60 * 24 * 30) -> str:
    now = int(time.time())
    payload = {
        "aud": TOKEN_AUDIENCE,
        "device_id": device_id,
        "device_name": device_name,
        "exp": now + expires_in_seconds,
        "iat": now,
        "iss": TOKEN_ISSUER,
        "scope": "mobile:approval",
        "sub": f"mobile:{device_id}",
    }
    return encode_jwt(payload)


def encode_jwt(payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join([_b64_json(header), _b64_json(payload)])
    signature = hmac.new(_secret(), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def decode_mobile_token(token: str) -> dict[str, Any]:
    try:
        header_segment, payload_segment, signature_segment = token.split(".", 2)
        signing_input = f"{header_segment}.{payload_segment}"
        expected = hmac.new(_secret(), signing_input.encode("ascii"), hashlib.sha256).digest()
        actual = _b64_decode(signature_segment)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid mobile token") from None

    if not hmac.compare_digest(actual, expected):
        raise HTTPException(status_code=401, detail="Invalid mobile token")

    try:
        header = json.loads(_b64_decode(header_segment))
        payload = json.loads(_b64_decode(payload_segment))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid mobile token") from None

    if header.get("alg") != "HS256":
        raise HTTPException(status_code=401, detail="Invalid mobile token")
    if payload.get("aud") != TOKEN_AUDIENCE or payload.get("iss") != TOKEN_ISSUER:
        raise HTTPException(status_code=401, detail="Invalid mobile token")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise HTTPException(status_code=401, detail="Mobile token expired")
    if payload.get("scope") != "mobile:approval":
        raise HTTPException(status_code=403, detail="Mobile token scope is not allowed")

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


def _secret() -> bytes:
    configured = os.environ.get("MARVIS_MOBILE_JWT_SECRET") or os.environ.get("MAVRIS_MOBILE_JWT_SECRET")
    if configured:
        return configured.encode("utf-8")
    data_dir = os.environ.get("MARVIS_DATA_DIR") or os.environ.get("MAVRIS_DATA_DIR") or ".marvis_data"
    path = Path(data_dir) / "mobile_jwt_secret"
    with _secret_lock:
        if path.exists():
            return path.read_text(encoding="utf-8").strip().encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(48)
        path.write_text(value, encoding="utf-8")
        return value.encode("utf-8")


def _b64_json(value: dict[str, Any]) -> str:
    return _b64(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def new_device_id() -> str:
    return f"mobile_{secrets.token_hex(8)}"
