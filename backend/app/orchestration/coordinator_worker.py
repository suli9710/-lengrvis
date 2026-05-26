from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable
from uuid import uuid4


class WorkerTaskKind(StrEnum):
    RESEARCH = "research"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"


class WorkerTaskStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class WorkerRole(StrEnum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


@dataclass(slots=True)
class WorkerTaskSpec:
    goal: str
    kind: WorkerTaskKind
    prompt: str
    owned_paths: list[str] = field(default_factory=list)
    completion_criteria: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: f"worker_{uuid4().hex}")
    status: WorkerTaskStatus = WorkerTaskStatus.CREATED
    result: str = ""
    role: WorkerRole = WorkerRole.WORKER

    def self_contained_prompt(self) -> str:
        sections = [
            f"Role: {self.role.value}",
            f"Goal: {self.goal}",
            f"Task kind: {self.kind.value}",
            "Prompt:",
            self.prompt,
        ]
        if self.role == WorkerRole.WORKER:
            sections.append(
                "Responsibility boundary:\n"
                "- Execute only this assigned task.\n"
                "- Do not coordinate, split, reprioritize, or delegate other tasks.\n"
                "- Report completion, blockers, and concrete outputs."
            )
        if self.owned_paths:
            sections.append("Owned paths:\n" + "\n".join(f"- {path}" for path in self.owned_paths))
        if self.completion_criteria:
            sections.append("Completion criteria:\n" + "\n".join(f"- {item}" for item in self.completion_criteria))
        if self.forbidden_actions:
            sections.append("Forbidden actions:\n" + "\n".join(f"- {item}" for item in self.forbidden_actions))
        return "\n\n".join(sections)

    @property
    def is_write_task(self) -> bool:
        return self.kind == WorkerTaskKind.IMPLEMENTATION


class CoordinatorWorkerPolicy:
    """Small deterministic policy for safe worker fan-out decisions."""

    WORKER_FORBIDDEN_TERMS = (
        "coordinate",
        "coordinator",
        "delegate",
        "fan out",
        "prioritize workers",
        "replan",
        "split tasks",
    )

    def can_run_together(self, left: WorkerTaskSpec, right: WorkerTaskSpec) -> bool:
        if not left.is_write_task and not right.is_write_task:
            return True
        if left.is_write_task and right.is_write_task:
            return not self._overlaps(left.owned_paths, right.owned_paths)
        return not self._overlaps(left.owned_paths, right.owned_paths)

    def partition_batches(self, tasks: Iterable[WorkerTaskSpec]) -> list[list[WorkerTaskSpec]]:
        batches: list[list[WorkerTaskSpec]] = []
        for task in tasks:
            for batch in batches:
                if all(self.can_run_together(task, existing) for existing in batch):
                    batch.append(task)
                    break
            else:
                batches.append([task])
        return batches

    def review_spec(self, task: WorkerTaskSpec) -> list[str]:
        findings: list[str] = []
        if not task.prompt.strip():
            findings.append("worker prompt must be self-contained and non-empty")
        if task.is_write_task and not task.owned_paths:
            findings.append("implementation workers must declare owned_paths")
        if not task.completion_criteria:
            findings.append("worker task must declare completion criteria")
        if task.role == WorkerRole.WORKER:
            lowered_prompt = task.prompt.casefold()
            for term in self.WORKER_FORBIDDEN_TERMS:
                if term in lowered_prompt:
                    findings.append("worker prompt must not assign coordinator responsibilities")
                    break
        return findings

    def _overlaps(self, left: list[str], right: list[str]) -> bool:
        if not left or not right:
            return False
        normalized_left = {_normalize_path(path) for path in left}
        normalized_right = {_normalize_path(path) for path in right}
        for left_path in normalized_left:
            for right_path in normalized_right:
                if left_path == right_path or left_path.startswith(right_path + "/") or right_path.startswith(left_path + "/"):
                    return True
        return False


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip().rstrip("/").casefold()
