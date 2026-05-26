from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


EngineName = Literal["os", "developer"]
EngineSelection = Literal["auto", "os", "developer"]


class RunPhase(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


class RunObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn: int = 0
    source: str = ""
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class LargeResultRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    path: str = ""
    original_size: int = 0
    preview: str = ""
    has_more: bool = False


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    engine: EngineName
    phase: RunPhase = RunPhase.CREATED
    turn_count: int = 0
    transition_reason: str = ""
    current_plan: dict[str, Any] = Field(default_factory=dict)
    observations: list[RunObservation] = Field(default_factory=list)
    large_result_refs: list[LargeResultRef] = Field(default_factory=list)
    recovery_count_by_step: dict[str, int] = Field(default_factory=dict)
    goal: str = ""
    mode: str = "privacy"
    task_id: str = ""
    paused: bool = False

    @field_validator("turn_count")
    @classmethod
    def validate_turn_count(cls, value: int) -> int:
        return max(0, value)


class EngineRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_engine: EngineSelection = "auto"
    selected_engine: EngineName
    reason: str


class EngineTurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: RunState
    finished: bool = False
    message: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    event_type: str
    sequence: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
