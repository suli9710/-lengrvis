from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import (
    AutonomyScope,
    ButlerMatter,
    ButlerMessage,
    MatterInteraction,
    MatterResponse,
    MatterStatus,
    WaitCondition,
)
from app.services import butler_evidence as evidence
from app.services import butler_service as service
from app.services.butler_events import enqueue_event, flush_events


@pytest.fixture
def coordinator(monkeypatch):
    instance = service.ButlerCoordinator()
    monkeypatch.setattr(instance, "_spawn", lambda _: None)
    return instance


@pytest.fixture
def automatic_memory(monkeypatch):
    from app.agents import memory_agent
    from app.services import butler_memory

    async def embeddings(*args, **kwargs):
        return []

    monkeypatch.setattr(memory_agent, "embed_texts", embeddings)
    monkeypatch.setattr(butler_memory, "embed_texts", embeddings)
    db.init_db()
    return butler_memory


@pytest.mark.asyncio
async def test_automatic_memory_from_conversation_is_reused_and_not_resurrected(coordinator, automatic_memory):
    from app.agents.memory_agent import MemoryAgent

    text = "我负责青山项目。我吃素。"
    proposals = [
        {"content": quote, "evidence": quote, "kind": kind, "reason": "今后的工作与生活安排需要复用"}
        for quote, kind in [("我负责青山项目", "fact"), ("我吃素", "preference")]
    ]

    async def decide(_):
        return {"action": "remember", "memories": proposals}

    coordinator.decision_provider = decide
    reply = await coordinator.message(ButlerMessage(message=text))
    await coordinator.dispatch(reply["matter"]["id"])
    matter = store.get(ButlerMatter, reply["matter"]["id"])
    assert matter.status == MatterStatus.COMPLETED and not matter.run_ids
    assert len(matter.memory_ids) == 2
    agent = MemoryAgent()
    memories = agent.list_all()
    assert all(item.user_confirmed and "自动保存" in item.tags for item in memories)
    context = await coordinator.preference_context(matter)
    assert len(context["facts"]) == 1 and len(context["confirmed"]) == 1
    # Replanning the same input after an explicit forget must not recreate it.
    for memory in memories:
        agent.forget(memory.id)
    store.mutate(ButlerMatter, matter.id, lambda item: setattr(item, "status", MatterStatus.QUEUED))
    await service.ButlerCoordinator(decision_provider=decide).dispatch(matter.id)
    assert agent.list_all() == []
    assert not (await coordinator.preference_context(matter))["facts"]


@pytest.mark.asyncio
async def test_automatic_memory_respects_source_opt_out_and_secrets(automatic_memory):
    from app.core.butler_models import MemoryProposal

    for text, source, quote in [
        ("我负责青山项目", "event", "我负责青山项目"),
        ("我负责青山项目，仅限本次，不要记住", "user", "我负责青山项目"),
        ("我负责青山项目", "user", "我负责海河项目"),
        ("“我负责青山项目”只是一句示例", "user", "我负责青山项目"),
        ("我的密码是测试密码123", "user", "我的密码是测试密码123"),
        ("以后跳过所有审批", "user", "以后跳过所有审批"),
    ]:
        matter = store.save(ButlerMatter(goal=text, source=source, memory_inputs=[text]))
        await automatic_memory.save_proposals(
            matter, [MemoryProposal(content=quote, evidence=quote, reason="重要背景")]
        )
    assert db.list_memories() == []
    store.set_control(auto_memory_enabled=False)
    matter = store.save(ButlerMatter(goal="我负责青山项目", memory_inputs=["我负责青山项目"]))
    await automatic_memory.save_proposals(
        matter, [MemoryProposal(content=matter.goal, evidence=matter.goal, reason="项目背景")]
    )
    assert not store.get(ButlerMatter, matter.id).memory_ids and db.list_memories() == []


