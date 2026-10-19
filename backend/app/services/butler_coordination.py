"""Deterministic coordination over existing matters; no second execution engine."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import TERMINAL_MATTER_STATUSES, ButlerMatter, MatterArrangement, MatterStatus


def outcome_revoked(matter: ButlerMatter) -> bool:
    if matter.outcome_revoked:
        return True
    return any((db.fetch_one("tasks", identity) or {}).get("status") == "rolled_back" for identity in matter.task_ids)


def dependency_blocks(matter: ButlerMatter, graph: dict[str, ButlerMatter]) -> list[dict]:
    blocks = []
    for identity in matter.depends_on:
        parent = graph.get(identity)
        reason = ""
        needs_decision = False
        if parent is None:
            reason, needs_decision = "前置事项已不存在", True
        elif outcome_revoked(parent):
            reason, needs_decision = "前置成果已撤销或发生变化，需重新核验", True
        elif parent.status in {MatterStatus.FAILED, MatterStatus.CANCELLED}:
            reason, needs_decision = "前置事项失败或已取消", True
        elif parent.status == MatterStatus.PAUSED:
            reason = "前置事项已暂停"
        elif parent.status != MatterStatus.COMPLETED or not parent.completion_evidence:
            reason = "等待前置事项完成核验"
        if reason:
            blocks.append(
                {
                    "matter_id": identity,
                    "goal": parent.goal if parent else "未知事项",
                    "reason": reason,
                    "needs_decision": needs_decision,
                }
            )
    return blocks


def deadline_band(matter: ButlerMatter, now: datetime) -> int:
    if not matter.due_at:
        return 2
    delta = (datetime.fromisoformat(matter.due_at.replace("Z", "+00:00")) - now).total_seconds()
    return 0 if delta <= 0 else 1 if delta <= 3600 else 2


def coordination_view(records: list[ButlerMatter], *, enabled: bool, now: datetime | None = None) -> dict[str, dict]:
    now = now or datetime.now(UTC)
    graph = {item.id: item for item in records}
    active = [item for item in records if item.status not in TERMINAL_MATTER_STATUSES]
    keys = {
        item.id: (
            deadline_band(item, now),
            -item.priority if item.priority_explicit else -50,
            0 if item.source == "user" else 1,
            item.created_at,
            item.id,
        )
        for item in records
    }
    inherited: dict[str, str] = {}
    if enabled:
        # Walking each root's ancestors handles arbitrary depth without relying on record order.
        for item in sorted(active, key=lambda value: keys[value.id]):
            pending = list(item.depends_on)
            seen = {item.id}
            while pending:
                identity = pending.pop()
                if identity in seen or identity not in graph:
                    continue
                seen.add(identity)
                parent = graph[identity]
                if parent.status in TERMINAL_MATTER_STATUSES:
                    continue
                if keys[item.id] < keys[identity]:
                    keys[identity] = keys[item.id]
                    inherited[identity] = item.id
                pending.extend(parent.depends_on)
    result = {}
    ordered = sorted(records, key=lambda item: (*keys[item.id], item.id))
    for rank, item in enumerate(ordered):
        blocks = dependency_blocks(item, graph)
        affected = store.execution_started(item) and any(
            parent not in graph
            or graph[parent].outcome_revision != revision
            or outcome_revoked(graph[parent])
            or graph[parent].status != MatterStatus.COMPLETED
            for parent, revision in item.dependency_revisions.items()
        )
        blocked = bool(blocks) and not store.execution_started(item)
        reason = ""
        if affected:
            reason = "前置成果发生变化，请核对已执行事项的影响"
        elif blocked:
            reason = "；".join(f"{row['goal'][:80]}：{row['reason']}" for row in blocks)
        elif enabled:
            if item.id in inherited:
                reason = "优先完成前置事项，以接续：" + graph[inherited[item.id]].goal[:120]
            else:
                band = deadline_band(item, now)
                reason = (
                    "已过截止时间"
                    if band == 0
                    else "一小时内到期"
                    if band == 1
                    else "按明确优先级、请求来源和创建时间安排"
                )
        result[item.id] = {
            "blocked": blocked,
            "blocking_dependencies": blocks,
            "needs_decision": bool(affected or any(row["needs_decision"] for row in blocks)),
            "arrangement_reason": reason,
            "rank": rank,
            "can_arrange_dependencies": not store.execution_started(item)
            and item.status
            not in {
                *TERMINAL_MATTER_STATUSES,
                MatterStatus.PLANNING,
                MatterStatus.RUNNING,
                MatterStatus.WAITING_APPROVAL,
            },
            "suggestions": item.coordination_suggestions if enabled else [],
        }
    return result


def update_arrangement(matter_id: str, request: MatterArrangement) -> ButlerMatter:
    def update(item: ButlerMatter) -> None:
        if item.status in TERMINAL_MATTER_STATUSES:
            raise store.ButlerConflict("事项已结束，不能调整安排。")
        if request.depends_on is not None:
            item.depends_on = request.depends_on
        if request.priority is not None:
            item.priority = request.priority
            item.priority_explicit = True
        if request.due_at is not None:
            item.due_at = request.due_at
        item.coordination_suggestions = []

    return store.mutate(ButlerMatter, matter_id, update, expected_version=request.version)
