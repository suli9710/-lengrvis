from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.core import db
from app.core.schemas import new_id, now_iso


class GoalStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class Goal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: new_id("goal"))
    user_goal: str = Field(validation_alias=AliasChoices("user_goal", "description"))
    sub_goals: list[str] = Field(default_factory=list)
    scope: str = "default"
    parent_goal_id: str | None = None
    depth: int = 0
    status: GoalStatus = GoalStatus.ACTIVE
    related_task_ids: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("related_task_ids", "task_ids"),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @property
    def description(self) -> str:
        return self.user_goal

    @description.setter
    def description(self, value: str) -> None:
        self.user_goal = value

    @property
    def task_ids(self) -> list[str]:
        return self.related_task_ids

    @task_ids.setter
    def task_ids(self, value: list[str]) -> None:
        self.related_task_ids = value


class GoalStack:
    def __init__(self, *, scope: str = "default") -> None:
        self.scope = scope or "default"
        db.init_db()

    def push(
        self,
        description: str = "",
        *,
        user_goal: str | None = None,
        sub_goals: list[str] | None = None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_goal_id: str | None = None,
    ) -> Goal:
        goal_text = (user_goal or description).strip()
        if not goal_text:
            raise ValueError("Goal text is required.")
        if parent_goal_id == "":
            parent = None
        else:
            parent = self.peek() if parent_goal_id is None else self._fetch_goal(parent_goal_id)
        if parent_goal_id not in {None, ""} and parent is None:
            raise ValueError(f"Parent goal not found: {parent_goal_id}")
        goal = Goal(
            user_goal=goal_text,
            sub_goals=list(sub_goals or []),
            scope=self.scope,
            parent_goal_id=parent.id if parent else None,
            depth=(parent.depth + 1) if parent else 0,
            related_task_ids=[task_id] if task_id else [],
            metadata=dict(metadata or {}),
        )
        self._persist(goal)
        return goal

    def pop(self) -> Goal | None:
        goal = self.peek()
        if goal is None:
            return None
        goal.status = GoalStatus.COMPLETED
        goal.updated_at = now_iso()
        self._persist(goal)
        return goal

    def peek(self) -> Goal | None:
        rows = db.fetch_many(
            "goals",
            "scope = ? AND status = ?",
            (self.scope, GoalStatus.ACTIVE.value),
            limit=200,
        )
        if not rows:
            return None
        goals = [Goal.model_validate(row) for row in rows]
        return max(goals, key=lambda item: (item.depth, item.created_at))

    def relate_task(self, task_id: str, goal_id: str | None = None) -> Goal:
        goal = self._fetch_goal(goal_id) if goal_id else self.peek()
        if goal is None:
            raise ValueError("No goal available to relate task.")
        if task_id not in goal.related_task_ids:
            goal.related_task_ids.append(task_id)
            goal.updated_at = now_iso()
            self._persist(goal)
        return goal

    def get_context_for_planning(self, goal_text: str = "", *, limit: int = 5) -> dict[str, Any]:
        goals = self._active_goals(limit=max(limit, 20))
        if not goals:
            return {"scope": self.scope, "active_goal": None, "goal_stack": []}

        if goal_text:
            related = [goal for goal in goals if self._is_related(goal.user_goal, goal_text)]
            if related:
                goals = related
        ordered = sorted(goals, key=lambda item: (item.depth, item.created_at))[-limit:]
        active = ordered[-1]
        return {
            "scope": self.scope,
            "active_goal": self._goal_context(active),
            "goal_stack": [self._goal_context(goal) for goal in ordered],
        }

    def find_related(self, goal_text: str, *, limit: int = 50) -> Goal | None:
        related = [
            goal
            for goal in self._active_goals(limit=limit)
            if self._is_related(goal.user_goal, goal_text)
        ]
        if not related:
            return None
        return max(related, key=lambda item: (item.depth, item.created_at))

    def _active_goals(self, *, limit: int) -> list[Goal]:
        rows = db.fetch_many(
            "goals",
            "scope = ? AND status = ?",
            (self.scope, GoalStatus.ACTIVE.value),
            limit=limit,
        )
        return [Goal.model_validate(row) for row in rows]

    def _fetch_goal(self, goal_id: str | None) -> Goal | None:
        if not goal_id:
            return None
        data = db.fetch_one("goals", goal_id)
        if data is None:
            return None
        goal = Goal.model_validate(data)
        if goal.scope != self.scope:
            raise ValueError(f"Goal {goal_id} belongs to scope {goal.scope!r}, not {self.scope!r}.")
        return goal

    def _persist(self, goal: Goal) -> None:
        db.upsert_model("goals", goal)

    def _goal_context(self, goal: Goal) -> dict[str, Any]:
        data = goal.model_dump(mode="json")
        data["description"] = goal.user_goal
        data["task_ids"] = list(goal.related_task_ids)
        return data

    def _is_related(self, existing: str, incoming: str) -> bool:
        left = existing.casefold().strip()
        right = incoming.casefold().strip()
        if not left or not right:
            return False
        if left in right or right in left:
            return True
        left_tokens = _goal_tokens(left)
        right_tokens = _goal_tokens(right)
        if not left_tokens or not right_tokens:
            return False
        overlap = left_tokens & right_tokens
        return len(overlap) / max(1, min(len(left_tokens), len(right_tokens))) >= 0.45


def _goal_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.casefold()))
