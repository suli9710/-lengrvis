from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.config import AppSettings
from app.context.agent_message_projection import llm_safe_agent_message
from app.context.compaction import compact_session_context, manual_compact_result_to_dict
from app.context.management import (
    ContextProjection,
    auto_compact_messages,
    build_llm_request_snapshot,
    project_messages_for_llm,
)
from app.context.summary_provenance import (
    CONVERSATION_SEGMENT_SOURCE_KIND,
    LEGACY_SUMMARY_SOURCE_KIND,
    SUMMARY_PROVENANCE_VERSION,
    SummaryProvenanceError,
    build_summary_content_envelope,
    summary_anchor_source_id,
    validate_summary_content_envelope,
)
from app.core import db
from app.core.content_provenance import content_envelope_integrity_valid, create_content_envelope
from app.core.schemas import AgentMessage, ContentEnvelope, MessageType, Task, TaskStatus
from app.core.session_context import (
    SessionContext,
    SessionContextStore,
    SessionSummaryConflictError,
)
from app.orchestration.agent_bus import AgentBus
from app.orchestration.handlers.completion_handler import CompletionHandler


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.reset_connection_state()
    db.reset_init_db_cache()
    db.init_db()


def _messages() -> list[dict[str, str]]:
    return [
        {"id": "msg_1", "role": "user", "content": "Draft the release note."},
        {"id": "msg_2", "role": "assistant", "content": "The draft is ready."},
    ]


def _envelope(summary: str = "Release note drafted.") -> ContentEnvelope:
    return build_summary_content_envelope(
        summary,
        _messages(),
        session_id="session_lineage",
        last_message_id="msg_2",
        source_message_ids=["msg_1", "msg_2"],
        task_id="task_release",
    )


def _settings() -> AppSettings:
    return AppSettings(
        model_context_window=2000,
        model_auto_compact_token_limit=1600,
        max_tokens=200,
        context_recent_message_limit=2,
        context_history_snip_enabled=False,
        context_micro_compact_enabled=False,
        context_auto_compact_enabled=False,
        context_session_summary_limit=1000,
    )


def test_summary_envelope_hmac_binds_root_field_to_message_id_segment() -> None:
    envelope = _envelope()

    assert content_envelope_integrity_valid(envelope)
    assert envelope.source_kind == "conversation_summary"
    assert len(envelope.field_lineage) == 1
    edge = envelope.field_lineage[0]
    assert edge.output_pointer == ""
    assert edge.source_pointer == ""
    assert edge.source_kind == CONVERSATION_SEGMENT_SOURCE_KIND
    assert edge.source_id.startswith("conversation-segment:v1:2:")
    assert edge.operation == "summarize"

    wire = envelope.model_dump(mode="json")
    assert "field_lineage" not in wire
    restored = ContentEnvelope.model_validate(wire)
    assert restored.field_lineage == envelope.field_lineage
    assert (
        validate_summary_content_envelope(
            "Release note drafted.",
            restored,
            session_id="session_lineage",
            last_message_id="msg_2",
            source_message_ids=["msg_1", "msg_2"],
        )
        == restored
    )


def test_legacy_empty_lineage_hmac_cannot_authenticate_injected_summary_mapping() -> None:
    legitimate = _envelope()
    legacy_root = create_content_envelope(
        "Release note drafted.",
        source_kind="conversation_summary",
        source_id=summary_anchor_source_id("session_lineage", "msg_2"),
        task_scope="session_lineage",
    )
    injected = legacy_root.model_copy(update={"field_lineage": legitimate.field_lineage})

    assert content_envelope_integrity_valid(injected) is False
    with pytest.raises(SummaryProvenanceError, match="integrity check failed"):
        validate_summary_content_envelope(
            "Release note drafted.",
            injected,
            session_id="session_lineage",
            last_message_id="msg_2",
            source_message_ids=["msg_1", "msg_2"],
        )