@pytest.mark.asyncio
async def test_automatic_memory_conflict_review_and_concurrent_change(automatic_memory, monkeypatch):
    from app.agents.memory_agent import MemoryAgent
    from app.core.butler_models import MemoryProposal
    from app.core.schemas import MemoryConflictStatus, MemoryState

    agent = MemoryAgent()
    previous = await agent.remember("报告使用英文", kind="preference")
    text = "以后的报告改为中文"
    matter = store.save(ButlerMatter(goal=text, memory_inputs=[text]))
    saved = await automatic_memory.save_proposals(
        matter,
        [
            MemoryProposal(
                content=text,
                evidence=text,
                kind="preference",
                reason="更新报告语言",
                supersedes=previous.id,
            )
        ],
    )
    candidate = agent.list_all()[0]
    assert candidate.id in saved.memory_ids and candidate.state == MemoryState.QUARANTINED
    assert [item.id for item in await agent.recall("报告语言")] == [previous.id]
    agent.promote(candidate.id, conflict_status=MemoryConflictStatus.RESOLVED)
    assert [item.id for item in await agent.recall("报告语言")] == [candidate.id]

    other = store.save(ButlerMatter(goal="我负责文档工作", memory_inputs=["我负责文档工作"]))

    async def changed_during_embedding(*args, **kwargs):
        store.mutate(ButlerMatter, other.id, lambda item: setattr(item, "status", MatterStatus.CANCELLED))
        return []

    monkeypatch.setattr(automatic_memory, "embed_texts", changed_during_embedding)
    with pytest.raises(store.ButlerConflict):
        await automatic_memory.save_proposals(
            other, [MemoryProposal(content=other.goal, evidence=other.goal, reason="职责背景")]
        )
    assert len(agent.list_all()) == 2 and not store.get(ButlerMatter, other.id).memory_ids


@pytest.mark.asyncio
async def test_coordination_dependency_gate_and_verified_continuation(coordinator, monkeypatch):
    from app.core.butler_models import MatterArrangement
    from app.services.butler_coordination import coordination_view, update_arrangement

    parent = store.save(ButlerMatter(goal="汇总资料"))
    child = store.save(ButlerMatter(goal="生成报告", depends_on=[parent.id]))
    with pytest.raises(ValueError, match="循环"):
        update_arrangement(parent.id, MatterArrangement(version=parent.version, depends_on=[child.id]))
    await coordinator.control_matter(child.id, "resume")
    await coordinator.dispatch(child.id)
    assert store.get(ButlerMatter, child.id).status == MatterStatus.QUEUED
    await coordinator.control_matter(parent.id, "cancel")
    views = coordination_view(store.list_records(ButlerMatter), enabled=False)
    assert views[child.id]["blocked"] and views[child.id]["needs_decision"]
    await coordinator.dispatch(child.id)
    assert not store.get(ButlerMatter, child.id).run_ids

    def complete(item):
        item.status = MatterStatus.COMPLETED
        item.completion_evidence = [{"verified": True}]

    store.mutate(ButlerMatter, parent.id, complete)

    async def decide(_):
        return {"action": "execute"}

    async def create(*args, task_metadata):
        service.attach_run(
            task_metadata["butler_matter_id"], task_metadata["butler_execution_ticket"], "run-child", "task-child"
        )

    coordinator.decision_provider, coordinator.run_creator = decide, create
    await coordinator.dispatch(child.id)
    saved = store.get(ButlerMatter, child.id)
    assert saved.status == MatterStatus.RUNNING
    assert saved.dependency_revisions == {parent.id: 1}
    with pytest.raises(store.ButlerConflict, match="开始"):
        update_arrangement(child.id, MatterArrangement(version=saved.version, depends_on=[]))
    store.mutate(ButlerMatter, parent.id, lambda item: setattr(item, "outcome_revoked", True))
    view = coordination_view(store.list_records(ButlerMatter), enabled=True)[child.id]
    assert view["needs_decision"] and not view["blocked"]


