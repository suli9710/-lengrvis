from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import AppSettings
from app.core.schemas import Task, now_iso
from app.orchestration.agent_bus import AgentBus


@dataclass(slots=True)
class FileState:
    path: str
    timestamp: str
    partial_view: bool = False
    size: int = 0


@dataclass(slots=True)
class LargeResultReference:
    path: str
    original_size: int
    preview: str
    has_more: bool


@dataclass(slots=True)
class TaskRuntimeContext:
    task: Task
    settings: AppSettings
    bus: AgentBus
    allowed_directories: list[str]
    file_state_cache: dict[str, FileState] = field(default_factory=dict)
    tool_decisions: dict[str, str] = field(default_factory=dict)
    large_results: dict[str, LargeResultReference] = field(default_factory=dict)
    extra_context: dict[str, Any] = field(default_factory=dict)
    abort_requested: bool = False

    @classmethod
    def from_task(cls, task: Task, settings: AppSettings, bus: AgentBus) -> "TaskRuntimeContext":
        return cls(
            task=task,
            settings=settings,
            bus=bus,
            allowed_directories=list(settings.allowed_directories or []),
        )

    def tool_context(self) -> dict[str, Any]:
        return {
            "allowed_directories": self.allowed_directories,
            "settings": self.settings,
            "runtime": self,
            **self.extra_context,
        }

    def remember_file(self, path: str | Path, *, partial_view: bool = False, size: int = 0) -> None:
        key = _normalize_path(path)
        self.file_state_cache[key] = FileState(
            path=str(path),
            timestamp=now_iso(),
            partial_view=partial_view,
            size=size,
        )

    def file_state(self, path: str | Path) -> FileState | None:
        return self.file_state_cache.get(_normalize_path(path))

    def remember_large_result(self, result_id: str, reference: LargeResultReference) -> None:
        self.large_results[result_id] = reference


def _normalize_path(path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve(strict=False)).casefold()
    except OSError:
        return str(path).casefold()
