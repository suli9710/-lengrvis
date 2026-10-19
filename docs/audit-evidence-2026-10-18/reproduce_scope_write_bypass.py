"""Reproduce observe-only scope authorizing a proactive file write."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("LENGRVIS_AUDIT_HMAC_SECRET", "review-repro-secret")
os.environ["LENGRVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="lengrvis-scope-")

from app.agents.orchestrator_agent import OrchestratorAgent  # noqa: E402
from app.core import butler_store as store  # noqa: E402
from app.core import db  # noqa: E402
from app.core.butler_models import AutonomyScope, ButlerMatter, MatterStatus  # noqa: E402
from app.core.schemas import PlanStep, Task  # noqa: E402
from app.orchestration.butler_runtime import validate_task_action  # noqa: E402
from app.orchestration.runtime_context import TaskRuntimeContext  # noqa: E402
from app.orchestration.tool_runtime_approval import requires_runtime_approval  # noqa: E402
from app.llm.registry import get_effective_settings  # noqa: E402

workspace = Path(tempfile.mkdtemp(prefix="lengrvis-scope-files-"))
target = workspace / "proactive.txt"
db.init_db()
scope = store.save(
    AutonomyScope(
        label="observe only",
        directories=[str(workspace)],
        allowed_actions=["observe"],
    )
)
matter = store.save(
    ButlerMatter(
        goal="watch folder",
        mode="efficiency",
        source="event",
        status=MatterStatus.RUNNING,
        scope_versions={scope.id: scope.version},
        execution_ticket="ticket",
    )
)
task = Task(
    user_goal="watch folder",
    metadata={"butler_matter_id": matter.id, "butler_execution_ticket": "ticket"},
)
db.upsert_model("tasks", task)
store.mutate(
    ButlerMatter,
    matter.id,
    lambda item: (setattr(item, "active_task_id", task.id), setattr(item, "execution_ticket", "ticket")),
)
orchestrator = OrchestratorAgent()
tool = orchestrator.registry.get("file.write_text")
args = {"path": str(target), "text": "written without file_modify", "dry_run": False}
step = PlanStep(task_id=task.id, agent_name="FileAgent", tool_name=tool.name, args=args, description="write")
runtime = TaskRuntimeContext.from_task(task, get_effective_settings(), orchestrator.bus)
try:
    validate_task_action(task, tool, args, live=True)
    print("validate_task_action=PASS")
except Exception as exc:  # noqa: BLE001 - probe
    print(f"validate_task_action={type(exc).__name__}:{exc}")
settings = runtime.settings.model_copy(update={"permission_mode": "trusted_edits"})
runtime.settings = settings
print("trusted_reversible_edit_allowed=", __import__("app.policy.permission_modes", fromlist=["x"]).trusted_reversible_edit_allowed(tool, args))
print("requires_runtime_approval=", requires_runtime_approval(step, tool, runtime))