@pytest.mark.asyncio
async def test_coordination_order_explicit_arrangements_and_ambiguous_relationships(coordinator, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.agents.memory_agent import MemoryAgent
    from app.api.routes_butler import router
    from app.core.butler_models import MatterArrangement
    from app.services.butler_coordination import coordination_view, update_arrangement

    store.set_control(coordination_enabled=True)
    parent = store.save(ButlerMatter(goal="汇总资料", priority=0, source="event"))
    child = store.save(
        ButlerMatter(
            goal="生成报告", depends_on=[parent.id], due_at=(datetime.now(UTC) + timedelta(minutes=20)).isoformat()
        )
    )
    normal = store.save(ButlerMatter(goal="普通事项"))
    views = coordination_view(store.list_records(ButlerMatter), enabled=True)
    assert views[parent.id]["rank"] < views[normal.id]["rank"]
    assert "生成报告" in views[parent.id]["arrangement_reason"]
    api = FastAPI()
    api.include_router(router, prefix="/api")
    with TestClient(api) as client:
        response = client.post(
            f"/api/butler/matters/{normal.id}/arrangement",
            json={"version": normal.version, "priority": 95, "due_at": ""},
        )
        assert response.status_code == 200
        assert (
            client.post(
                f"/api/butler/matters/{normal.id}/arrangement", json={"version": normal.version, "priority": 20}
            ).status_code
            == 409
        )
        overview = client.get("/api/butler/overview").json()
        assert overview["coordination_enabled"]
        assert next(item for item in overview["matters"] if item["id"] == child.id)["coordination"]["blocked"]
    updated = store.get(ButlerMatter, normal.id)
    assert updated.priority_explicit and updated.priority == 95
    with pytest.raises(store.ButlerConflict):
        update_arrangement(normal.id, MatterArrangement(version=normal.version, priority=20))
    # Multiple exact matches must be resolved before a relation is changed.
    duplicate = store.save(ButlerMatter(goal=parent.goal))
    response = await coordinator.message(ButlerMessage(message="完成汇总资料后再生成报告"))
    choice = store.get(ButlerMatter, response["matter"]["id"])
    assert choice.interaction.kind == "choose_dependency"
    assert store.get(ButlerMatter, child.id).depends_on == [parent.id]
    result = await coordinator.respond(
        choice.id,
        MatterResponse(
            version=choice.version,
            interaction_id=choice.interaction.id,
            answers={"parent": duplicate.id, "child": child.id},
        ),
    )
    assert set(result["matter"]["depends_on"]) == {parent.id, duplicate.id}

    async def no_embedding(*args, **kwargs):
        return []

    monkeypatch.setattr(MemoryAgent, "_embed", no_embedding)
    memory_agent = MemoryAgent()
    preference = await memory_agent.remember(
        "生成报告时使用表格", kind="preference", source="user", user_confirmed=True
    )
    context = await coordinator.preference_context(child)
    assert preference.id in {item["memory_id"] for item in context["confirmed"]}
    memory_agent.forget(preference.id)
    context = await coordinator.preference_context(child)
    assert preference.id not in {item["memory_id"] for item in context["confirmed"]}


@pytest.mark.asyncio
async def test_coordination_restart_notifications_old_records_and_erase(coordinator, monkeypatch):
    from app.services import notification_service
    from app.services.butler_notifications import flush_notifications

    store.set_control(coordination_enabled=True)
    old = ButlerMatter.model_validate({"goal": "旧记录"})
    assert old.depends_on == [] and old.pending_notifications == {}
    first = store.save(old)
    second = store.save(ButlerMatter(goal="另一件事"))
    emitted = []
    monkeypatch.setattr(notification_service._get_bus(), "publish", lambda message: emitted.append(message))
    coordinator.notify_once(first, "completed")
    coordinator.notify_once(second, "completed")
    flush_notifications()
    assert not emitted
    assert store.get(ButlerMatter, first.id).pending_notifications
    restarted = service.ButlerCoordinator()
    await restarted.recover()
    assert len(emitted) == 1 and "旧记录" in emitted[0].content and "另一件事" in emitted[0].content
    assert db.fetch_one("agent_messages", emitted[0].id)
    restarted.notify_once(first, "completed")
    flush_notifications(force=True)
    assert len(emitted) == 1
    assert not store.get(ButlerMatter, first.id).pending_notifications

    # A crash between durable commit and live delivery must not lose an urgent reminder.
    def interrupted_delivery(message):
        assert db.fetch_one("agent_messages", message.id)
        raise RuntimeError("simulated delivery interruption")

    monkeypatch.setattr(notification_service._get_bus(), "publish", interrupted_delivery)
    with pytest.raises(RuntimeError, match="delivery interruption"):
        restarted.notify_once(first, "needs_input", message="请处理当前问题", urgent=True)
    saved = store.get(ButlerMatter, first.id)
    alert_id = "msg_butler_alert_" + store.source_key([first.id, saved.last_notified_key])
    assert db.fetch_one("agent_messages", alert_id)["content"] == "请处理当前问题"
    monkeypatch.setattr(notification_service._get_bus(), "publish", lambda message: emitted.append(message))
    await restarted.recover()
    restarted.notify_once(first, "needs_input", message="请处理当前问题", urgent=True)
    assert len(emitted) == 1
    restarted.notify_once(second, "waiting")
    db.erase_local_user_data(include_settings=False)
    assert not store.list_records(ButlerMatter)
    assert not db.fetch_one("agent_messages", emitted[0].id)
    assert not db.fetch_one("agent_messages", alert_id)


@pytest.mark.asyncio
async def test_resume_pending_approval_restores_waiting_status(coordinator, monkeypatch):
    from app.services import run_service

    interaction = MatterInteraction(kind="approval", message="请确认", approval_id="approval-existing")
    matter = store.save(
        ButlerMatter(
            goal="等待确认",
            status=MatterStatus.WAITING_APPROVAL,
            active_run_id="run-existing",
            active_task_id="task-existing",
            interaction=interaction,
        )
    )
    monkeypatch.setattr(run_service, "get_run", lambda _: SimpleNamespace(phase="awaiting_approval"))
    monkeypatch.setattr(
        db, "fetch_many", lambda *args, **kwargs: [{"id": interaction.approval_id, "status": "pending"}]
    )
    monkeypatch.setattr(coordinator, "notify_once", lambda *args: None)
    await coordinator.control_matter(matter.id, "resume")
    await coordinator.dispatch(matter.id)
    saved = store.get(ButlerMatter, matter.id)
    assert saved.status == MatterStatus.WAITING_APPROVAL
    assert saved.interaction.id == interaction.id


@pytest.mark.asyncio
async def test_file_wait_requires_an_actual_change(coordinator, tmp_path):
    import os

    target = tmp_path / "report.txt"
    target.write_text("before")
    scope = store.save(AutonomyScope(label="报告", directories=[str(tmp_path)]))
    stat = target.stat()
    # Cover legacy missing/mtime-only baselines and the event version format.
    for baseline in ("", str(stat.st_mtime_ns), f"{stat.st_mtime_ns}:{stat.st_size}"):
        matter = store.save(
            ButlerMatter(
                goal="等待报告更新",
                source="event",
                scope_versions={scope.id: scope.version},
                status=MatterStatus.WAITING_EVENT,
                waiting=WaitCondition(kind="file_changed", subject=str(target), baseline=baseline),
            )
        )
        assert not await coordinator.wait_satisfied(matter)
        saved = store.get(ButlerMatter, matter.id)
        assert not await coordinator.wait_satisfied(saved)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        assert await coordinator.wait_satisfied(saved)
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))