def test_session_store_persists_authenticated_summary_and_diagnostics() -> None:
    store = SessionContextStore(session_id="session_lineage")
    store.load()
    store.remember_summary(
        "Release note drafted.",
        last_message_id="msg_2",
        summary_envelope=_envelope(),
        token_stats={"summary_source_message_ids": ["msg_1", "msg_2"]},
    )

    reloaded = SessionContextStore(session_id="session_lineage").load()
    diagnostics = reloaded.lineage_diagnostics()

    assert reloaded.token_stats["summary_provenance_version"] == SUMMARY_PROVENANCE_VERSION
    assert diagnostics["summary_provenance_status"] == "authenticated"
    assert diagnostics["summary_anchor_authenticated"] is True
    assert diagnostics["summary_field_mapping_count"] == 1
    assert diagnostics["summary_source_message_count"] == 2
    assert len(diagnostics["summary_message_id_digests"]) == 1


def test_compact_session_persists_summary_envelope_bound_to_compacted_message_ids() -> None:
    messages = [
        {"id": f"msg_{index}", "role": "user", "content": f"message {index} " + ("x" * 100)} for index in range(6)
    ]
    store = SessionContextStore(session_id="session_compaction")

    result = compact_session_context(messages, _settings(), session_store=store)

    envelope = result.summary_content_envelope
    assert envelope is not None
    compacted_ids = result.compact_metadata["messages_to_summarize_ids"]
    assert (
        validate_summary_content_envelope(
            result.summary,
            envelope,
            session_id="session_compaction",
            last_message_id="msg_5",
            source_message_ids=compacted_ids,
        )
        == envelope
    )
    assert result.boundary_message["metadata"]["summary_content_envelope"]
    public_result = manual_compact_result_to_dict(result)
    assert public_result["summary_content_envelope"]
    assert "summary_content_envelope" not in public_result["compact_metadata"]
    assert "summary_content_envelope" not in public_result["boundary_message"]["metadata"]
    reloaded = SessionContextStore(session_id="session_compaction").load()
    assert reloaded.lineage_diagnostics()["summary_source_message_count"] == len(compacted_ids)


def test_persisted_compaction_refuses_missing_message_id_without_legacy_fallback() -> None:
    messages = [
        {"id": f"msg_{index}", "role": "user", "content": f"message {index} " + ("x" * 100)} for index in range(6)
    ]
    messages[1].pop("id")
    store = SessionContextStore(session_id="session_missing_id")

    with pytest.raises(SummaryProvenanceError, match="one stable message id"):
        compact_session_context(messages, _settings(), session_store=store)

    assert SessionContextStore(session_id="session_missing_id").load().conversation_summary == ""


def test_persisted_compaction_refuses_unanchored_summary_without_legacy_fallback() -> None:
    messages = [{"role": "user", "content": f"message {index} " + ("x" * 100)} for index in range(6)]
    store = SessionContextStore(session_id="session_unanchored")

    with pytest.raises(SummaryProvenanceError, match="stable message anchor"):
        compact_session_context(messages, _settings(), session_store=store)

    assert SessionContextStore(session_id="session_unanchored").load().conversation_summary == ""


def test_summary_envelope_is_removed_from_provider_safe_agent_metadata() -> None:
    wire = _envelope().model_dump(mode="json")
    message = AgentMessage(
        task_id="task_release",
        from_agent="ContextManager",
        message_type=MessageType.NOTIFICATION,
        content="summary boundary",
        metadata={
            "summary_content_envelope": wire,
            "compact_metadata": {"summary_content_envelope": wire},
        },
    )

    safe = llm_safe_agent_message(message)

    assert "summary_content_envelope" not in safe["metadata"]
    assert "summary_content_envelope" not in safe["metadata"]["compact_metadata"]


def test_private_summary_envelope_is_removed_from_public_stats_and_projection_metadata() -> None:
    wire = _envelope().model_dump(mode="json")
    context = SessionContext(
        id="session_public_stats",
        token_stats={
            "compact_metadata": {
                "summary_content_envelope": wire,
                "nested": {"summary_content_envelope": wire, "visible": True},
            }
        },
    )
    projection = ContextProjection(
        messages=[],
        original_count=0,
        projected_count=0,
        original_tokens=0,
        projected_tokens=0,
        compact_metadata=context.token_stats["compact_metadata"],
    )

    public_stats = context.public_token_stats()
    public_projection = projection.to_dict()

    assert "summary_content_envelope" not in public_stats["compact_metadata"]
    assert "summary_content_envelope" not in public_stats["compact_metadata"]["nested"]
    assert public_stats["compact_metadata"]["nested"]["visible"] is True
    assert "summary_content_envelope" not in public_projection["compact_metadata"]
    assert "summary_content_envelope" not in public_projection["compact_metadata"]["nested"]


