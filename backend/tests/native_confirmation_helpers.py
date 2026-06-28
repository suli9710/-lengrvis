from __future__ import annotations

import time
from uuid import uuid4

from app.security.native_confirmation import (
    NATIVE_CONFIRMATION_ID_HEADER,
    NATIVE_CONFIRMATION_SIGNATURE_HEADER,
    NATIVE_CONFIRMATION_TIMESTAMP_HEADER,
    native_confirmation_signature,
)

TEST_NATIVE_CONFIRMATION_SECRET = "test-native-confirmation-secret"  # noqa: S105


def native_confirmation_headers(action: str, approval_id: str) -> dict[str, str]:
    confirmation_id = f"test-{uuid4().hex}"
    timestamp = str(int(time.time()))
    return {
        NATIVE_CONFIRMATION_ID_HEADER: confirmation_id,
        NATIVE_CONFIRMATION_TIMESTAMP_HEADER: timestamp,
        NATIVE_CONFIRMATION_SIGNATURE_HEADER: native_confirmation_signature(
            secret=TEST_NATIVE_CONFIRMATION_SECRET,
            action=action,
            approval_id=approval_id,
            confirmation_id=confirmation_id,
            timestamp=timestamp,
        ),
    }