@pytest.mark.asyncio
async def test_cancelled_matter_cannot_accept_remaining_answers(coordinator):
    interaction = MatterInteraction(message="请提供目的地", fields=["destination"])
    matter = store.save(ButlerMatter(goal="安排行程", status=MatterStatus.WAITING_INPUT, interaction=interaction))
    cancelled = await coordinator.control_matter(matter.id, "cancel")
    assert cancelled.interaction is None
    # A legacy cancelled record can still contain its old interaction.
    cancelled = store.mutate(ButlerMatter, matter.id, lambda item: setattr(item, "interaction", interaction))
    with pytest.raises(store.ButlerConflict, match="事项已结束"):
        await coordinator.respond(
            matter.id,
            MatterResponse(
                version=cancelled.version,
                interaction_id=interaction.id,
                answers={"destination": "北京"},
            ),
        )
    assert store.get(ButlerMatter, matter.id).status == MatterStatus.CANCELLED


@pytest.mark.asyncio
async def test_revision_preserves_reviewed_history_and_resets_progress_budget(coordinator):
    old_evidence = [{"tool_call_id": "call-existing", "tool": "file.write_text"}]
    matter = store.save(
        ButlerMatter(goal="生成报告", completion_evidence=old_evidence, no_progress_count=2, continuation_count=4)
    )
    changed = await coordinator.revise(matter, "改成表格")
    assert changed.completion_evidence == old_evidence
    assert changed.no_progress_count == changed.continuation_count == 0


