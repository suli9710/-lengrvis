from __future__ import annotations

from collections.abc import Iterable

from app.config import AppSettings
from app.core.audit import record
from app.policy.risk import RiskLevel
from app.security.capability_manifest import (
    CapabilityManifestError,
    assert_tool_allowed,
    is_tool_allowed,
)
from app.skills.loader import register_skills
from app.skills.schemas import SkillLoadError
from app.tools.schemas import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        # P0-9 fix: Track tool names case-insensitively to detect and reject
        # case-confusing MCP tool names that could impersonate builtin tools.
        self._name_index: dict[str, str] = {}

    def register(self, definition: ToolDefinition) -> None:
        _mark_tool_authoritative(definition)
        try:
            assert_tool_allowed(definition)
        except CapabilityManifestError:
            return
        _guard_tool_executor(definition)
        name = definition.name
        lower = name.casefold()
        # P0-9 fix: Reject registration if a tool with the same case-insensitive
        # name already exists (prevents MCP tools from shadowing builtin tools
        # via case variations like "Browser.Navigate" vs "browser.navigate").
        if lower in self._name_index and self._name_index[lower] != name:
            raise ValueError(
                f"Tool name '{name}' conflicts with already registered '{self._name_index[lower]}' "
                f"(case-insensitive collision)"
            )
        self._tools[name] = definition
        self._name_index[lower] = name

    def get(self, name: str) -> ToolDefinition:
        # P0-9 fix: Use case-sensitive lookup for the actual tool, but also
        # check the case-insensitive index to detect and block case-based
        # impersonation attempts.
        if name in self._tools:
            tool = self._tools[name]
            assert_tool_allowed(tool)
            return tool
        # If the name doesn't match exactly but matches case-insensitively,
        # reject to prevent case-based bypass of permission checks.
        lower = name.casefold()
        if lower in self._name_index:
            registered = self._name_index[lower]
            if registered != name:
                raise KeyError(
                    f"Tool '{name}' not found. Did you mean '{registered}'? Tool name matching is case-sensitive."
                )
        raise KeyError(f"Tool not registered: {name}")

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def list_for_planning(self) -> list[ToolDefinition]:
        return [tool for tool in self.list() if is_tool_allowed(tool) and self._is_planning_visible(tool)]

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        include_deferred: bool = True,
        deferred_only: bool = False,
    ) -> list[ToolDefinition]:
        query_text = query.strip()
        terms = [term.casefold() for term in query_text.replace(".", " ").replace("_", " ").split() if term.strip()]
        if not terms and not query_text.casefold().startswith("select:"):
            return []
        direct = query_text
        if direct.casefold().startswith("select:"):
            name = direct.split(":", 1)[1].strip()
            try:
                tool = self.get(name)
            except KeyError:
                return []
            if not self._tool_in_search_scope(tool, include_deferred=include_deferred, deferred_only=deferred_only):
                return []
            return [tool]

        scored: list[tuple[int, str, ToolDefinition]] = []
        for tool in self.list():
            if not self._tool_in_search_scope(tool, include_deferred=include_deferred, deferred_only=deferred_only):
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
        if include_deferred and not deferred_only:
            deferred_matches = [item for item in scored if item[2].defer_loading]
            if deferred_matches:
                scored = deferred_matches
        return [tool for _score, _name, tool in scored[: max(1, max_results)]]

    def _is_planning_visible(self, tool: ToolDefinition) -> bool:
        if tool.name == "tool.search":
            return True
        if tool.defer_loading:
            return False
        return tool.is_model_visible() or _has_builtin_namespace(tool.name)

    def _tool_in_search_scope(self, tool: ToolDefinition, *, include_deferred: bool, deferred_only: bool) -> bool:
        if not is_tool_allowed(tool):
            return False
        if not (tool.is_model_visible() or tool.defer_loading or _has_builtin_namespace(tool.name)):
            return False
        if deferred_only:
            return tool.defer_loading
        if include_deferred:
            return True
        return not tool.defer_loading


def _has_builtin_namespace(name: str) -> bool:
    return str(name or "").startswith(
        (
            "app.",
            "browser.",
            "document.",
            "file.",
            "image.",
            "remote.",
            "search.",
            "system.",
            "tool.",
            "ui_automation.",
            "vision.",
            "workflow.",
        )
    )


registry = ToolRegistry()


def sync_extension_tools_from_global(target: ToolRegistry) -> int:
    """Copy MCP and other extension tools from the module-global registry.

    Per-orchestrator registries are built via ``register_all_tools(..., target=...)``
    without MCP definitions; after ``main`` lifespan registers MCP tools on the
    global registry, each orchestrator must pull those entries in so plans and
    execution see the same third-party surface.
    """
    copied = 0
    for tool in registry.list():
        if tool.name.startswith("mcp."):
            target.register(tool)
            copied += 1
    return copied


