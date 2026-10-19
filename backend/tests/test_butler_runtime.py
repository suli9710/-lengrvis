from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import butler_store as store
from app.core.butler_models import AutonomyScope, ButlerMatter, MatterStatus
from app.core.schemas import PlanStep, Task
from app.orchestration.butler_runtime import ButlerExecutionDeferred, validate_task_action
from app.orchestration.tool_runtime_execution import ToolRuntimeExecutionMixin
from app.policy.risk import RiskLevel
from app.tools import file_tools
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolDefinition


def test_scope_is_rechecked_after_approval_and_cannot_be_forged_in_task_metadata(tmp_path):
    scope = store.save(
        AutonomyScope(label="文件", directories=[str(tmp_path)], allowed_actions=["observe", "file_modify"])
    )
    task = Task(
        user_goal="写入", metadata={"butler_matter_id": "matter-bound", "butler_execution_ticket": "runtime-ticket"}
    )
    matter = store.save(
        ButlerMatter(
            id="matter-bound",
            goal=task.user_goal,
            source="event",
            status=MatterStatus.RUNNING,
            execution_ticket="runtime-ticket",
            active_task_id=task.id,
            scope_versions={scope.id: 1},
        )
    )
    registry = ToolRegistry()
    file_tools.register(registry)
    tool = registry.get("file.write_text")
    args = {"path": str(tmp_path / "new.txt"), "text": "hello"}
    assert validate_task_action(task, tool, args).id == matter.id
    store.mutate(AutonomyScope, scope.id, lambda item: setattr(item, "enabled", False))
    with pytest.raises((ValueError, ButlerExecutionDeferred)):
        validate_task_action(task, tool, args, live=True)
    task.metadata["butler_execution_ticket"] = "forged"
    with pytest.raises(ButlerExecutionDeferred):
        validate_task_action(task, tool, args)


def test_desktop_input_is_serialized_across_independent_event_loops():
    active = 0
    highest = 0
    guard = threading.Lock()

    def execute(args, context):
        nonlocal active, highest
        with guard:
            active += 1
            highest = max(highest, active)
        time.sleep(0.04)
        with guard:
            active -= 1
        return {"ok": True}

    tool = ToolDefinition(
        name="test.desktop",
        description="test",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        agent_owner="ComputerAgent",
        supports_dry_run=True,
        requires_authorized_path=False,
        execute=execute,
        concurrency_key="desktop_gui_input",
    )

    def run():
        host = ToolRuntimeExecutionMixin()
        step = PlanStep(agent_name="ComputerAgent", tool_name=tool.name, description="input")
        return asyncio.run(host.execute_tool_with_locks(tool, step, {"dry_run": False}, {}))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda _: run(), range(2)))
    assert all(item["ok"] for item in outputs)
    assert highest == 1