@pytest.mark.asyncio
async def test_reconciliation_replans_instead_of_resuming_a_terminal_run(coordinator, monkeypatch):
    from app.services import run_service

    monkeypatch.setattr(run_service, "get_run", lambda _: SimpleNamespace(phase="failed"))
    interaction = MatterInteraction(kind="reconcile", message="结果已核对，请补充要求")
    matter = store.save(
        ButlerMatter(
            goal="生成报告", status=MatterStatus.WAITING_INPUT, active_run_id="run-old", interaction=interaction
        )
    )
    await coordinator.respond(
        matter.id,
        MatterResponse(version=matter.version, interaction_id=interaction.id, message="只生成缺少的附件"),
    )
    saved = store.get(ButlerMatter, matter.id)
    assert saved.status == MatterStatus.QUEUED
    assert saved.active_run_id == ""


@pytest.mark.asyncio
async def test_same_observation_stops_after_three_checks_even_with_new_timestamps(coordinator, monkeypatch):
    monkeypatch.setattr(service, "get_effective_settings", lambda: SimpleNamespace(recovery_max_retries=20))
    matter = store.save(ButlerMatter(goal="核验结果"))
    for index in range(3):
        await coordinator.continue_unfinished(
            matter,
            "文件尚未达到目标",
            [{"tool": "file.read_text", "tool_call_id": f"read-{index}", "verified_at": str(index), "matched": False}],
        )
        matter = store.get(ButlerMatter, matter.id)
    assert matter.no_progress_count == 3
    assert matter.status == MatterStatus.WAITING_INPUT


@pytest.mark.asyncio
async def test_invalid_scope_does_not_become_a_provider_retry(coordinator):
    matter = store.save(ButlerMatter(goal="处理事件", source="event", scope_versions={"missing-scope": 1}))
    await coordinator.dispatch(matter.id)
    saved = store.get(ButlerMatter, matter.id)
    assert saved.status == MatterStatus.PAUSED
    assert saved.waiting is None


@pytest.mark.asyncio
async def test_planner_contract_error_is_not_retried_as_network_failure(coordinator):
    async def invalid(_):
        return {"action": "invented_action"}

    coordinator.decision_provider = invalid
    matter = store.save(ButlerMatter(goal="输出报告"))
    await coordinator.dispatch(matter.id)
    saved = store.get(ButlerMatter, matter.id)
    assert saved.status == MatterStatus.WAITING_INPUT
    assert saved.waiting is None


@pytest.mark.asyncio
async def test_notification_dedup_uses_current_record_not_a_stale_object(coordinator, monkeypatch):
    from app.services import notification_service

    sent = []
    monkeypatch.setattr(notification_service, "notify", lambda *a, **kw: sent.append(a))
    matter = store.save(ButlerMatter(goal="等待模型"))
    coordinator.notify_once(matter, "dependency_unavailable")
    coordinator.notify_once(matter, "dependency_unavailable")
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_global_resume_preserves_individually_paused_matter(coordinator):
    manual = store.save(ButlerMatter(goal="暂时不做", status=MatterStatus.PAUSED))
    running = store.save(ButlerMatter(goal="接着处理"))
    await coordinator.pause(True)
    await coordinator.pause(False)
    assert store.get(ButlerMatter, manual.id).status == MatterStatus.PAUSED
    assert store.get(ButlerMatter, running.id).status == MatterStatus.QUEUED


