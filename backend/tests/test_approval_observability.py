from __future__ import annotations

import pytest

from app.core import approval_observability as observed
from app.observability import metrics


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


def _counter_entries(name: str) -> list[dict[str, object]]:
    return [entry for entry in metrics.snapshot()["counters"] if entry["name"] == name]


@pytest.mark.parametrize(
    ("decision", "result", "expected"),
    [
        ("approved", {"status": "approved"}, ("approved", "applied")),
        ("rejected", {"status": "rejected"}, ("rejected", "applied")),
        ("approved", {"status": "expired"}, ("approved", "expired")),
        ("rejected", None, ("rejected", "unavailable")),
        ("approved", {"status": "pending"}, ("approved", "invalid_result")),
        ("approved", object(), ("approved", "invalid_result")),
    ],
)
def test_decision_result_has_a_closed_semantic_classification(
    decision: str,
    result: object,
    expected: tuple[str, str],
) -> None:
    assert observed.record_decision_result(decision, result) is result

    assert _counter_entries("approval_decision_outcomes_total") == [
        {
            "name": "approval_decision_outcomes_total",
            "labels": {"decision": expected[0], "outcome": expected[1]},
            "value": 1.0,
        }
    ]


def test_vocabulary_and_metric_series_upper_bound_are_hard_contracts() -> None:
    assert observed.APPROVAL_DECISIONS == frozenset({"approved", "rejected", "other"})
    assert observed.APPROVAL_DECISION_OUTCOMES == frozenset(
        {
            "applied",
            "expired",
            "unavailable",
            "error",
            "invalid_result",
        }
    )
    assert observed.APPROVAL_CLAIM_OUTCOMES == frozenset(
        {
            "claimed",
            "already_consumed",
            "expired",
            "authorization_invalidated",
            "unavailable",
            "conflict",
            "error",
            "invalid_result",
        }
    )
    assert observed.MAX_APPROVAL_METRIC_SERIES == 23


def test_unknown_values_collapse_without_leaking_caller_text() -> None:
    private_value = r"C:\private\approval-secret-0123456789"

    observed.record_decision_result(private_value, {"status": private_value})
    observed.record_claim_outcome(private_value)

    assert _counter_entries("approval_decision_outcomes_total")[0]["labels"] == {
        "decision": "other",
        "outcome": "invalid_result",
    }
    assert _counter_entries("approval_claim_outcomes_total")[0]["labels"] == {
        "outcome": "invalid_result",
    }
    assert private_value not in metrics.render_prometheus()
    assert "approval-secret-0123456789" not in metrics.render_prometheus()


def test_result_payload_never_becomes_metric_labels() -> None:
    payload = {
        "status": "approved",
        "id": "approval-sensitive-id",
        "task_id": "task-sensitive-id",
        "tool_name": "private.tool",
        "auth_context": {"confirmation_id": "confirmation-sensitive-id"},
        "expired_reason": r"C:\private\error.txt",
    }

    observed.record_decision_result("approved", payload)

    rendered = metrics.render_prometheus()
    assert "approval-sensitive-id" not in rendered
    assert "task-sensitive-id" not in rendered
    assert "private.tool" not in rendered
    assert "confirmation-sensitive-id" not in rendered
    assert "error.txt" not in rendered


def test_atomic_decision_decorator_records_one_post_call_result() -> None:
    calls: list[str] = []

    @observed.observe_atomic_decision
    def decide(approval_id: str, status: str) -> dict[str, str]:
        calls.append(approval_id)
        return {"status": status}

    result = decide("private-approval-id", status="rejected")

    assert result == {"status": "rejected"}
    assert calls == ["private-approval-id"]
    assert _counter_entries("approval_decision_outcomes_total") == [
        {
            "name": "approval_decision_outcomes_total",
            "labels": {"decision": "rejected", "outcome": "applied"},
            "value": 1.0,
        }
    ]
    assert "private-approval-id" not in metrics.render_prometheus()


def test_counter_and_recovery_logger_failure_do_not_change_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = {"status": "approved"}

    def fail_counter(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("metrics backend unavailable")

    def fail_recovery_log(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("logger unavailable")

    monkeypatch.setattr(observed.metrics, "increment_counter", fail_counter)
    monkeypatch.setattr(observed, "log_best_effort_failure", fail_recovery_log)

    assert observed.record_decision_result("approved", result) is result
    observed.record_claim_outcome("claimed")


def test_counter_and_recovery_logger_failure_preserve_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuntimeError("original approval storage failure")

    def fail_counter(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("metrics backend unavailable")

    def fail_recovery_log(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        raise RuntimeError("logger unavailable")

    @observed.observe_atomic_decision
    def decide(approval_id: str, status: str) -> dict[str, str]:  # noqa: ARG001
        raise original

    monkeypatch.setattr(observed.metrics, "increment_counter", fail_counter)
    monkeypatch.setattr(observed, "log_best_effort_failure", fail_recovery_log)

    with pytest.raises(RuntimeError) as exc_info:
        decide("private-approval-id", "approved")

    assert exc_info.value is original


def test_hostile_label_stringification_never_changes_result_or_original_exception() -> None:
    class HostileLabel:
        def __str__(self) -> str:
            raise RuntimeError("hostile label stringification")

    hostile = HostileLabel()
    result = {"status": "approved"}
    original = RuntimeError("original decision failure")

    assert observed.record_decision_result(hostile, result) is result
    observed.record_claim_outcome(hostile)  # type: ignore[arg-type]

    @observed.observe_atomic_decision
    def decide(approval_id: str, status: object) -> dict[str, str]:  # noqa: ARG001
        raise original

    with pytest.raises(RuntimeError) as exc_info:
        decide("private-approval-id", hostile)

    assert exc_info.value is original
    assert _counter_entries("approval_decision_outcomes_total") == [
        {
            "name": "approval_decision_outcomes_total",
            "labels": {"decision": "other", "outcome": "error"},
            "value": 1.0,
        },
        {
            "name": "approval_decision_outcomes_total",
            "labels": {"decision": "other", "outcome": "invalid_result"},
            "value": 1.0,
        },
    ]
    assert _counter_entries("approval_claim_outcomes_total")[0]["labels"] == {
        "outcome": "invalid_result",
    }
