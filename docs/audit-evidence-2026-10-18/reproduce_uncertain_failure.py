"""Reproduce an ordinary failed non-R0 write making a matter permanently uncertain."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("LENGRVIS_AUDIT_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-uncertain-")

from app.core import butler_store as store  # noqa: E402
from app.core import db  # noqa: E402
from app.core.butler_models import ButlerMatter, MatterStatus  # noqa: E402
from app.core.schemas import Task, ToolCall, ToolResult  # noqa: E402
from app.policy.risk import RiskLevel  # noqa: E402
from app.services.butler_evidence import reviewed_results, uncertain_execution, verify_conditions  # noqa: E402

db.init_db()
task = Task(user_goal="write a report")
db.upsert_model("tasks", task)
matter = store.save(
    ButlerMatter(
        id="matter",
        goal=task.user_goal,
        status=MatterStatus.RUNNING,
        active_task_id=task.id,
        task_ids=[task.id],
        execution_ticket="ticket",
    )
)
call = ToolCall(
    task_id=task.id,
    step_id="step",
    tool_name="file.write_text",
    args={"path": "C:/tmp/not-a-file/report.md", "text": "x"},
    risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
    status="committed",
    dry_run=False,
)
db.upsert_model("tool_calls", call)
result = ToolResult(
    tool_call_id=call.id,
    ok=False,
    output={"error": "Path is not a file."},
    runtime_review_completed=True,
    runtime_review_verdict="allow",
)
db.upsert_model("tool_results", result)
print("reviewed_results=", len(reviewed_results(matter)))
print("uncertain_execution=", uncertain_execution(matter))
import asyncio

print("verify_conditions=", asyncio.run(verify_conditions(matter)))