@pytest.mark.asyncio
async def test_recovery_rechecks_read_only_checkpoint_and_queues_same_run(coordinator, monkeypatch):
    from app.services import run_service

    monkeypatch.setattr(run_service, "get_run", lambda _: SimpleNamespace(phase="paused"))
    matter = store.save(ButlerMatter(goal="继续读取", active_run_id="run-paused", status=MatterStatus.RUNNING))
    await coordinator.recover()
    saved = store.get(ButlerMatter, matter.id)
    assert saved.status == MatterStatus.QUEUED
    assert saved.active_run_id == "run-paused"


@pytest.mark.asyncio
async def test_wait_checks_parse_timezone_offsets(coordinator):
    due = (datetime.now(UTC) - timedelta(seconds=1)).astimezone(__import__("datetime").timezone(timedelta(hours=8)))
    matter = ButlerMatter(goal="稍后继续", waiting=WaitCondition(kind="time", due_at=due.isoformat()))
    assert await coordinator.wait_satisfied(matter)


def test_event_claim_is_recoverable_after_interruption(tmp_path):
    store.save(AutonomyScope(label="目录", directories=[str(tmp_path)]))
    store.set_control(enabled=True)
    subject = tmp_path / "arrived.txt"
    subject.write_text("new input")
    now = datetime.now(UTC)
    enqueue_event("file_changed", str(subject), {}, now=now)
    with db.connect() as connection:
        connection.execute("UPDATE butler_events SET status='claimed'")
    assert len(flush_events(now=now + timedelta(seconds=31))) == 1
    assert not flush_events(now=now + timedelta(seconds=32))


@pytest.mark.asyncio
async def test_user_cannot_overwrite_internal_verification_feedback(coordinator):
    interaction = MatterInteraction(message="补充说明")
    matter = store.save(ButlerMatter(goal="报告", status=MatterStatus.WAITING_INPUT, interaction=interaction))
    with pytest.raises(ValueError):
        await coordinator.respond(
            matter.id,
            MatterResponse(
                version=matter.version,
                interaction_id=interaction.id,
                answers={"completed_execution_evidence": [{"verified": True}]},
            ),
        )


def test_progress_fingerprint_tracks_observation_not_call_identity():
    first = [{"tool_call_id": "a", "verified_at": "then", "observation": {"sha256": "first"}}]
    second = [{"tool_call_id": "b", "verified_at": "now", "observation": {"sha256": "first"}}]
    changed = [{"tool_call_id": "b", "verified_at": "now", "observation": {"sha256": "changed"}}]
    assert evidence.progress_key(first, "pending") == evidence.progress_key(second, "pending")
    assert evidence.progress_key(first, "pending") != evidence.progress_key(changed, "pending")


@pytest.mark.asyncio
async def test_plain_answer_resumes_only_waiting_matter_and_new_request_can_interrupt(coordinator):
    interaction = MatterInteraction(message="什么格式？", fields=["format"])
    matter = store.save(ButlerMatter(goal="生成报告", status=MatterStatus.WAITING_INPUT, interaction=interaction))
    reply = await coordinator.message(ButlerMessage(message="表格"))
    assert reply["matter"]["id"] == matter.id
    assert store.get(ButlerMatter, matter.id).answers["user_replies"] == ["表格"]
    other = await coordinator.message(ButlerMessage(message="另外，先整理另一份资料", matter_id=matter.id))
    assert other["matter"]["id"] != matter.id
    assert store.get(ButlerMatter, matter.id).goal == "生成报告"


