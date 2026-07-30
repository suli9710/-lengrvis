from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.context import compaction_observability as observed
from app.observability import metrics


@pytest.fixture(autouse=True)
def _reset_metrics_registry():
    metrics.reset()
    yield
    metrics.reset()


def _counter_entries() -> list[dict[str, Any]]:
    return [entry for entry in metrics.snapshot()["counters"] if entry["name"] == "context_compaction_decisions_total"]


def _only_labels() -> dict[str, str]:
    entries = _counter_entries()
    assert len(entries) == 1
    assert entries[0]["value"] == 1.0
    labels = entries[0]["labels"]
    assert isinstance(labels, dict)
    assert set(labels) == {"trigger", "strategy", "outcome"}
    return labels


def _projection(*, before: object = 100, after: object = 40, **extra: object) -> SimpleNamespace:
    return SimpleNamespace(
        original_tokens=before,
        projected_tokens=after,
        **extra,
    )


def _manual(
    *,
    compacted_messages: object = 3,
    before: object = 100,
    after: object = 40,
    **extra: object,
) -> SimpleNamespace:
    return SimpleNamespace(
        compacted_messages=compacted_messages,
        pre_compact_tokens=before,
        post_compact_tokens=after,
        **extra,
    )


def test_metric_contract_has_three_fixed_labels_and_180_series_cap() -> None:
    assert observed.CONTEXT_COMPACTION_TRIGGERS == frozenset({"projection", "provider_limit", "manual", "other"})
    assert observed.CONTEXT_COMPACTION_STRATEGIES == frozenset(
        {
            "none",
            "micro",
            "history_snip",
            "auto_summary",
            "mixed",
            "reactive_summary",
            "fallback_trim",
            "manual_summary",
            "other",
        }
    )
    assert observed.CONTEXT_COMPACTION_OUTCOMES == frozenset(
        {"not_needed", "applied", "ineffective", "error", "invalid_result"}
    )
    assert observed.MAX_CONTEXT_COMPACTION_METRIC_SERIES == 180
    assert observed.MAX_CONTEXT_COMPACTION_METRIC_SERIES == (
        len(observed.CONTEXT_COMPACTION_TRIGGERS)
        * len(observed.CONTEXT_COMPACTION_STRATEGIES)
        * len(observed.CONTEXT_COMPACTION_OUTCOMES)
    )

    observed.record_projection_result(
        _projection(),
        enabled=True,
        micro_applied=True,
        history_snip_applied=False,
        auto_attempted=False,
        auto_applied=False,
    )

    assert _only_labels() == {
        "trigger": "projection",
        "strategy": "micro",
        "outcome": "applied",
    }


def test_exhaustive_vocabulary_reaches_but_cannot_exceed_series_cap() -> None:
    for trigger in observed.CONTEXT_COMPACTION_TRIGGERS:
        for strategy in observed.CONTEXT_COMPACTION_STRATEGIES:
            for outcome in observed.CONTEXT_COMPACTION_OUTCOMES:
                observed._increment(trigger, strategy, outcome)

    assert len(_counter_entries()) == observed.MAX_CONTEXT_COMPACTION_METRIC_SERIES

    secret = "task-secret-provider-model-prompt"
    observed._increment(secret, secret, secret)

    assert len(_counter_entries()) == observed.MAX_CONTEXT_COMPACTION_METRIC_SERIES
    folded = next(
        entry
        for entry in _counter_entries()
        if entry["labels"] == {"trigger": "other", "strategy": "other", "outcome": "invalid_result"}
    )
    assert folded["value"] == 2.0


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (
            {
                "micro_applied": False,
                "history_snip_applied": False,
                "auto_attempted": False,
                "auto_applied": False,
            },
            {"trigger": "projection", "strategy": "none", "outcome": "not_needed"},
        ),
        (
            {
                "micro_applied": True,
                "history_snip_applied": False,
                "auto_attempted": False,
                "auto_applied": False,
            },
            {"trigger": "projection", "strategy": "micro", "outcome": "applied"},
        ),
        (
            {
                "micro_applied": False,
                "history_snip_applied": True,
                "auto_attempted": False,
                "auto_applied": False,
            },
            {"trigger": "projection", "strategy": "history_snip", "outcome": "applied"},
        ),
        (
            {
                "micro_applied": False,
                "history_snip_applied": False,
                "auto_attempted": True,
                "auto_applied": True,
            },
            {"trigger": "projection", "strategy": "auto_summary", "outcome": "applied"},
        ),
    ],
)
def test_projection_result_uses_structured_strategy_flags(
    flags: dict[str, bool],
    expected: dict[str, str],
) -> None:
    result = _projection()

    returned = observed.record_projection_result(result, enabled=True, **flags)

    assert returned is result
    assert _only_labels() == expected


