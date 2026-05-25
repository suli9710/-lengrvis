from __future__ import annotations

from typing import Iterable

from app.config import AppSettings
from app.core.audit import record
from app.skills.loader import register_skills
from app.skills.schemas import SkillLoadError
from app.tools.schemas import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        return self._tools[name]

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def list_for_planning(self) -> list[ToolDefinition]:
        return [tool for tool in self.list() if not tool.defer_loading or tool.name == "tool.search"]

    def search(self, query: str, *, max_results: int = 5, include_deferred: bool = True) -> list[ToolDefinition]:
        terms = [term.casefold() for term in query.replace(".", " ").replace("_", " ").split() if term.strip()]
        if not terms and not query.strip().startswith("select:"):
            return []
        direct = query.strip()
        if direct.casefold().startswith("select:"):
            name = direct.split(":", 1)[1].strip()
            try:
                return [self.get(name)]
            except KeyError:
                return []

        scored: list[tuple[int, str, ToolDefinition]] = []
        for tool in self.list():
            if not include_deferred and tool.defer_loading:
                continue
            haystack = " ".join(
                [
                    tool.name,
                    tool.description,
                    tool.search_hint,
                    tool.agent_owner,
                ]
            ).casefold()
            score = sum(3 if term in tool.name.casefold() else 1 for term in terms if term in haystack)
            if score:
                scored.append((score, tool.name, tool))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [tool for _score, _name, tool in scored[: max(1, max_results)]]


registry = ToolRegistry()


def register_all_tools(
    extra_definitions=(),
    *,
    settings: AppSettings | None = None,
    skill_directories: Iterable[str] | None = None,
    load_skills: bool = True,
) -> ToolRegistry:
    from app.tools import (
        app_excel,
        app_tools,
        browser_tools,
        cluster_tools,
        document_tools,
        file_tools,
        remote_tools,
        search_tools,
        system_tools,
        tool_search,
        ui_automation_tools,
        vision_tools,
        workflow_tools,
    )

    registry._tools.clear()
    file_tools.register(registry)
    document_tools.register(registry)
    system_tools.register(registry)
    remote_tools.register(registry)
    ui_automation_tools.register(registry)
    workflow_tools.register(registry)
    app_tools.register(registry)
    app_excel.register(registry)
    browser_tools.register(registry)
    search_tools.register(registry)
    tool_search.register(registry)
    vision_tools.register(registry)
    cluster_tools.register(registry)
    for definition in extra_definitions or ():
        registry.register(definition)
    if load_skills:
        try:
            if settings is None:
                from app.llm.registry import get_effective_settings

                settings = get_effective_settings()
            register_skills(registry, settings=settings, skill_directories=skill_directories)
        except SkillLoadError:
            raise
        except Exception as exc:  # noqa: BLE001
            record("skills.load_failed", "ToolRegistry", {"error": str(exc)})
            raise SkillLoadError(f"Could not load configured skills: {exc}") from exc
    return registry