def register_all_tools(
    extra_definitions=(),
    *,
    settings: AppSettings | None = None,
    skill_directories: Iterable[str] | None = None,
    load_skills: bool = True,
    target: ToolRegistry | None = None,
) -> ToolRegistry:
    """Build the full toolset.

    With ``target=None`` this rebuilds the module-global ``registry`` in place
    (legacy behavior for API routes that import the global). Callers that need
    an isolated toolset (one per orchestrator) MUST pass their own ``target``:
    rebuilding the shared global would wipe custom registrations of every
    other live orchestrator and briefly empty the toolset mid-run.
    """
    from app.adapters import tools as adapter_tools
    from app.tools import (
        app_excel,
        app_tools,
        browser_tools,
        cluster_tools,
        developer_tools,
        document_tools,
        file_tools,
        notification_tools,
        remote_tools,
        search_tools,
        system_tools,
        tool_search,
        ui_automation_tools,
        vision_tools,
        workflow_tools,
    )

    reg = target if target is not None else registry
    reg._tools.clear()
    reg._name_index.clear()
    file_tools.register(reg)
    developer_tools.register(reg)
    document_tools.register(reg)
    notification_tools.register(reg)
    system_tools.register(reg)
    remote_tools.register(reg)
    ui_automation_tools.register(reg)
    workflow_tools.register(reg)
    app_tools.register(reg)
    app_excel.register(reg)
    browser_tools.register(reg)
    search_tools.register(reg)
    tool_search.register(reg)
    vision_tools.register(reg)
    cluster_tools.register(reg)
    adapter_tools.register(reg)
    for definition in extra_definitions or ():
        reg.register(definition)
    if load_skills:
        try:
            if settings is None:
                from app.llm.registry import get_effective_settings

                settings = get_effective_settings()
            register_skills(reg, settings=settings, skill_directories=skill_directories)
        except SkillLoadError:
            raise
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            record("skills.load_failed", "ToolRegistry", {"error": str(exc)})
            raise SkillLoadError(f"Could not load configured skills: {exc}") from exc
    _mark_builtin_tools_authoritative(reg)
    return reg


def _mark_builtin_tools_authoritative(reg: ToolRegistry | None = None) -> None:
    for tool in (reg if reg is not None else registry).list():
        _mark_tool_authoritative(tool)


def _mark_tool_authoritative(tool: ToolDefinition) -> None:
    if tool.trust_tier == "unknown" and getattr(tool, "origin", "builtin") == "builtin":
        tool.trust_tier = "builtin"
    if tool.read_only is None:
        tool.read_only = tool.risk_level == RiskLevel.R0_READ_ONLY and not tool.supports_dry_run
    if tool.concurrency_safe is None:
        tool.concurrency_safe = tool.is_read_only() and not tool.concurrency_key and not tool.destructive
    if not tool.effects:
        tool.effects = _infer_effects(tool)
    if not tool.resource_kinds:
        tool.resource_kinds = _infer_resource_kinds(tool)


def _guard_tool_executor(tool: ToolDefinition) -> None:
    original = tool.execute
    if bool(getattr(original, "__lengrvis_capability_guarded__", False)):
        return

    def guarded_execute(args, context):  # noqa: ANN001, ANN202 - preserves the ToolExecutor protocol.
        assert_tool_allowed(tool)
        return original(args, context)

    guarded_execute.__lengrvis_capability_guarded__ = True  # type: ignore[attr-defined]
    tool.execute = guarded_execute


def _infer_effects(tool: ToolDefinition) -> list[str]:
    name = tool.name
    if tool.risk_level == RiskLevel.R0_READ_ONLY:
        if name.startswith(("search.", "tool.search")):
            return ["search", "read"]
        if name.startswith(("vision.", "image.", "file.cluster", "app.cluster")):
            return ["inspect", "read"]
        return ["read"]
    if tool.risk_level == RiskLevel.R1_OPEN_ONLY:
        if name.startswith(("remote.", "ui_automation.")):
            return ["observe", "open"]
        if name.startswith("browser."):
            return ["navigate", "open"]
        return ["open"]
    if name.endswith(("click", "click_at")) or ".click" in name:
        return ["click", "write"]
    if "type" in name or "write" in name:
        return ["type", "write"]
    if "drag" in name:
        return ["drag", "write"]
    if "key_press" in name or "hotkey" in name:
        return ["key", "write"]
    if "uninstall" in name:
        return ["system", "delete"]
    if "trash" in name or "delete" in name or "cleanup_execute" in name:
        return ["delete", "write"]
    if "workflow" in name:
        return ["workflow", "write"]
    return ["write"]


def _infer_resource_kinds(tool: ToolDefinition) -> list[str]:
    name = tool.name
    if name.startswith("file."):
        return ["file"]
    if name.startswith("document."):
        return ["document"]
    if name.startswith("browser."):
        return ["browser"]
    if name.startswith("remote."):
        return ["remote_screen"]
    if name.startswith("ui_automation."):
        return ["desktop_ui"]
    if name.startswith("app.excel."):
        return ["spreadsheet"]
    if name.startswith("app."):
        return ["application"]
    if name.startswith("search."):
        return ["web"]
    if name.startswith(("vision.", "image.")):
        return ["image"]
    if name.startswith("system."):
        return ["system"]
    if name.startswith("workflow."):
        return ["workflow"]
    if name.startswith("tool."):
        return ["tool"]
    return ["runtime"]
