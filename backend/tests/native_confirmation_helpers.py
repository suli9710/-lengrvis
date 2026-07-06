from __future__ import annotations

import base64
import time
from typing import Any
from uuid import uuid4

from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_ID_HEADER,
    NATIVE_CONFIRMATION_SIGNATURE_HEADER,
    NATIVE_CONFIRMATION_TIMESTAMP_HEADER,
    native_confirmation_signature,
)

TEST_NATIVE_CONFIRMATION_SECRET = "test-native-confirmation-secret"  # noqa: S105


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def native_confirmation_headers(action: str, approval_id: str, *, endpoint: str | None = None) -> dict[str, str]:
    confirmation_id = f"test-{uuid4().hex}"
    timestamp = str(int(time.time()))
    bound_endpoint = endpoint or f"/api/approvals/{approval_id}/{action}"
    return {
        NATIVE_CONFIRMATION_ID_HEADER: confirmation_id,
        NATIVE_CONFIRMATION_TIMESTAMP_HEADER: timestamp,
        NATIVE_CONFIRMATION_SIGNATURE_HEADER: native_confirmation_signature(
            secret=TEST_NATIVE_CONFIRMATION_SECRET,
            action=action,
            endpoint=bound_endpoint,
            approval_id=approval_id,
            confirmation_id=confirmation_id,
            timestamp=timestamp,
        ),
    }


def signed_native_confirmation_headers(challenge: dict[str, Any], private_key: Any) -> dict[str, str]:
    signature = private_key.sign(str(challenge["signing_payload"]).encode("utf-8"))
    return {
        NATIVE_CONFIRMATION_ID_HEADER: str(challenge["confirmation_id"]),
        NATIVE_CONFIRMATION_TIMESTAMP_HEADER: str(challenge["expires_at_epoch"]),
        NATIVE_CONFIRMATION_SIGNATURE_HEADER: _b64url(signature),
    }
