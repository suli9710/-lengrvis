"""Reproduce submission-ledger bypass when browser.act kind is not lowercase."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("LENGRVIS_APPROVAL_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-kind-")

from app.core import butler_store as store  # noqa: E402
from app.core import db  # noqa: E402
from app.core.butler_models import ButlerMatter, MatterStatus  # noqa: E402
from app.core.schemas import Task, ToolCall  # noqa: E402
from app.policy.risk import RiskLevel  # noqa: E402
from app.services.browser_activity_runtime import _normalize_action  # noqa: E402
from app.services.butler_effects import repeated_submission  # noqa: E402
from app.services.butler_evidence import is_submission  # noqa: E402

db.init_db()
task = Task(user_goal="submit once")
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
args = {"action": {"kind": "Submit", "url": "https://example.com/form", "selector": "#f", "fields": {"x": "1"}}}
print("normalized_kind=", _normalize_action(args)["kind"])
print("is_submission_prior=", is_submission({"tool_name": "browser.act", "args": args}))
call = ToolCall(
    task_id=task.id,
    step_id="step",
    tool_name="browser.act",
    args={**args, "dry_run": False},
    risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    status="committed",
    dry_run=False,
)
db.upsert_model("tool_calls", call)
later = {"action": {"kind": "submit", "url": "https://example.com/form", "selector": "#f", "fields": {"x": "1"}}}
print("repeated_submission_blocked=", repeated_submission(matter, "browser.act", {**later, "dry_run": False}))
