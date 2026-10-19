"""Reproduce residual duplicate-submission guard bypass via URL spelling.

A committed submission to https://example.com/form is not recognized when the
next run spells the same endpoint as https://EXAMPLE.com/form (or adds the
default :443, or a dot segment). Chromium treats these as the same URL, so the
matter-level guard is bypassed.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("LENGRVIS_APPROVAL_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-repro-")

from app.core import butler_store as store  # noqa: E402
from app.core import db  # noqa: E402
from app.core.butler_models import ButlerMatter, MatterStatus  # noqa: E402
from app.core.schemas import Task, ToolCall  # noqa: E402
from app.policy.risk import RiskLevel  # noqa: E402
from app.services.butler_effects import (  # noqa: E402
    repeated_submission,
    submission_binding_hmac,
    submission_identity,
)

base = {"url": "https://example.com/form", "selector": "#f", "fields": {"reference": "R-1"}}
variant = {"url": "https://EXAMPLE.com/form", "selector": "#f", "fields": {"reference": "R-1"}}

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
digest = submission_identity("browser.submit_form", base)
call = ToolCall(
    task_id=task.id,
    step_id="step",
    tool_name="browser.submit_form",
    args={**base, "dry_run": False},
    risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    status="committed",
    dry_run=False,
    submission_digest=digest,
)
call.submission_binding_hmac = submission_binding_hmac(call)
db.upsert_model("tool_calls", call)

blocked_exact = repeated_submission(matter, "browser.submit_form", {**base, "dry_run": False})
blocked_variant = repeated_submission(matter, "browser.submit_form", {**variant, "dry_run": False})
print(f"same_url_blocked={blocked_exact}")
print(f"equivalent_url_blocked={blocked_variant}")
print(f"digests_equal={submission_identity('browser.submit_form', base) == submission_identity('browser.submit_form', variant)}")
if not blocked_exact:
    raise SystemExit("exact URL was not blocked; fixture invalid")
if blocked_variant:
    raise SystemExit("equivalent URL was blocked; no residual bypass")
print("RESIDUAL_BYPASS_CONFIRMED")
