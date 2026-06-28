from __future__ import annotations

import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Header, HTTPException, Query, WebSocket

from app.core import db
from app.llm.registry import get_effective_settings
from app.security.websocket_origin import is_trusted_websocket_origin

TOKEN_AUDIENCE = "lengrvis-mobile"  # noqa: S105 - JWT audience label, not a secret.
TOKEN_ISSUER = "lengrvis-backend"  # noqa: S105 - JWT issuer label, not a secret.
TOKEN_SCOPE = "mobile:approval"  # noqa: S105 - OAuth-style scope label, not a secret.
REMOTE_VIEW_SCOPE = "remote:view"
REMOTE_INPUT_SCOPE = "remote:input"
MOBILE_AUTH_WS_PROTOCOL_PREFIX = "lengrvis.mobile.token."
MOBILE_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7
MOBILE_REMOTE_VIEW_TTL_SECONDS = 4 * 60 * 60
# P1-5 fix: Pin the algorithm explicitly in both encode and decode.
# The old code used "HS256" as a bare string, making algorithm confusion
# attacks possible if a future change accidentally broadened the decode list.
# We also pass algorithms as an explicit list to jwt.decode to prevent
# alg=none bypass attacks.  To migrate to EdDSA (Ed25519) in the future:
#   1. Add "EdDSA" to the algorithms list during a transition period.
#   2. Issue new tokens with EdDSA while still accepting HS256.
#   3. After all HS256 tokens expire (7-day TTL), remove "HS256".
MOBILE_JWT_ALGORITHM = "HS256"


def issue_mobile_token(
    *,
    device_id: str,
    device_name: str,
    expires_in_seconds: int = MOBILE_TOKEN_TTL_SECONDS,
    scope: str | Iterable[str] = TOKEN_SCOPE,
    scope_ttl: dict[str, int] | None = None,
    source: str = "",
    grant_id: str = "",
    token_id: str = "",
    token_epoch: int = 0,
) -> str:
    now = datetime.now(UTC)
    scopes = _scope_values(scope)
    jti = token_id or secrets.token_hex(16)
    payload = {
        "aud": TOKEN_AUDIENCE,
        "device_id": device_id,
        "device_name": device_name,
        "exp": now + timedelta(seconds=expires_in_seconds),
        "iat": now,
        "iss": TOKEN_ISSUER,
        "jti": jti,
        "scope": " ".join(scopes),
        "sub": f"mobile:{device_id}",
        "token_epoch": int(token_epoch),
    }
    encoded_scope_exp = _encode_scope_exp(now, scopes, scope_ttl)
    if encoded_scope_exp:
        payload["scope_exp"] = encoded_scope_exp
    if source:
        payload["source"] = source
    if grant_id:
        payload["grant_id"] = grant_id
    return jwt.encode(payload, _secret(), algorithm=MOBILE_JWT_ALGORITHM)


def decode_mobile_token(
    token: str,
    *,
    allowed_scopes: set[str] | None = None,
    require_active_device: bool = True,
) -> dict[str, Any]:
    payload = _decode_mobile_token_payload(token)

    accepted_scopes = allowed_scopes or {TOKEN_SCOPE}
    scopes = mobile_token_scopes(payload)
    active_scopes = scopes.intersection(accepted_scopes)
    if not active_scopes:
        raise HTTPException(status_code=403, detail="Mobile token scope is not allowed")
    _raise_if_scope_expired(payload, active_scopes)
    if not payload.get("device_id"):
        raise HTTPException(status_code=401, detail="Invalid mobile token") from None
    if require_active_device:
        _raise_if_device_inactive(str(payload.get("device_id") or ""), token_epoch=_token_epoch(payload))
        _raise_if_remote_input_grant_inactive(payload, scopes)
    payload["scopes"] = sorted(scopes)
    return payload


def validate_mobile_claims_active(
    claims: dict[str, Any],
    *,
    scope_exp_scopes: set[str] | None = None,
) -> None:
    scopes = mobile_token_scopes(claims)
    _raise_if_token_expired(claims)
    # Only evaluate scope_exp for the scopes relevant to this session (e.g. paired
    # approval notifications should stay alive after remote:view scope_exp expires).
    exp_scopes = scope_exp_scopes if scope_exp_scopes is not None else {TOKEN_SCOPE}
    _raise_if_scope_expired(claims, scopes.intersection(exp_scopes))
    _raise_if_device_inactive(str(claims.get("device_id") or ""), token_epoch=_token_epoch(claims))
    _raise_if_remote_input_grant_inactive(claims, scopes)