def test_summary_cas_rejects_stale_authenticated_overwrite() -> None:
    first = SessionContextStore(session_id="session_summary_cas")
    second = SessionContextStore(session_id="session_summary_cas")
    first_snapshot = first.load().updated_at
    second_snapshot = second.load().updated_at
    messages = [{"id": "msg_a", "role": "user", "content": "first"}]
    first_summary = "First authenticated summary."
    second_summary = "Stale authenticated summary."
    first_envelope = build_summary_content_envelope(
        first_summary,
        messages,
        session_id="session_summary_cas",
        last_message_id="msg_a",
        source_message_ids=["msg_a"],
    )
    second_envelope = build_summary_content_envelope(
        second_summary,
        messages,
        session_id="session_summary_cas",
        last_message_id="msg_a",
        source_message_ids=["msg_a"],
    )

    first.remember_summary(
        first_summary,
        last_message_id="msg_a",
        summary_envelope=first_envelope,
        expected_updated_at=first_snapshot,
        token_stats={"summary_source_message_ids": ["msg_a"]},
    )
    with pytest.raises(SessionSummaryConflictError, match="changed"):
        second.remember_summary(
            second_summary,
            last_message_id="msg_a",
            summary_envelope=second_envelope,
            expected_updated_at=second_snapshot,
            token_stats={"summary_source_message_ids": ["msg_a"]},
        )
    second.current.notes.append("stale whole-row update")
    with pytest.raises(SessionSummaryConflictError, match="changed"):
        second.save()

    reloaded = SessionContextStore(session_id="session_summary_cas").load()
    assert reloaded.conversation_summary == first_summary
    assert reloaded.notes == []


def test_stale_session_state_writer_reloads_and_preserves_new_summary() -> None:
    summary_writer = SessionContextStore(session_id="session_state_merge")
    stale_state_writer = SessionContextStore(session_id="session_state_merge")
    summary_writer.load()
    stale_state_writer.load()
    messages = [{"id": "msg_summary", "role": "assistant", "content": "done"}]
    summary = "Authenticated result."
    envelope = build_summary_content_envelope(
        summary,
        messages,
        session_id="session_state_merge",
        last_message_id="msg_summary",
        source_message_ids=["msg_summary"],
    )
    summary_writer.remember_summary(
        summary,
        last_message_id="msg_summary",
        summary_envelope=envelope,
        token_stats={"summary_source_message_ids": ["msg_summary"]},
    )

    stale_state_writer.remember_task("task_after_summary", workflow_state={"phase": "verify"})

    reloaded = SessionContextStore(session_id="session_state_merge").load()
    assert reloaded.conversation_summary == summary
    assert reloaded.conversation_summary_envelope == envelope
    assert reloaded.active_task_ids == ["task_after_summary"]
    assert reloaded.current_workflow_state["phase"] == "verify"


def test_auto_compact_boundary_authenticates_summary_and_message_id_segment() -> None:
    messages = [
        {"id": f"msg_{index}", "role": "user", "content": f"message {index} " + ("x" * 900)} for index in range(10)
    ]
    settings = _settings()
    settings.context_auto_compact_enabled = True
    settings.model_auto_compact_token_limit = 1

    compacted, changed = auto_compact_messages(messages, settings)

    assert changed
    boundary = next(
        message for message in compacted if (message.get("metadata") or {}).get("context_boundary") == "auto_compact"
    )
    metadata = boundary["metadata"]
    envelope = ContentEnvelope.model_validate(metadata["summary_content_envelope"])
    summarized_ids = [f"msg_{index}" for index in range(6)]
    assert (
        validate_summary_content_envelope(
            metadata["summary"],
            envelope,
            session_id="projection",
            last_message_id="msg_9",
            source_message_ids=summarized_ids,
        )
        == envelope
    )


