"""Reproduce permission-policy lockout after include_settings local erase."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("LENGRVIS_AUDIT_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-erase-policy-")

from app.core import db  # noqa: E402
from app.policy.permissions import PermissionStore  # noqa: E402

db.init_db()
db.bootstrap_sensitive_record_integrity()
print("policy_before_erase", PermissionStore().get_policy().id)
db.erase_local_user_data(include_settings=True)
for attempt in (1, 2):
    try:
        policy = PermissionStore().get_policy()
        print(f"policy_after_erase_attempt{attempt}=ok:{policy.id}")
    except Exception as exc:  # noqa: BLE001 - probe
        print(f"policy_after_erase_attempt{attempt}={type(exc).__name__}:{exc}")
    if attempt == 1:
        result = db.bootstrap_sensitive_record_integrity()
        print(f"rebootstrap_ok={result.get('ok')} checked={result.get('checked')}")