def require_mobile_token(authorization: str = Header(default="")) -> dict[str, Any]:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing mobile bearer token")
    claims = decode_mobile_token(token)
    if _is_remote_input_grant_claim(claims):
        raise HTTPException(status_code=403, detail="Remote input grant token is not allowed for mobile resources")
    return claims


def require_mobile_or_remote_input_token(authorization: str = Header(default="")) -> dict[str, Any]:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing mobile bearer token")
    return decode_mobile_token(token, allowed_scopes={TOKEN_SCOPE, REMOTE_INPUT_SCOPE})


def mobile_token_from_query(token: str = Query(default="")) -> dict[str, Any]:
    if not token:
        raise HTTPException(status_code=401, detail="Missing mobile token")
    return decode_mobile_token(token)


def mobile_token_from_websocket(websocket: WebSocket, token: str = "") -> str:
    if not is_trusted_websocket_origin(
        websocket,
        allow_missing_origin_with_token=_has_mobile_token_protocol(websocket),
    ):
        raise HTTPException(status_code=403, detail="WebSocket origin is not allowed")
    query_token = token.strip()
    if query_token:
        raise HTTPException(status_code=401, detail="WebSocket query tokens are not allowed")
    for protocol in _websocket_protocols(websocket):
        if protocol.startswith(MOBILE_AUTH_WS_PROTOCOL_PREFIX):
            candidate = protocol.removeprefix(MOBILE_AUTH_WS_PROTOCOL_PREFIX).strip()
            if candidate:
                return candidate
    return ""


def _has_mobile_token_protocol(websocket: WebSocket) -> bool:
    return any(protocol.startswith(MOBILE_AUTH_WS_PROTOCOL_PREFIX) for protocol in _websocket_protocols(websocket))


async def accept_or_close_mobile_websocket(websocket: WebSocket, token: str) -> dict[str, Any] | None:
    try:
        return decode_mobile_token(mobile_token_from_websocket(websocket, token), allowed_scopes={TOKEN_SCOPE})
    except HTTPException as exc:
        await websocket.accept()
        await websocket.send_json({"type": "error", "code": "unauthorized", "message": str(exc.detail)})
        await websocket.close(code=1008)
        return None


def new_device_id() -> str:
    return f"mobile_{secrets.token_hex(8)}"


def mobile_token_scopes(claims: dict[str, Any]) -> set[str]:
    scopes: set[str] = set()
    scopes.update(_scope_values(claims.get("scope") or ""))
    scopes.update(_scope_values(claims.get("scopes") or []))
    return scopes


def _encode_scope_exp(
    issued_at: datetime,
    scopes: list[str],
    scope_ttl: dict[str, int] | None,
) -> dict[str, int]:
    if not scope_ttl:
        return {}
    encoded: dict[str, int] = {}
    for scope in scopes:
        ttl_seconds = scope_ttl.get(scope)
        if ttl_seconds is None:
            continue
        try:
            normalized_ttl = int(ttl_seconds)
        except (TypeError, ValueError):
            continue
        if normalized_ttl <= 0:
            continue
        encoded[scope] = int((issued_at + timedelta(seconds=normalized_ttl)).timestamp())
    return encoded


def _raise_if_scope_expired(payload: dict[str, Any], scopes: set[str]) -> None:
    scope_exp = payload.get("scope_exp")
    if not isinstance(scope_exp, dict):
        return
    now = datetime.now(UTC)
    for scope in scopes:
        raw_exp = scope_exp.get(scope)
        if raw_exp is None:
            continue
        try:
            if isinstance(raw_exp, datetime):
                expires_at = raw_exp if raw_exp.tzinfo else raw_exp.replace(tzinfo=UTC)
            else:
                expires_at = datetime.fromtimestamp(float(raw_exp), UTC)
        except (TypeError, ValueError, OverflowError, OSError):
            raise HTTPException(status_code=401, detail="Invalid mobile token") from None
        if expires_at <= now:
            raise HTTPException(status_code=401, detail="Mobile token scope expired")


def _scope_values(raw_scope: Any) -> list[str]:
    if raw_scope is None:
        return []
    if isinstance(raw_scope, str):
        candidates = raw_scope.replace(",", " ").split()
    elif isinstance(raw_scope, Iterable):
        candidates = []
        for item in raw_scope:
            candidates.extend(_scope_values(item))
    else:
        candidates = [str(raw_scope)]

    scopes: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        scope = str(candidate or "").strip()
        if not scope or scope in seen:
            continue
        scopes.append(scope)
        seen.add(scope)
    return scopes