def test_projection_session_only_is_not_misreported_as_compaction() -> None:
    result = _projection(
        before=100,
        after=130,
        compacted=True,
        session_summary_added=True,
        strategy="session",
        source="private-session-id",
    )

    returned = observed.record_projection_result(
        result,
        enabled=True,
        micro_applied=False,
        history_snip_applied=False,
        auto_attempted=False,
        auto_applied=False,
    )

    assert returned is result
    assert _only_labels() == {
        "trigger": "projection",
        "strategy": "none",
        "outcome": "not_needed",
    }


def test_projection_compaction_effect_is_not_hidden_by_session_injection_growth() -> None:
    result = _projection(before=100, after=130, session_summary_added=True)

    observed.record_projection_result(
        result,
        enabled=True,
        micro_applied=True,
        history_snip_applied=False,
        auto_attempted=False,
        auto_applied=False,
        token_reduced=True,
    )

    assert _only_labels() == {
        "trigger": "projection",
        "strategy": "micro",
        "outcome": "applied",
    }


def test_projection_combined_compaction_is_classified_as_mixed() -> None:
    result = _projection(before=240, after=60)

    observed.record_projection_result(
        result,
        enabled=True,
        micro_applied=True,
        history_snip_applied=True,
        auto_attempted=True,
        auto_applied=True,
    )

    assert _only_labels() == {
        "trigger": "projection",
        "strategy": "mixed",
        "outcome": "applied",
    }


def test_projection_auto_attempt_without_reduction_is_ineffective() -> None:
    result = _projection(before=100, after=100)

    observed.record_projection_result(
        result,
        enabled=True,
        micro_applied=False,
        history_snip_applied=False,
        auto_attempted=True,
        auto_applied=False,
    )

    assert _only_labels() == {
        "trigger": "projection",
        "strategy": "auto_summary",
        "outcome": "ineffective",
    }


def test_disabled_projection_has_no_operational_side_effect() -> None:
    result = _projection()

    returned = observed.record_projection_result(
        result,
        enabled=False,
        micro_applied=True,
        history_snip_applied=True,
        auto_attempted=True,
        auto_applied=True,
    )

    assert returned is result
    assert _counter_entries() == []


@pytest.mark.parametrize(
    ("result", "outcome"),
    [
        (_manual(compacted_messages=0), "not_needed"),
        (_manual(compacted_messages=3, before=100, after=40), "applied"),
        (_manual(compacted_messages=3, before=100, after=100), "ineffective"),
        (_manual(compacted_messages=3, before=100, after=120), "ineffective"),
    ],
)
def test_manual_result_classification(result: SimpleNamespace, outcome: str) -> None:
    returned = observed.record_manual_result(result)

    assert returned is result
    assert _only_labels() == {
        "trigger": "manual",
        "strategy": "manual_summary",
        "outcome": outcome,
    }


