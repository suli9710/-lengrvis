"""Reproduce sensitive-integrity lockout after retention cleanup of approvals."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta

os.environ.setdefault("LENGRVIS_AUDIT_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-retention-")

from app.core import db  # noqa: E402
from app.core.schemas import Approval, Task, TaskPhase  # noqa: E402
from app.services.local_retention_service import cleanup_expired_task_details  # noqa: E402

now = datetime(2026, 10, 18, tzinfo=UTC)
old = (now - timedelta(days=60)).isoformat()
db.init_db()
db.bootstrap_sensitive_record_integrity()

task = Task(user_goal="old task", status=TaskPhase.COMPLETED, created_at=old)
db.upsert_model("tasks", task)
approval = Approval(
    task_id=task.id,
    message="old approval",
    tool_name="file.write_text",
    source="desktop",
    created_at=old,
)
db.upsert_model("approvals", approval)
with db.connect() as conn:
    conn.execute("UPDATE tasks SET created_at = ?, updated_at = ? WHERE id = ?", (old, old, task.id))
print("integrity_before", db.sensitive_integrity_check()["ok"])
result = cleanup_expired_task_details(now=now, retention_days=1, vacuum=False)
print("cleanup_approvals_deleted", result.get("approvals_deleted"))
check = db.sensitive_integrity_check()
print("integrity_after_ok", check["ok"])
for failure in check.get("failures", [])[:3]:
    print("failure", failure)
try:
    db.require_sensitive_integrity_ok()
    print("require_ok=yes")
except Exception as exc:  # noqa: BLE001 - probe
    print("require_ok", type(exc).__name__, exc)