def _websocket_protocols(websocket: WebSocket) -> list[str]:
    raw_header = websocket.headers.get("sec-websocket-protocol", "")
    return [item.strip() for item in raw_header.split(",") if item.strip()]


def _decode_mobile_token_payload(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            _secret(),
            # P1-5 fix: Pin algorithm list to ONLY the expected algorithm.
            # This prevents algorithm confusion attacks (e.g. alg=none bypass).
            algorithms=[MOBILE_JWT_ALGORITHM],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Mobile token expired") from None
    except jwt.PyJWTError:
        pass
    raise HTTPException(status_code=401, detail="Invalid mobile token") from None


def _raise_if_device_inactive(device_id: str, *, token_epoch: int | None = None) -> None:
    if not device_id:
        raise HTTPException(status_code=401, detail="Mobile token is missing a device binding")
    device = db.fetch_one("mobile_devices", device_id)
    if not device:
        raise HTTPException(status_code=401, detail="Mobile device is not paired")
    if str(device.get("status") or "active").lower() != "active":
        raise HTTPException(status_code=401, detail="Mobile device has been revoked")
    if token_epoch is not None and int(device.get("token_epoch") or 0) != int(token_epoch):
        raise HTTPException(status_code=401, detail="Mobile session has been revoked")


def _token_epoch(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("token_epoch") or 0)
    except (TypeError, ValueError):
        return 0


def _raise_if_token_expired(payload: dict[str, Any]) -> None:
    raw_exp = payload.get("exp")
    try:
        if isinstance(raw_exp, datetime):
            expires_at = raw_exp if raw_exp.tzinfo else raw_exp.replace(tzinfo=UTC)
        else:
            expires_at = datetime.fromtimestamp(float(raw_exp), UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        raise HTTPException(status_code=401, detail="Invalid mobile token") from None

    if expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=401, detail="Mobile token expired")


def _raise_if_remote_input_grant_inactive(payload: dict[str, Any], scopes: set[str]) -> None:
    if REMOTE_INPUT_SCOPE not in scopes:
        return
    grant_id = str(payload.get("grant_id") or "").strip()
    if not grant_id:
        raise HTTPException(status_code=401, detail="Remote input grant is required")
    if str(payload.get("source") or "") != "remote_input_grant":
        raise HTTPException(status_code=401, detail="Remote input grant source is invalid")
    if not get_effective_settings().remote_desktop_enabled:
        raise HTTPException(status_code=403, detail="Remote desktop is disabled")

    device_id = str(payload.get("device_id") or "")
    device = db.fetch_one("mobile_devices", device_id)
    grants = device.get("remote_input_grants") if isinstance(device, dict) else []
    if not isinstance(grants, list):
        raise HTTPException(status_code=401, detail="Remote input grant is not active")

    now = datetime.now(UTC)
    for grant in grants:
        if not isinstance(grant, dict) or str(grant.get("id") or "") != grant_id:
            continue
        if str(grant.get("status") or "").lower() != "active":
            raise HTTPException(status_code=401, detail="Remote input grant is not active")
        expires_at = _remote_input_grant_expires_at(grant)
        if expires_at < now:
            raise HTTPException(status_code=401, detail="Remote input grant expired")
        bound_token_id = str(grant.get("token_id") or "")
        if bound_token_id and str(payload.get("jti") or "") != bound_token_id:
            raise HTTPException(status_code=401, detail="Remote input grant token has been superseded")
        return

    raise HTTPException(status_code=401, detail="Remote input grant is not active")


def _remote_input_grant_expires_at(grant: dict[str, Any]) -> datetime:
    try:
        expires_at = datetime.fromisoformat(str(grant.get("expires_at") or ""))
    except ValueError:
        raise HTTPException(status_code=401, detail="Remote input grant is invalid") from None
    return expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)


def _is_remote_input_grant_claim(claims: dict[str, Any]) -> bool:
    return (
        REMOTE_INPUT_SCOPE in mobile_token_scopes(claims)
        and str(claims.get("source") or "") == "remote_input_grant"
        and bool(str(claims.get("grant_id") or "").strip())
    )


def _secret() -> str:
    settings = get_effective_settings()
    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail="Mobile JWT secret is not configured")
    return settings.jwt_secret
