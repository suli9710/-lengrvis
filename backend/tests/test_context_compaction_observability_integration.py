from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.config import AppSettings
from app.context import compaction as compaction_module
from app.context import management as management
from app.context.compaction import compact_session_context, compact_task_context, manual_compact_messages
from app.context.management import (
    ContextAwareProvider,
    PromptTooLongError,
    force_compact_for_retry,
    project_messages_for_llm,
    provider_safe_projection_fallback,
)
from app.llm.base import LLMProvider
from app.observability import metrics


@pytest.fixture(autouse=True)
def _reset_metrics_registry() -> Iterator[None]:
    metrics.reset()
    yield
    metrics.reset()


def _settings(**overrides: Any) -> AppSettings:
    settings = AppSettings(
        model_context_window=2000,
        model_auto_compact_token_limit=100_000,
        max_tokens=200,
        context_recent_message_limit=4,
        context_history_snip_enabled=False,
        context_history_snip_threshold=1000,
        context_history_snip_keep_recent=6,
        context_micro_compact_enabled=False,
        context_auto_compact_enabled=False,
        context_session_memory_enabled=False,
        context_session_summary_limit=1000,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _messages(*, count: int = 8, chars: int = 900) -> list[dict[str, Any]]:
    return [
        {"id": "system_1", "role": "system", "content": "Keep answers concise."},
        *[
            {
                "id": f"msg_{index}",
                "role": "user" if index % 2 else "assistant",
                "content": f"message {index} " + ("x" * chars),
            }
            for index in range(count)
        ],
    ]


def _decision_entries() -> list[dict[str, Any]]:
    return [entry for entry in metrics.snapshot()["counters"] if entry["name"] == "context_compaction_decisions_total"]


def _labels(entry: dict[str, Any]) -> dict[str, str]:
    labels = entry["labels"]
    assert isinstance(labels, dict)
    assert set(labels) == {"trigger", "strategy", "outcome"}
    return labels


def _only_decision() -> dict[str, str]:
    entries = _decision_entries()
    assert len(entries) == 1
    assert entries[0]["value"] == 1.0
    return _labels(entries[0])


class _FakeSessionStore:
    def __init__(self) -> None:
        self.current = SimpleNamespace(
            id="session_observability_integration",
            updated_at="version-1",
            conversation_summary_envelope=None,
        )
        self.summary = ""
        self.last_message_id = ""

    def load(self, session_id: str) -> SimpleNamespace:
        assert session_id == self.current.id
        return self.current

    def planning_context(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "session_id": self.current.id,
            "conversation_summary": self.summary,
            "last_summarized_message_id": self.last_message_id,
        }

    def remember_summary(self, summary: str, **kwargs: Any) -> SimpleNamespace:
        self.summary = summary
        self.last_message_id = str(kwargs.get("last_message_id") or "")
        self.current.updated_at = "version-2"
        return self.current


class _FailingTwiceStructuredProvider(LLMProvider):
    name = "structured_observability_integration"

    def __init__(self) -> None:
        self.structured_calls = 0

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        del messages, model, temperature, tools
        return "unused"

    async def structured_chat(
        self,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        del messages, output_schema
        self.structured_calls += 1
        if self.structured_calls <= 2:
            raise PromptTooLongError("private prompt detail must not become a metric label")
        return {"ok": True}


def test_real_projection_without_compaction_records_none_not_needed() -> None:
    projection = project_messages_for_llm(
        [{"id": "message_1", "role": "user", "content": "hello"}],
        _settings(),
        source="dynamic-source-must-not-be-a-label",
    )

    assert projection.compacted is False
    assert _only_decision() == {
        "trigger": "projection",
        "strategy": "none",
        "outcome": "not_needed",
    }


def test_projection_observability_disabled_suppresses_success_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [{"id": "message_1", "role": "user", "content": "hello"}]

    projection = project_messages_for_llm(
        messages,
        _settings(),
        source="context_usage",
        record_projection_event=False,
    )
    assert projection.compacted is False
    assert _decision_entries() == []

    original_error = RuntimeError("private projection failure")

    def fail_normalization(_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise original_error

    monkeypatch.setattr(management, "_normalize_messages", fail_normalization)

    with pytest.raises(RuntimeError) as exc_info:
        project_messages_for_llm(
            messages,
            _settings(),
            source="context_usage",
            record_projection_event=False,
        )

    assert exc_info.value is original_error
    assert _decision_entries() == []


def test_session_summary_only_projection_is_not_counted_as_compaction() -> None:
    projection = project_messages_for_llm(
        [{"id": "message_1", "role": "user", "content": "continue"}],
        _settings(context_session_memory_enabled=True),
        session_context={
            "session_id": "private-session-id",
            "conversation_summary": "Earlier work remains in progress.",
            "last_summarized_message_id": "private-message-id",
        },
        source="private-session-source",
    )

    assert projection.session_summary_added is True
    assert projection.compacted is True
    assert projection.projected_tokens > projection.original_tokens
    assert _only_decision() == {
        "trigger": "projection",
        "strategy": "none",
        "outcome": "not_needed",
    }


def test_structural_only_auto_candidate_is_ineffective_but_still_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def structural_only_candidate(
        messages: list[dict[str, Any]],
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[list[dict[str, Any]], bool]:
        candidate = [dict(message) for message in messages]
        candidate[0] = {**candidate[0], "metadata": {"structural_only": True}}
        return candidate, False

    monkeypatch.setattr(management, "auto_compact_messages", structural_only_candidate)
    projection = project_messages_for_llm(
        [{"id": "message_1", "role": "user", "content": "large enough"}],
        _settings(context_auto_compact_enabled=True, model_auto_compact_token_limit=1),
        source="projection-structural-only",
    )

    assert projection.compacted is True
    assert projection.messages[0]["metadata"] == {"structural_only": True}
    assert projection.projected_tokens == projection.original_tokens
    assert _only_decision() == {
        "trigger": "projection",
        "strategy": "auto_summary",
        "outcome": "ineffective",
    }


@pytest.mark.parametrize("entrypoint", ["manual", "session", "task"])
def test_manual_session_and_task_nesting_records_one_transformation(
    entrypoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _messages()
    settings = _settings(context_recent_message_limit=2)
    store = _FakeSessionStore()

    if entrypoint == "manual":
        result = manual_compact_messages(messages, settings, recent_message_limit=2)
    elif entrypoint == "session":
        result = compact_session_context(
            messages,
            settings,
            recent_message_limit=2,
            session_store=store,  # type: ignore[arg-type]
        )
    else:

        def load_messages(_task_id: str, *, bus: object | None = None) -> list[dict[str, Any]]:
            del bus
            return [dict(message) for message in messages]

        monkeypatch.setattr(compaction_module, "load_task_messages", load_messages)
        result = compact_task_context(
            "private-task-id",
            settings,
            recent_message_limit=2,
            session_store=store,  # type: ignore[arg-type]
            persist_session_context=True,
            persist_agent_boundary=False,
        )

    assert result.compacted_messages > 0
    assert result.post_compact_tokens < result.pre_compact_tokens
    assert _only_decision() == {
        "trigger": "manual",
        "strategy": "manual_summary",
        "outcome": "applied",
    }


def test_force_and_fallback_each_record_one_provider_limit_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(management, "_load_session_context", lambda: None)
    settings = _settings(
        model_context_window=400,
        max_tokens=40,
        context_manual_compact_buffer_tokens=100,
        context_recent_message_limit=4,
    )
    messages = [{"id": f"message_{index}", "role": "user", "content": "payload " * 100} for index in range(6)]

    reactive = force_compact_for_retry(messages, settings)
    fallback = provider_safe_projection_fallback(messages, settings)

    assert reactive.projected_tokens < reactive.original_tokens
    assert fallback.projected_tokens < fallback.original_tokens
    entries = _decision_entries()
    assert len(entries) == 2
    assert all(entry["value"] == 1.0 for entry in entries)
    assert {_labels(entry)["strategy"] for entry in entries} == {
        "reactive_summary",
        "fallback_trim",
    }
    assert all(_labels(entry)["trigger"] == "provider_limit" for entry in entries)
    assert all(_labels(entry)["outcome"] == "applied" for entry in entries)


def test_fallback_target_unmet_is_ineffective() -> None:
    settings = _settings(
        model_context_window=100,
        max_tokens=10,
        context_manual_compact_buffer_tokens=89,
    )
    projection = provider_safe_projection_fallback(
        [
            {"id": "system", "role": "system", "content": "policy " * 200},
            {"id": "latest", "role": "user", "content": "request " * 200},
        ],
        settings,
    )

    assert projection.compact_metadata is not None
    assert projection.projected_tokens > projection.compact_metadata["target_tokens"]
    assert _only_decision() == {
        "trigger": "provider_limit",
        "strategy": "fallback_trim",
        "outcome": "ineffective",
    }


def test_structured_prompt_limit_reactive_and_fallback_decisions_have_fixed_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = r"C:\Users\alice\private\task-and-prompt-token-938421"
    provider = _FailingTwiceStructuredProvider()
    settings = _settings(
        model_context_window=400,
        max_tokens=40,
        context_manual_compact_buffer_tokens=100,
        context_recent_message_limit=4,
    )
    monkeypatch.setattr(management, "_load_session_context", lambda: None)
    monkeypatch.setattr(management, "_record_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(management, "record_llm_response", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(management, "_safe_context_usage_snapshot", lambda *_args, **_kwargs: {})
    wrapped = ContextAwareProvider(provider, settings, task=secret)

    payload = asyncio.run(
        wrapped.structured_chat(
            [{"id": f"message_{index}", "role": "user", "content": f"{secret} " + ("x" * 800)} for index in range(6)],
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )
    )

    assert payload == {"ok": True}
    assert provider.structured_calls == 3
    entries = _decision_entries()
    provider_limit_entries = [entry for entry in entries if _labels(entry)["trigger"] == "provider_limit"]
    assert len(provider_limit_entries) == 2
    assert all(entry["value"] == 1.0 for entry in provider_limit_entries)
    assert {_labels(entry)["strategy"] for entry in provider_limit_entries} == {
        "reactive_summary",
        "fallback_trim",
    }

    from app.context import compaction_observability as observed

    for entry in entries:
        labels = _labels(entry)
        assert labels["trigger"] in observed.CONTEXT_COMPACTION_TRIGGERS
        assert labels["strategy"] in observed.CONTEXT_COMPACTION_STRATEGIES
        assert labels["outcome"] in observed.CONTEXT_COMPACTION_OUTCOMES
    snapshot_text = str(metrics.snapshot())
    prometheus_text = metrics.render_prometheus()
    assert secret not in snapshot_text
    assert secret not in prometheus_text
    assert "task-and-prompt-token-938421" not in snapshot_text
    assert "task-and-prompt-token-938421" not in prometheus_text