def test_auto_compact_preserves_legacy_session_summary_taint() -> None:
    store = SessionContextStore(session_id="session_auto_legacy")
    store.load()
    store.remember_summary("Legacy session context.", last_message_id="msg_prior")
    session_context = store.planning_context(include_private_summary_envelope=True)
    messages = [
        {"id": f"msg_{index}", "role": "user", "content": f"message {index} " + ("x" * 900)} for index in range(10)
    ]
    settings = _settings()
    settings.context_auto_compact_enabled = True
    settings.model_auto_compact_token_limit = 1

    compacted, changed = auto_compact_messages(messages, settings, session_context=session_context)

    assert changed
    boundary = next(
        message for message in compacted if (message.get("metadata") or {}).get("context_boundary") == "auto_compact"
    )
    envelope = ContentEnvelope.model_validate(boundary["metadata"]["summary_content_envelope"])
    assert "legacy_summary_lineage_unavailable" in envelope.taint_flags
    assert envelope.trust_level == "untrusted"
    assert {edge.source_kind for edge in envelope.field_lineage} == {
        LEGACY_SUMMARY_SOURCE_KIND,
        CONVERSATION_SEGMENT_SOURCE_KIND,
    }


def test_private_auto_compact_envelope_does_not_destabilize_wire_prompt_hash() -> None:
    messages = [
        {"id": f"msg_{index}", "role": "user", "content": f"message {index} " + ("x" * 900)} for index in range(10)
    ]
    settings = _settings()
    settings.context_auto_compact_enabled = True
    settings.model_auto_compact_token_limit = 1
    first = project_messages_for_llm(messages, settings, record_projection_event=False)
    second = project_messages_for_llm(messages, settings, record_projection_event=False)

    first_snapshot = build_llm_request_snapshot(
        first,
        settings,
        task="summary_hash",
        purpose="chat",
        provider="mock",
        model="mock",
    )
    second_snapshot = build_llm_request_snapshot(
        second,
        settings,
        task="summary_hash",
        purpose="chat",
        provider="mock",
        model="mock",
    )

    assert first_snapshot["prompt_hash"] == second_snapshot["prompt_hash"]
    assert first_snapshot["snapshot_id"] == second_snapshot["snapshot_id"]


def test_completion_summary_binds_latest_eighty_messages_and_latest_anchor() -> None:
    bus = AgentBus()
    task = Task(id="task_completion_lineage", user_goal="summarize", status=TaskStatus.COMPLETED)
    for index in range(82):
        bus.publish(
            AgentMessage(
                id=f"msg_{index:03}",
                task_id=task.id,
                from_agent="PlannerAgent",
                message_type=MessageType.OBSERVATION,
                content=f"message {index}",
                created_at=f"2026-01-01T00:{index // 60:02}:{index % 60:02}+00:00",
            )
        )
    store = SessionContextStore(session_id="session_completion_lineage")

    CompletionHandler(SimpleNamespace(bus=bus, session_context_store=store))._update_session_summary(task)

    reloaded = SessionContextStore(session_id="session_completion_lineage").load()
    source_ids = reloaded.token_stats["summary_source_message_ids"]
    assert source_ids == [f"msg_{index:03}" for index in range(2, 82)]
    assert reloaded.last_summarized_message_id == "msg_081"
    assert reloaded.conversation_summary_envelope is not None
    assert reloaded.lineage_diagnostics()["summary_source_message_count"] == 80
    assert (
        validate_summary_content_envelope(
            reloaded.conversation_summary,
            reloaded.conversation_summary_envelope,
            session_id=reloaded.id,
            last_message_id="msg_081",
            source_message_ids=source_ids,
        )
        == reloaded.conversation_summary_envelope
    )


def test_completion_merges_existing_authenticated_summary_and_parent_lineage() -> None:
    bus = AgentBus()
    store = SessionContextStore(session_id="session_completion_merge")
    store.load()
    prior_messages = [{"id": "msg_prior", "role": "assistant", "content": "prior"}]
    prior_summary = "Prior session decision."
    prior_envelope = build_summary_content_envelope(
        prior_summary,
        prior_messages,
        session_id=store.current.id,
        last_message_id="msg_prior",
        source_message_ids=["msg_prior"],
    )
    store.remember_summary(
        prior_summary,
        last_message_id="msg_prior",
        summary_envelope=prior_envelope,
        token_stats={"summary_source_message_ids": ["msg_prior"]},
    )
    task = Task(id="task_completion_merge", user_goal="continue", status=TaskStatus.COMPLETED)
    bus.publish(
        AgentMessage(
            id="msg_new",
            task_id=task.id,
            from_agent="PlannerAgent",
            message_type=MessageType.OBSERVATION,
            content="New task result.",
        )
    )

    CompletionHandler(SimpleNamespace(bus=bus, session_context_store=store))._update_session_summary(task)

    reloaded = SessionContextStore(session_id=store.current.id).load()
    assert prior_summary in reloaded.conversation_summary
    assert "New task result." in reloaded.conversation_summary
    assert reloaded.conversation_summary_envelope is not None
    assert {edge.source_kind for edge in reloaded.conversation_summary_envelope.field_lineage} == {
        "conversation_summary",
        CONVERSATION_SEGMENT_SOURCE_KIND,
    }
    assert (
        validate_summary_content_envelope(
            reloaded.conversation_summary,
            reloaded.conversation_summary_envelope,
            session_id=reloaded.id,
            last_message_id="msg_new",
            source_message_ids=["msg_new"],
        )
        == reloaded.conversation_summary_envelope
    )


