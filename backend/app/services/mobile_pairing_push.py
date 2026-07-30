from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException

from app.core import db
from app.core.audit import record
from app.core.schemas import now_iso
from app.services.mobile_pairing_common import _text


def register_mobile_push_subscription(
    claims: dict[str, Any],
    *,
    provider: str,
    push_token: str,
) -> dict[str, str]:
    device_id = _text(claims.get("device_id"))
    if not device_id:
        raise HTTPException(status_code=401, detail="Mobile token is missing a device binding")
    normalized_provider = _text(provider).lower()
    normalized_token = _text(push_token)
    if normalized_provider != "expo" or not valid_expo_push_token(normalized_token):
        raise HTTPException(status_code=422, detail="Invalid mobile push subscription")
    timestamp = now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (device_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Mobile device is not paired")
        device = json.loads(row["data"])
        if str(device.get("status") or "active").lower() != "active":
            raise HTTPException(status_code=401, detail="Mobile device has been revoked")
        device["push_subscription"] = {
            "provider": normalized_provider,
            "token": normalized_token,
            "updated_at": timestamp,
        }
        device["updated_at"] = timestamp
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device, ensure_ascii=False), timestamp, device_id),
        )
    record(
        "mobile.push_subscription.registered",
        "MobilePairingService",
        {"device_id": device_id, "provider": normalized_provider},
    )
    return {"status": "registered", "provider": normalized_provider}


def unregister_mobile_push_subscription(claims: dict[str, Any]) -> dict[str, str]:
    device_id = _text(claims.get("device_id"))
    if not device_id:
        raise HTTPException(status_code=401, detail="Mobile token is missing a device binding")
    remove_mobile_push_subscription(device_id)
    return {"status": "unregistered"}


def remove_mobile_push_subscription(device_id: str, *, expected_token: str = "") -> bool:
    normalized_id = _text(device_id)
    if not normalized_id:
        return False
    timestamp = now_iso()
    removed = False
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM mobile_devices WHERE id = ?", (normalized_id,)).fetchone()
        if not row:
            return False
        device = json.loads(row["data"])
        subscription = device.get("push_subscription")
        if not isinstance(subscription, dict):
            return False
        if expected_token and _text(subscription.get("token")) != expected_token:
            return False
        device.pop("push_subscription", None)
        device["updated_at"] = timestamp
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device, ensure_ascii=False), timestamp, normalized_id),
        )
        removed = True
    if removed:
        record(
            "mobile.push_subscription.removed",
            "MobilePairingService",
            {"device_id": normalized_id},
        )
    return removed


def valid_expo_push_token(value: str) -> bool:
    return bool(re.fullmatch(r"(?:Expo|Exponent)PushToken\[[A-Za-z0-9_-]{1,200}\]", value))
