from __future__ import annotations

import base64
import binascii
import fnmatch
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.automation.models import IntentCapsule, SignedIntentCapsule, parse_utc
from app.core import audit, db
from app.core.content_provenance import stable_content_hash
from app.core.schemas import now_iso
from app.security.local_secret import load_or_create_local_secret

INTENT_CAPSULE_SECRET_FILE = "intent_capsule.secret"  # noqa: S105
DEFAULT_INTENT_TTL_SECONDS = 15 * 60
MAX_INTENT_TTL_SECONDS = 60 * 60


class IntentCapsuleError(ValueError):
    pass


def user_goal_digest(goal: str) -> str:
    return stable_content_hash(str(goal or ""))


def issue_intent_capsule(
    *,
    task_id: str,
    user_goal: str,
    plan_revision: int,
    allowed_tools: list[str],
    resource_scope: list[str],
    data_egress_scope: list[str],
    policy_version: str,
    ttl_seconds: int = DEFAULT_INTENT_TTL_SECONDS,
) -> SignedIntentCapsule:
    if not 60 <= int(ttl_seconds) <= MAX_INTENT_TTL_SECONDS:
        raise ValueError(f"intent capsule TTL must be between 60 and {MAX_INTENT_TTL_SECONDS} seconds")
    now = datetime.now(UTC)
    capsule = IntentCapsule(
        task_id=task_id,
        user_goal_digest=user_goal_digest(user_goal),
        plan_revision=plan_revision,
        allowed_tools=allowed_tools,
        resource_scope=resource_scope,
        data_egress_scope=data_egress_scope,
        policy_version=policy_version,
        expires_at=(now + timedelta(seconds=int(ttl_seconds))).isoformat(),
        nonce=secrets.token_urlsafe(24),
    )
    token = _encode_token(capsule)
    _store_capsule(capsule)
    audit.record(
        "intent_capsule.issued",
        "IntentCapsuleService",
        {
            "capsule_id": capsule.id,
            "task_id": task_id,
            "plan_revision": plan_revision,
            "allowed_tools": capsule.allowed_tools,
            "resource_scope_count": len(capsule.resource_scope),
            "data_egress_scope": capsule.data_egress_scope,
            "expires_at": capsule.expires_at,
        },
        task_id=task_id,
    )
    return SignedIntentCapsule(capsule=capsule, token=token)


def verify_intent_capsule(
    token: str,
    *,
    task_id: str,
    user_goal: str,
    plan_revision: int,
    policy_version: str,
    tool_name: str = "",
    resource: str = "",
    data_egress: str = "",
    now: datetime | None = None,
) -> IntentCapsule:
    capsule = _decode_token(token)
    current = now or datetime.now(UTC)
    if capsule.task_id != task_id:
        raise IntentCapsuleError("intent capsule task does not match")
    if capsule.user_goal_digest != user_goal_digest(user_goal):
        raise IntentCapsuleError("intent capsule goal digest does not match")
    if capsule.plan_revision != int(plan_revision):
        raise IntentCapsuleError("intent capsule plan revision does not match")
    if capsule.policy_version != policy_version:
        raise IntentCapsuleError("intent capsule policy version does not match")
    stored = _load_capsule(capsule.id)
    if stored is None:
        raise IntentCapsuleError("intent capsule is not registered")
    if capsule.status != "active":
        raise IntentCapsuleError(f"intent capsule status is {capsule.status}")
    if stored.status != "active":
        raise IntentCapsuleError(f"stored intent capsule status is {stored.status}")
    if _capsule_payload(stored) != _capsule_payload(capsule):
        raise IntentCapsuleError("stored intent capsule does not match token")
    if parse_utc(capsule.expires_at) <= current:
        _set_capsule_status(stored, "expired")
        raise IntentCapsuleError("intent capsule has expired")
    if tool_name and not _matches_scope(tool_name, capsule.allowed_tools):
        raise IntentCapsuleError("tool is outside the intent capsule scope")
    if resource and not _matches_scope(resource, capsule.resource_scope):
        raise IntentCapsuleError("resource is outside the intent capsule scope")
    if data_egress and not _matches_scope(data_egress, capsule.data_egress_scope):
        raise IntentCapsuleError("data egress is outside the intent capsule scope")
    return capsule


def revoke_intent_capsule(capsule_id: str) -> IntentCapsule | None:
    capsule = _load_capsule(capsule_id)
    if capsule is None:
        return None
    if capsule.status != "revoked":
        capsule.status = "revoked"
        capsule.updated_at = now_iso()
        _store_capsule(capsule)
        audit.record(
            "intent_capsule.revoked",
            "IntentCapsuleService",
            {"capsule_id": capsule.id},
            task_id=capsule.task_id,
        )
    return capsule


def _matches_scope(value: str, patterns: list[str]) -> bool:
    normalized = str(value or "").strip().casefold()
    return any(fnmatch.fnmatchcase(normalized, pattern.casefold()) for pattern in patterns)


def _secret() -> str:
    secret_path = db.db_path().parent / "secrets" / INTENT_CAPSULE_SECRET_FILE
    return load_or_create_local_secret(secret_path, unavailable_message="Intent capsule secret is unavailable.")


def _capsule_payload(capsule: IntentCapsule) -> bytes:
    return json.dumps(
        capsule.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _encode_token(capsule: IntentCapsule) -> str:
    payload = _capsule_payload(capsule)
    signature = hmac.new(_secret().encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def _decode_token(token: str) -> IntentCapsule:
    parts = str(token or "").split(".")
    if len(parts) != 2:
        raise IntentCapsuleError("intent capsule token is malformed")
    try:
        payload = _b64decode(parts[0])
    except (ValueError, TypeError) as exc:
        raise IntentCapsuleError("intent capsule token encoding is invalid") from exc
    try:
        _b64decode(parts[1])
    except (ValueError, TypeError) as exc:
        raise IntentCapsuleError("intent capsule signature is invalid") from exc
    expected = _b64encode(hmac.new(_secret().encode("utf-8"), payload, hashlib.sha256).digest())
    if not hmac.compare_digest(parts[1], expected):
        raise IntentCapsuleError("intent capsule signature is invalid")
    try:
        raw: Any = json.loads(payload.decode("utf-8"))
        return IntentCapsule.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise IntentCapsuleError("intent capsule payload is invalid") from exc


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    if not value or "=" in value:
        raise ValueError("base64url value must be unpadded")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("base64url value is invalid") from exc
    if not hmac.compare_digest(_b64encode(decoded), value):
        raise ValueError("base64url value is not canonical")
    return decoded


def _store_capsule(capsule: IntentCapsule) -> None:
    db.init_db()
    payload = capsule.model_dump_json()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO intent_capsules (
                id, task_id, plan_revision, status, expires_at, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                expires_at=excluded.expires_at,
                data=excluded.data,
                updated_at=excluded.updated_at
            """,
            (
                capsule.id,
                capsule.task_id,
                capsule.plan_revision,
                capsule.status,
                capsule.expires_at,
                payload,
                capsule.created_at,
                capsule.updated_at,
            ),
        )


def _load_capsule(capsule_id: str) -> IntentCapsule | None:
    db.init_db()
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM intent_capsules WHERE id = ?", (capsule_id,)).fetchone()
    return IntentCapsule.model_validate_json(row["data"]) if row else None


def _set_capsule_status(capsule: IntentCapsule, status: str) -> None:
    capsule.status = status  # type: ignore[assignment]
    capsule.updated_at = now_iso()
    _store_capsule(capsule)