def test_completion_rebuilds_merge_after_summary_cas_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = AgentBus()
    store = SessionContextStore(session_id="session_completion_retry")
    task = Task(id="task_completion_retry", user_goal="retry", status=TaskStatus.COMPLETED)
    bus.publish(
        AgentMessage(
            id="msg_task",
            task_id=task.id,
            from_agent="PlannerAgent",
            message_type=MessageType.OBSERVATION,
            content="Task summary after conflict.",
        )
    )
    original_remember_summary = store.remember_summary
    injected = False

    def racing_remember_summary(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal injected
        if not injected:
            injected = True
            concurrent_store = SessionContextStore(session_id=store.current.id)
            concurrent_context = concurrent_store.load()
            concurrent_messages = [{"id": "msg_concurrent", "role": "assistant", "content": "concurrent"}]
            concurrent_summary = "Concurrent session update."
            concurrent_envelope = build_summary_content_envelope(
                concurrent_summary,
                concurrent_messages,
                session_id=concurrent_context.id,
                last_message_id="msg_concurrent",
                source_message_ids=["msg_concurrent"],
            )
            concurrent_store.remember_summary(
                concurrent_summary,
                last_message_id="msg_concurrent",
                summary_envelope=concurrent_envelope,
                expected_updated_at=concurrent_context.updated_at,
                token_stats={"summary_source_message_ids": ["msg_concurrent"]},
            )
        return original_remember_summary(*args, **kwargs)

    monkeypatch.setattr(store, "remember_summary", racing_remember_summary)

    CompletionHandler(SimpleNamespace(bus=bus, session_context_store=store))._update_session_summary(task)

    reloaded = SessionContextStore(session_id=store.current.id).load()
    assert injected is True
    assert "Concurrent session update." in reloaded.conversation_summary
    assert "Task summary after conflict." in reloaded.conversation_summary
    assert reloaded.last_summarized_message_id == "msg_task"


def test_completion_then_manual_compaction_replaces_canonical_message_id_segment() -> None:
    bus = AgentBus()
    store = SessionContextStore(session_id="session_completion_then_compact")
    task = Task(id="task_before_compact", user_goal="finish", status=TaskStatus.COMPLETED)
    bus.publish(
        AgentMessage(
            id="msg_completion",
            task_id=task.id,
            from_agent="PlannerAgent",
            message_type=MessageType.OBSERVATION,
            content="Task result before manual compaction.",
        )
    )
    CompletionHandler(SimpleNamespace(bus=bus, session_context_store=store))._update_session_summary(task)
    compact_messages = [
        {
            "id": f"msg_compact_{index}",
            "role": "user",
            "content": f"manual compact message {index} " + ("x" * 100),
        }
        for index in range(6)
    ]

    result = compact_session_context(compact_messages, _settings(), session_store=store)

    reloaded = SessionContextStore(session_id=store.current.id).load()
    expected_ids = result.compact_metadata["messages_to_summarize_ids"]
    assert reloaded.token_stats["summary_source_message_ids"] == expected_ids
    assert reloaded.conversation_summary_envelope is not None
    assert (
        validate_summary_content_envelope(
            reloaded.conversation_summary,
            reloaded.conversation_summary_envelope,
            session_id=reloaded.id,
            last_message_id="msg_compact_5",
            source_message_ids=expected_ids,
        )
        == reloaded.conversation_summary_envelope
    )


@pytest.mark.parametrize("tamper", ["summary", "anchor", "message_ids"])
def test_session_load_rejects_tampered_summary_anchor_or_message_ids(tamper: str) -> None:
    store = SessionContextStore(session_id="session_lineage")
    store.load()
    store.remember_summary(
        "Release note drafted.",
        last_message_id="msg_2",
        summary_envelope=_envelope(),
        token_stats={"summary_source_message_ids": ["msg_1", "msg_2"]},
    )
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM session_contexts WHERE id = ?", ("session_lineage",)).fetchone()
        payload = json.loads(row["data"])
        if tamper == "summary":
            payload["conversation_summary"] = "Tampered summary."
        elif tamper == "anchor":
            payload["last_summarized_message_id"] = "msg_other"
        else:
            payload["token_stats"]["summary_source_message_ids"] = ["msg_1", "msg_other"]
        conn.execute(
            "UPDATE session_contexts SET data = ? WHERE id = ?",
            (json.dumps(payload), "session_lineage"),
        )

    with pytest.raises(SummaryProvenanceError):
        SessionContextStore(session_id="session_lineage").load()


def test_new_summary_recovers_from_compatibility_sidecar_and_rejects_full_removal() -> None:
    store = SessionContextStore(session_id="session_lineage")
    store.load()
    stored = store.remember_summary(
        "Release note drafted.",
        last_message_id="msg_2",
        summary_envelope=_envelope(),
        token_stats={"summary_source_message_ids": ["msg_1", "msg_2"]},
    )
    payload = stored.model_dump(mode="json")
    payload.pop("conversation_summary_envelope")
    with db.connect() as conn:
        conn.execute(
            "UPDATE session_contexts SET data = ? WHERE id = ?",
            (json.dumps(payload), "session_lineage"),
        )

    recovered = SessionContextStore(session_id="session_lineage").load()
    assert recovered.conversation_summary_envelope is not None
    payload["token_stats"].pop("summary_content_envelope")
    with db.connect() as conn:
        conn.execute(
            "UPDATE session_contexts SET data = ? WHERE id = ?",
            (json.dumps(payload), "session_lineage"),
        )
    with pytest.raises(ValueError, match="missing after provenance migration"):
        SessionContextStore(session_id="session_lineage").load()


def test_legacy_session_summary_migrates_to_tainted_root_without_false_mapping() -> None:
    legacy = SessionContext(
        id="session_legacy",
        conversation_summary="Old summary without message provenance.",
        last_summarized_message_id="msg_old",
    )
    raw = legacy.model_dump(mode="json", exclude={"conversation_summary_envelope"})
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO session_contexts (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("session_legacy", json.dumps(raw), legacy.created_at, legacy.updated_at),
        )

    migrated = SessionContextStore(session_id="session_legacy").load()
    envelope = migrated.conversation_summary_envelope

    assert envelope is not None
    assert envelope.source_kind == LEGACY_SUMMARY_SOURCE_KIND
    assert envelope.field_lineage == []
    assert "legacy_summary_lineage_unavailable" in envelope.taint_flags
    assert migrated.lineage_diagnostics()["summary_provenance_status"] == "legacy_root"


def test_old_session_model_preserves_compatibility_sidecar_across_unrelated_save() -> None:
    class LegacySessionContext(BaseModel):
        model_config = ConfigDict(extra="ignore")

        id: str
        conversation_summary: str = ""
        last_summarized_message_id: str = ""
        token_stats: dict = Field(default_factory=dict)

    current = SessionContext(
        id="session_lineage",
        conversation_summary="Release note drafted.",
        last_summarized_message_id="msg_2",
        conversation_summary_envelope=_envelope(),
        token_stats={
            "summary_provenance_version": SUMMARY_PROVENANCE_VERSION,
            "summary_source_message_ids": ["msg_1", "msg_2"],
        },
    )
    current.ensure_summary_provenance()
    old = LegacySessionContext.model_validate(current.model_dump(mode="json"))
    assert old.conversation_summary == current.conversation_summary

    rewritten_by_old_binary = SessionContext.model_validate(old.model_dump(mode="json"))
    restored = rewritten_by_old_binary.ensure_summary_provenance()
    assert restored is not None
    assert restored.source_kind == "conversation_summary"

    old.conversation_summary = "Old binary rewrote the summary."
    stale_rewrite = SessionContext.model_validate(old.model_dump(mode="json"))
    with pytest.raises(SummaryProvenanceError, match="does not match"):
        stale_rewrite.ensure_summary_provenance()
