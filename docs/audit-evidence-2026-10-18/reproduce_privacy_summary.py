"""Reproduce privacy-mode task summary leaking into the global session context.

The summary is deterministic (no model call), persisted in session_contexts and
then injected into every planning/provider payload. A later efficiency task
therefore sends the privacy goal text to the cloud planner.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("LENGRVIS_AUDIT_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-summary-")

from app.context.management import project_messages_for_llm  # noqa: E402
from app.core import db  # noqa: E402
from app.core.schemas import Task  # noqa: E402
from app.core.session_context import get_session_context_store  # noqa: E402
from app.llm.registry import get_effective_settings  # noqa: E402
from app.orchestration.handlers.completion_handler import CompletionHandler  # noqa: E402

MARKER = "PRIVATE_GOAL_MARKER_8842"
db.init_db()
started = datetime.now(UTC)
messages = [
    SimpleNamespace(id="msg_1", created_at=started.isoformat()),
    SimpleNamespace(id="msg_2", created_at=(started + timedelta(seconds=1)).isoformat()),
]
llm_messages = [
    {"role": "user", "content": f"{MARKER}: my bank balance is 123456"},
    {"role": "assistant", "content": "Understood."},
]


class FakeBus:
    def get_messages(self, _task_id):
        return messages

    def get_llm_messages(self, _task_id, _settings, limit=80):
        return llm_messages[:limit]


store = get_session_context_store()
orchestrator = SimpleNamespace(bus=FakeBus(), session_context_store=store, name="review-probe")
task = Task(user_goal=f"{MARKER}: my bank balance is 123456", mode="privacy")
handler = CompletionHandler(orchestrator)  # type: ignore[arg-type]
handler._update_session_summary(task)

summary = store.planning_context()["conversation_summary"]
print(f"summary_contains_private_marker={MARKER in summary}")
settings = get_effective_settings().model_copy(update={"mode": "efficiency", "context_session_memory_enabled": True})
projection = project_messages_for_llm(
    [{"role": "user", "content": "public follow-up"}],
    settings,
    session_context=store.planning_context(),
    source="review-probe",
)
text = "\n".join(str(item.get("content") or "") for item in projection.messages)
print(f"efficiency_projection_contains_private_marker={MARKER in text}")
if MARKER not in summary or MARKER not in text:
    raise SystemExit("marker did not propagate; probe assumptions invalid")
print("PRIVACY_SUMMARY_EGRESS_CONFIRMED")