@pytest.mark.parametrize(
    ("strategy", "result", "outcome"),
    [
        ("reactive_summary", _projection(before=100, after=40), "applied"),
        ("reactive_summary", _projection(before=100, after=100), "ineffective"),
        (
            "fallback_trim",
            _projection(before=100, after=40, compact_metadata={"target_tokens": 40}),
            "applied",
        ),
        (
            "fallback_trim",
            _projection(before=100, after=40, compact_metadata={"target_tokens": 30}),
            "ineffective",
        ),
        ("fallback_trim", _projection(before=100, after=40, compact_metadata={}), "invalid_result"),
    ],
)
def test_reactive_result_classification(
    strategy: str,
    result: SimpleNamespace,
    outcome: str,
) -> None:
    returned = observed.record_reactive_result(result, strategy=strategy)

    assert returned is result
    assert _only_labels() == {
        "trigger": "provider_limit",
        "strategy": strategy,
        "outcome": outcome,
    }


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            lambda: observed.record_projection_result(
                _projection(before=True, after=0),
                enabled=True,
                micro_applied=True,
                history_snip_applied=False,
                auto_attempted=False,
                auto_applied=False,
            ),
            {"trigger": "projection", "strategy": "micro", "outcome": "invalid_result"},
        ),
        (
            lambda: observed.record_projection_result(
                _projection(),
                enabled=True,
                micro_applied=1,  # type: ignore[arg-type]
                history_snip_applied=False,
                auto_attempted=False,
                auto_applied=False,
            ),
            {"trigger": "projection", "strategy": "other", "outcome": "invalid_result"},
        ),
        (
            lambda: observed.record_projection_result(
                _projection(),
                enabled=True,
                micro_applied=False,
                history_snip_applied=False,
                auto_attempted=False,
                auto_applied=True,
            ),
            {"trigger": "projection", "strategy": "other", "outcome": "invalid_result"},
        ),
        (
            lambda: observed.record_manual_result(_manual(compacted_messages=True)),
            {"trigger": "manual", "strategy": "manual_summary", "outcome": "invalid_result"},
        ),
        (
            lambda: observed.record_manual_result(_manual(before=-1)),
            {"trigger": "manual", "strategy": "manual_summary", "outcome": "invalid_result"},
        ),
        (
            lambda: observed.record_reactive_result(
                _projection(before=100, after=False),
                strategy="reactive_summary",
            ),
            {"trigger": "provider_limit", "strategy": "reactive_summary", "outcome": "invalid_result"},
        ),
    ],
)
def test_bool_and_invalid_token_or_flag_values_fail_closed(
    record: Callable[[], object],
    expected: dict[str, str],
) -> None:
    record()

    assert _only_labels() == expected


def test_unknown_values_fold_to_other_without_leaking_raw_text() -> None:
    secret = "C:\\Users\\alice\\private\\prompt-token-938421"
    result = _projection(
        before=100,
        after=40,
        error=secret,
        task_id=secret,
        source=secret,
        prompt=secret,
    )

    returned = observed.record_reactive_result(result, strategy=secret)
    observed._increment(secret, secret, secret)

    assert returned is result
    entries = _counter_entries()
    assert len(entries) == 2
    assert all(set(entry["labels"]) == {"trigger", "strategy", "outcome"} for entry in entries)
    assert entries[0]["labels"] == {
        "trigger": "provider_limit",
        "strategy": "other",
        "outcome": "applied",
    }
    assert entries[1]["labels"] == {
        "trigger": "other",
        "strategy": "other",
        "outcome": "invalid_result",
    }
    snapshot_text = str(metrics.snapshot())
    prometheus_text = metrics.render_prometheus()
    assert secret not in snapshot_text
    assert secret not in prometheus_text
    assert "prompt-token-938421" not in snapshot_text
    assert "prompt-token-938421" not in prometheus_text


@pytest.mark.parametrize(
    ("decorate", "expected"),
    [
        (
            observed.observe_projection_failures,
            {"trigger": "projection", "strategy": "other", "outcome": "error"},
        ),
        (
            observed.observe_manual_compaction,
            {"trigger": "manual", "strategy": "manual_summary", "outcome": "error"},
        ),
        (
            observed.observe_reactive_compaction("fallback_trim"),
            {"trigger": "provider_limit", "strategy": "fallback_trim", "outcome": "error"},
        ),
    ],
)
def test_decorators_preserve_the_original_error_object(
    decorate: Callable[[Callable[[], object]], Callable[[], object]],
    expected: dict[str, str],
) -> None:
    original = RuntimeError("private compaction failure")

    @decorate
    def fail() -> object:
        raise original

    with pytest.raises(RuntimeError) as exc_info:
        fail()

    assert exc_info.value is original
    assert _only_labels() == expected


def test_metrics_and_recovery_logger_double_failure_do_not_change_results_or_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter_error = RuntimeError("private metrics backend detail")
    logger_error = RuntimeError("private logger backend detail")

    def fail_counter(*args: object, **kwargs: object) -> None:  # noqa: ARG001
        raise counter_error

    def fail_logger(*args: object, **kwargs: object) -> None:  # noqa: ARG001
        raise logger_error

    monkeypatch.setattr(observed.metrics, "increment_counter", fail_counter)
    monkeypatch.setattr(observed, "log_best_effort_failure", fail_logger)

    result = _projection(before=100, after=40)
    returned = observed.record_projection_result(
        result,
        enabled=True,
        micro_applied=True,
        history_snip_applied=False,
        auto_attempted=False,
        auto_applied=False,
    )
    assert returned is result

    original = ValueError("private original compaction error")

    @observed.observe_manual_compaction
    def fail() -> object:
        raise original

    with pytest.raises(ValueError) as exc_info:
        fail()

    assert exc_info.value is original
    assert _counter_entries() == []