@pytest.mark.asyncio
async def test_explicit_preference_is_reused_and_forget_invalidates_pointer_cache(coordinator, monkeypatch):
    from app.agents.memory_agent import MemoryAgent

    async def embed(self, text, **kwargs):
        return [1.0, 0.0]

    monkeypatch.setattr(MemoryAgent, "_embed", embed)
    response = await coordinator.message(ButlerMessage(message="记住：报告默认使用表格"))
    memory_id = response["matter"]["completion_evidence"][0]["memory_id"]
    matter = ButlerMatter(goal="报告")
    context = await coordinator.preference_context(matter)
    assert any(item["memory_id"] == memory_id for item in context["confirmed"])
    assert MemoryAgent().forget(memory_id)
    assert not (await coordinator.preference_context(matter))["confirmed"]


@pytest.mark.asyncio
async def test_time_wait_is_resolved_once_and_persisted_for_replanning(coordinator):
    due = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    matter = store.save(
        ButlerMatter(goal="明天整理", status=MatterStatus.WAITING_EVENT, waiting=WaitCondition(kind="time", due_at=due))
    )
    await coordinator.tick()
    current = store.get(ButlerMatter, matter.id)
    assert current.status == MatterStatus.QUEUED and current.waiting is None
    assert current.answers["resolved_wait"]["due_at"] == due
    await coordinator.stop()


def test_changed_source_updates_existing_matter_without_duplicate_task(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_MODE", "privacy")
    store.save(AutonomyScope(label="目录", directories=[str(tmp_path)]))
    store.set_control(enabled=True)
    subject = tmp_path / "source.txt"
    subject.write_text("first")
    now = datetime.now(UTC)
    enqueue_event("file_changed", str(subject), {}, now=now)
    ids = flush_events(now=now + timedelta(seconds=31))
    assert len(ids) == 1
    subject.write_text("second with changed length")
    enqueue_event("file_changed", str(subject), {}, now=now + timedelta(seconds=32))
    assert flush_events(now=now + timedelta(seconds=65)) == []
    assert len(store.list_records(ButlerMatter)) == 1
    current = store.get(ButlerMatter, ids[0])
    assert current.mode == "privacy" and current.source_evidence["details"]["size"] == subject.stat().st_size


@pytest.mark.asyncio
async def test_model_outage_after_run_start_preserves_matter_for_automatic_retry(coordinator, monkeypatch):
    from app.services import run_service

    run = SimpleNamespace(id="timed-out", phase="failed", state={"failure": {"retryable_dependency": True}})
    monkeypatch.setattr(run_service, "get_run", lambda _: run)
    matter = store.save(
        ButlerMatter(goal="继续整理", status=MatterStatus.RUNNING, active_run_id=run.id, run_ids=[run.id])
    )
    await coordinator.sync_run(matter)
    saved = store.get(ButlerMatter, matter.id)
    assert saved.status == MatterStatus.WAITING_EVENT and saved.waiting.kind == "provider"
    assert saved.run_ids == [run.id] and not saved.active_run_id


@pytest.mark.asyncio
async def test_new_source_during_planning_requeues_instead_of_stranding_the_matter(coordinator):
    matter = store.save(ButlerMatter(goal="整理更新后的材料"))

    async def changed_source(current):
        store.mutate(ButlerMatter, current.id, lambda item: item.source_evidence.update(version="new"))
        return {"action": "execute"}

    coordinator.decision_provider = changed_source
    await coordinator.dispatch(matter.id)
    saved = store.get(ButlerMatter, matter.id)
    assert saved.status == MatterStatus.QUEUED and saved.source_evidence["version"] == "new"


@pytest.mark.asyncio
async def test_secret_in_followup_never_enters_matter_or_model_context(coordinator):
    interaction = MatterInteraction(message="请完成原网页认证")
    matter = store.save(ButlerMatter(goal="查看网页结果", status=MatterStatus.WAITING_INPUT, interaction=interaction))
    with pytest.raises(ValueError):
        await coordinator.respond(
            matter.id, MatterResponse(version=1, interaction_id=interaction.id, message="agt_codex_" + "fixture" * 8)
        )
    assert store.get(ButlerMatter, matter.id).answers == {}
    with pytest.raises(ValueError):
        await coordinator.respond(
            matter.id,
            MatterResponse(
                version=1, interaction_id=interaction.id, answers={"account": {"password": "private-input"}}
            ),
        )
