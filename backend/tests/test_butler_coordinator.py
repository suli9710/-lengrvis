from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core import butler_store as store
from app.core.butler_models import (
    AutonomyScope,
    ButlerMatter,
    ButlerMessage,
    MatterInteraction,
    MatterResponse,
    MatterStatus,
    WaitCondition,
)
from app.services.butler_events import enqueue_event, flush_events
from app.services.butler_service import ButlerCoordinator, attach_run


@pytest.fixture
def coordinator(monkeypatch):
    instance = ButlerCoordinator()
    monkeypatch.setattr(instance, "_spawn", lambda _: None)
    return instance


@pytest.mark.asyncio
async def test_missing_information_is_answered_on_the_same_matter_and_version(coordinator):
    decisions = []

    async def decide(matter):
        decisions.append(matter.answers)
        return {"action": "ask", "reason": "请提供出发地和日期", "missing_fields": ["origin", "date"]}

    coordinator.decision_provider = decide
    response = await coordinator.message(ButlerMessage(message="安排去北京的行程"))
    matter_id = response["matter"]["id"]
    await coordinator.dispatch(matter_id)
    matter = store.get(ButlerMatter, matter_id)
    answer = MatterResponse(
        version=matter.version, interaction_id=matter.interaction.id, answers={"origin": "上海", "date": "2026-10-02"}
    )
    response = await coordinator.respond(matter_id, answer)
    assert response["matter"]["id"] == matter_id
    assert response["matter"]["answers"]["origin"] == "上海"
    with pytest.raises(store.ButlerConflict):
        await coordinator.respond(matter_id, answer)


@pytest.mark.asyncio
async def test_ambiguous_followup_does_not_mutate_an_unrelated_task(coordinator):
    first = store.save(ButlerMatter(goal="写报告"))
    second = store.save(ButlerMatter(goal="整理文件"))
    response = await coordinator.message(ButlerMessage(message="改成明天再做"))
    assert response["matter"]["interaction"]["kind"] == "choose_matter"
    assert set(response["matter"]["interaction"]["candidates"]) == {first.id, second.id}
    assert store.get(ButlerMatter, first.id).version == 1
    assert store.get(ButlerMatter, second.id).version == 1


@pytest.mark.asyncio
async def test_scope_revocation_invalidates_pending_events_and_pauses_matters(coordinator, tmp_path):
    scope = store.save(AutonomyScope(label="工作", directories=[str(tmp_path)]))
    matter = store.save(ButlerMatter(goal="整理", source="event", scope_versions={scope.id: scope.version}))
    store.set_control(enabled=True)
    file = tmp_path / "new.txt"
    file.write_text("ordinary text")
    assert enqueue_event("file_changed", str(file), {}) == 1
    await coordinator.revoke_scope(scope.id, scope.version)
    assert store.get(ButlerMatter, matter.id).status == MatterStatus.PAUSED
    assert flush_events(now=datetime.now(UTC) + timedelta(minutes=1)) == []


def test_events_are_scoped_batched_and_deduplicated(tmp_path):
    store.save(AutonomyScope(label="目录", directories=[str(tmp_path)]))
    store.set_control(enabled=True)
    now = datetime.now(UTC)
    file = tmp_path / "report.txt"
    file.write_text("report")
    assert enqueue_event("file_changed", str(file), {}, now=now) == 1
    assert enqueue_event("file_changed", str(file), {}, now=now) == 0
    assert enqueue_event("file_changed", str(tmp_path.parent / "outside.txt"), {}, now=now) == 0
    assert enqueue_event("app_switched", "Calculator", {}, now=now) == 0
    assert not flush_events(now=now + timedelta(seconds=29))
    ids = flush_events(now=now + timedelta(seconds=31))
    assert len(ids) == 1
    assert not flush_events(now=now + timedelta(seconds=60))


@pytest.mark.asyncio
async def test_waiting_file_resumes_only_inside_its_scope(coordinator, tmp_path):
    scope = store.save(AutonomyScope(label="目录", directories=[str(tmp_path)]))
    file = tmp_path / "later.txt"
    matter = store.save(
        ButlerMatter(
            goal="收到文件后整理",
            source="event",
            scope_versions={scope.id: 1},
            status=MatterStatus.WAITING_EVENT,
            waiting=WaitCondition(kind="file_exists", subject=str(file)),
        )
    )
    assert not await coordinator.wait_satisfied(matter)
    file.write_text("new source")
    assert await coordinator.wait_satisfied(matter)


@pytest.mark.asyncio
async def test_runtime_binding_exists_before_the_run_can_execute(coordinator):
    captured = []

    async def decide(matter):
        return {"action": "execute", "completion_conditions": [{"kind": "execution_evidence"}]}

    async def create(goal, mode, engine, *, task_metadata):
        attach_run(
            task_metadata["butler_matter_id"], task_metadata["butler_execution_ticket"], "run-owned", "task-owned"
        )
        captured.append(task_metadata)

    coordinator.decision_provider = decide
    coordinator.run_creator = create
    matter = store.save(ButlerMatter(goal="读取文件"))
    await coordinator.dispatch(matter.id)
    saved = store.get(ButlerMatter, matter.id)
    assert saved.status == MatterStatus.RUNNING and saved.active_task_id == "task-owned"
    assert captured[0]["butler_execution_ticket"] == saved.execution_ticket


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting", [None, {"kind": "user"}])
async def test_user_wait_accepts_conversational_answer_on_original_matter(coordinator, waiting):
    async def decide(matter):
        return {"action": "wait", "reason": "请在原应用完成登录后告诉我", "waiting": waiting}

    coordinator.decision_provider = decide
    original = await coordinator.message(ButlerMessage(message="整理已登录应用中的资料"))
    matter_id = original["matter"]["id"]
    await coordinator.dispatch(matter_id)
    waiting_matter = store.get(ButlerMatter, matter_id)
    assert waiting_matter.status == MatterStatus.WAITING_INPUT
    assert waiting_matter.interaction is not None

    response = await coordinator.message(ButlerMessage(message="已经登录好了"))
    assert response["matter"]["id"] == matter_id
    assert response["matter"]["status"] == MatterStatus.QUEUED
    assert response["matter"]["answers"]["user_replies"] == ["已经登录好了"]
    assert len(store.list_records(ButlerMatter)) == 1


@pytest.mark.asyncio
async def test_automatic_wait_remains_an_event_wait_without_user_interaction(coordinator):
    async def decide(matter):
        return {
            "action": "wait",
            "reason": "等待约定时间",
            "waiting": {"kind": "time", "due_at": "2030-01-01T00:00:00+00:00"},
        }

    coordinator.decision_provider = decide
    matter = store.save(ButlerMatter(goal="到约定时间再处理"))
    await coordinator.dispatch(matter.id)
    saved = store.get(ButlerMatter, matter.id)
    assert saved.status == MatterStatus.WAITING_EVENT
    assert saved.interaction is None


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting", [None, WaitCondition(kind="user")])
async def test_recover_repairs_legacy_user_handoffs(coordinator, waiting):
    matter = store.save(
        ButlerMatter(
            goal="接续网页任务",
            status=MatterStatus.WAITING_EVENT,
            waiting=waiting,
            interaction=MatterInteraction(kind="takeover", message="请完成登录"),
        )
    )
    await coordinator.recover()
    assert store.get(ButlerMatter, matter.id).status == MatterStatus.WAITING_INPUT
    response = await coordinator.message(ButlerMessage(message="登录好了"))
    assert response["matter"]["id"] == matter.id
    assert response["matter"]["status"] == MatterStatus.QUEUED
