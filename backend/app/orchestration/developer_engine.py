from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.config import AppSettings, PROJECT_ROOT
from app.orchestration.execution_engine import ExecutionEngine, InMemoryRunStore, default_run_store
from app.orchestration.execution_models import (
    EngineSelection,
    EngineTurnResult,
    LargeResultRef,
    RunObservation,
    RunPhase,
    RunState,
)
from app.tools import developer_tools


class DeveloperExecutionEngine(ExecutionEngine):
    """Read-only developer engine skeleton.

    This v1 engine observes repository state and proposes safe inspection steps.
    It intentionally does not introduce automatic file writes or code editing.
    """

    name = "developer"

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        store: InMemoryRunStore | None = None,
    ) -> None:
        self.settings = settings or AppSettings()
        self.store = store or default_run_store

    async def start_run(self, goal: str, mode: str, engine: EngineSelection = "auto") -> RunState:
        state = RunState(
            run_id=self.store.new_id("devrun"),
            engine="developer",
            phase=RunPhase.PLANNING,
            goal=goal,
            mode=mode,
            transition_reason="developer run created",
            current_plan={
                "summary": "Inspect repository state with read-only developer tools.",
                "allowed_tools": list(readonly_developer_tool_names()),
                "steps": [
                    {"id": "repo_status", "tool": "dev.git_status", "status": "pending"},
                    {"id": "diff_preview", "tool": "dev.diff_preview", "status": "pending"},
                    {"id": "test_inventory", "tool": "dev.pytest_inventory", "status": "pending"},
                    {"id": "goal_search", "tool": "dev.grep", "status": "pending"},
                ],
                "writes_enabled": False,
            },
        )
        return self.store.put(state)

    async def resume_run(self, run_id: str) -> RunState:
        state = self.store.get(run_id)
        if state.phase == RunPhase.PAUSED:
            state = state.model_copy(
                update={"phase": RunPhase.RUNNING, "transition_reason": "developer run resumed"},
                deep=True,
            )
            return self.store.put(state)
        return state

    async def cancel_run(self, run_id: str) -> RunState:
        state = self.store.get(run_id)
        updated = state.model_copy(
            update={"phase": RunPhase.CANCELLED, "transition_reason": "developer run cancelled"},
            deep=True,
        )
        return self.store.put(updated)

    async def run_turn(self, state: RunState) -> EngineTurnResult:
        if state.phase in {RunPhase.COMPLETED, RunPhase.FAILED, RunPhase.CANCELLED}:
            return EngineTurnResult(state=state, finished=True, message=f"Run is already {state.phase.value}.")

        context = self._tool_context()
        observations = list(state.observations)
        large_refs = list(state.large_result_refs)
        outputs: dict[str, Any] = {}

        try:
            tool_outputs = await self._inspect_repository(state.goal, context)
        except Exception as exc:  # noqa: BLE001 - tool failures are captured as run observations.
            failed = state.model_copy(
                update={
                    "phase": RunPhase.FAILED,
                    "turn_count": state.turn_count + 1,
                    "transition_reason": f"developer inspection failed: {exc}",
                },
                deep=True,
            )
            return EngineTurnResult(state=self.store.put(failed), finished=True, message=failed.transition_reason)

        for source, payload in tool_outputs.items():
            observations.append(
                RunObservation(
                    turn=state.turn_count + 1,
                    source=source,
                    message=_summarize_tool_payload(source, payload),
                    payload=_bounded_payload(payload),
                )
            )
            ref = _large_result_ref(source, payload)
            if ref is not None:
                large_refs.append(ref)
            outputs[source] = payload

        updated_plan = _mark_plan_steps_done(state.current_plan, tool_outputs)
        updated = state.model_copy(
            update={
                "phase": RunPhase.COMPLETED,
                "turn_count": state.turn_count + 1,
                "transition_reason": "developer read-only inspection complete",
                "current_plan": updated_plan,
                "observations": observations,
                "large_result_refs": large_refs,
            },
            deep=True,
        )
        stored = self.store.put(updated)
        return EngineTurnResult(
            state=stored,
            finished=True,
            message="Developer engine completed read-only repository inspection.",
            outputs=outputs,
        )

    async def _inspect_repository(self, goal: str, context: dict[str, Any]) -> dict[str, dict[str, Any]]:
        cwd = _default_workspace(self.settings)
        query = _query_from_goal(goal)
        tasks = {
            "dev.git_status": asyncio.to_thread(developer_tools.git_status, {"cwd": cwd}, context),
            "dev.diff_preview": asyncio.to_thread(developer_tools.diff_preview, {"cwd": cwd, "pathspec": "."}, context),
            "dev.pytest_inventory": asyncio.to_thread(
                developer_tools.pytest_inventory,
                {"path": cwd, "pattern": "test_*.py", "limit": 50},
                context,
            ),
            "dev.grep": asyncio.to_thread(
                developer_tools.grep_files,
                {"path": cwd, "query": query, "pattern": "*.py", "limit": 25},
                context,
            ),
        }
        results = await asyncio.gather(*tasks.values())
        return dict(zip(tasks.keys(), results, strict=True))

    def _tool_context(self) -> dict[str, Any]:
        allowed = list(self.settings.allowed_directories or [])
        workspace = _default_workspace(self.settings)
        if workspace not in allowed:
            allowed.append(workspace)
        return {"allowed_directories": allowed, "settings": self.settings}


