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


registry = ToolRegistry()


def register_all_tools(
    extra_definitions=(),
    *,
    settings: AppSettings | None = None,
    skill_directories: Iterable[str] | None = None,
    load_skills: bool = True,
) -> ToolRegistry:
    from app.tools import app_excel, app_tools, browser_tools, cluster_tools, document_tools, file_tools, remote_tools, search_tools, system_tools, vision_tools

    registry._tools.clear()
    file_tools.register(registry)
    document_tools.register(registry)
    system_tools.register(registry)
    remote_tools.register(registry)
    app_tools.register(registry)
    app_excel.register(registry)
    browser_tools.register(registry)
    search_tools.register(registry)
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
