from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.config_sources import env_flag
from app.security.mobile_jwt import mobile_token_scopes
from app.services.mobile_pairing_common import _text


def mobile_approval_auth_context(claims: dict[str, Any]) -> dict[str, Any]:
    try:
        token_epoch = int(claims.get("token_epoch") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Mobile token epoch is invalid") from exc
    raw_family_generation = claims.get("family_generation")
    if raw_family_generation is None and env_flag("LENGRVIS_TEST"):
        family_generation = None
    elif (
        isinstance(raw_family_generation, bool)
        or not isinstance(raw_family_generation, int)
        or raw_family_generation < 0
    ):
        raise HTTPException(status_code=401, detail="Mobile token family generation is invalid")
    else:
        family_generation = raw_family_generation
    step_up = claims.get("step_up") if isinstance(claims.get("step_up"), dict) else {}
    confirmation = claims.get("cnf") if isinstance(claims.get("cnf"), dict) else {}
    raw_authentication_methods = claims.get("amr", [])
    if not isinstance(raw_authentication_methods, list) or not all(
        isinstance(method, str) and method.strip() for method in raw_authentication_methods
    ):
        raise HTTPException(status_code=401, detail="Mobile authentication methods are invalid")
    raw_verified_at = step_up.get("verified_at") or 0
    raw_expires_at = step_up.get("expires_at") or 0
    if isinstance(raw_verified_at, bool) or isinstance(raw_expires_at, bool):
        raise HTTPException(status_code=401, detail="Mobile biometric step-up timestamps are invalid")
    try:
        step_up_verified_at = int(raw_verified_at)
        step_up_expires_at = int(raw_expires_at)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=401, detail="Mobile biometric step-up timestamps are invalid") from exc
    context = {
        "channel": "mobile",
        "device_id": _text(claims.get("device_id")),
        "token_family_id": _text(claims.get("family_id")),
        "credential_id": _text(claims.get("credential_id")),
        "token_epoch": token_epoch,
        "scopes": sorted(mobile_token_scopes(claims)),
        "token_id": _text(claims.get("jti")),
        "step_up_method": _text(step_up.get("method")).casefold(),
        "step_up_verified_at": step_up_verified_at,
        "step_up_expires_at": step_up_expires_at,
        "authentication_methods": sorted(
            {_text(method).casefold() for method in raw_authentication_methods if _text(method)}
        ),
        "proof_thumbprint": _text(confirmation.get("jkt")),
    }
    if family_generation is not None:
        context["family_generation"] = family_generation
    return context
