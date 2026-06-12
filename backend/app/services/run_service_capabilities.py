"""Engine routing metadata exposed on run API responses."""

from __future__ import annotations

from typing import Any

from app.agents.delegation_metadata import (
    developer_engine_capabilities,
    os_engine_capabilities,
)
from app.agents.worker_agents import normalize_supervisor_agent_hint
from app.core import db
from app.core.schemas import Run, RunEngine
from app.llm.registry import get_effective_settings
from app.orchestration.engine_router import route_engine


def engine_route_rule_for_run(run: Run) -> str:
    if isinstance(run.state, dict):
        stored = run.state.get("route_rule")
        if stored:
            return str(stored)
    if run.requested_engine in {RunEngine.OS, RunEngine.DEVELOPER}:
        return "explicit_override"
    settings = get_effective_settings()
    default_engine = settings.default_engine if settings.default_engine in {"auto", "os", "developer"} else "auto"
    return route_engine(
        run.message,
        "auto",
        fallback_engine=default_engine,  # type: ignore[arg-type]
        developer_writes_enabled=bool(getattr(settings, "developer_writes_enabled", False)),
    ).rule


def engine_capabilities_for_run(run: Run) -> dict[str, Any]:
    route_rule = engine_route_rule_for_run(run)
    if run.engine == RunEngine.DEVELOPER:
        plan = (run.state or {}).get("current_plan") if isinstance(run.state, dict) else {}
        writes_enabled = bool((plan or {}).get("writes_enabled", False))
        caps = developer_engine_capabilities(writes_enabled=writes_enabled)
        caps["route_rule"] = route_rule
        return caps
    caps = os_engine_capabilities(route_rule=route_rule)
    caps["route_rule"] = route_rule
    if run.task_id:
        task = db.fetch_one("tasks", run.task_id)
        metadata = (task or {}).get("metadata") or {}
        hint = normalize_supervisor_agent_hint(metadata.get("supervisor_agent_hint"))
        if hint:
            caps["supervisor_agent_hint"] = hint
    return caps