def readonly_developer_tool_names() -> tuple[str, ...]:
    return ("dev.glob", "dev.grep", "dev.git_status", "dev.diff_preview", "dev.shell_readonly", "dev.pytest_inventory")


def _default_workspace(settings: AppSettings) -> str:
    if settings.allowed_directories:
        return str(Path(settings.allowed_directories[0]).expanduser().resolve(strict=False))
    return str(PROJECT_ROOT.resolve(strict=False))


def _query_from_goal(goal: str) -> str:
    for token in goal.replace("/", " ").replace("\\", " ").replace(".", " ").split():
        clean = "".join(char for char in token if char.isalnum() or char in {"_", "-"}).strip()
        if len(clean) >= 4:
            return clean
    return "TODO"


def _summarize_tool_payload(source: str, payload: dict[str, Any]) -> str:
    if not payload.get("ok", False):
        return f"{source} failed: {payload.get('error') or payload.get('stderr') or 'unknown error'}"
    summary = payload.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    if source == "dev.git_status":
        return "Captured git status."
    if source == "dev.diff_preview":
        diff = str(payload.get("diff") or "")
        return f"Captured diff preview ({len(diff)} chars)."
    if source == "dev.pytest_inventory":
        return f"Captured pytest inventory ({payload.get('test_count', 0)} tests)."
    if source == "dev.grep":
        return f"Captured grep results ({payload.get('count', 0)} matches)."
    return f"Captured {source} output."


def _bounded_payload(payload: dict[str, Any], limit: int = 4000) -> dict[str, Any]:
    bounded: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str) and len(value) > limit:
            bounded[key] = value[:limit]
            bounded[f"{key}_truncated"] = True
        else:
            bounded[key] = value
    return bounded


def _large_result_ref(source: str, payload: dict[str, Any]) -> LargeResultRef | None:
    for key in ("diff", "stdout"):
        value = payload.get(key)
        if isinstance(value, str) and len(value) > 4000:
            return LargeResultRef(
                ref_id=f"{source}:{key}",
                original_size=len(value),
                preview=value[:1000],
                has_more=True,
            )
    return None


def _mark_plan_steps_done(plan: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    updated = {**plan}
    steps = []
    for raw_step in list(plan.get("steps") or []):
        if not isinstance(raw_step, dict):
            continue
        tool = raw_step.get("tool")
        steps.append({**raw_step, "status": "succeeded" if tool in outputs else raw_step.get("status", "pending")})
    updated["steps"] = steps
    return updated
