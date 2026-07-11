from __future__ import annotations

import pytest

from app.automation.models import BudgetConsumeRequest, RunBudgetLimits
from app.automation.run_budget import (
    consume_run_budget,
    consume_run_budget_events,
    create_run_budget,
    get_run_budget,
    tighten_run_budget,
)


def test_run_budget_soft_threshold_pauses_without_hard_stop() -> None:
    ledger = create_run_budget(
        "run-soft-hard",
        limits=RunBudgetLimits(max_tool_calls=5, max_duplicate_actions=10),
    )
    assert ledger.status == "active"

    for _ in range(3):
        decision = consume_run_budget("run-soft-hard", BudgetConsumeRequest(kind="tool_call"))
        assert decision.allowed is True
        assert decision.soft_exceeded is False

    decision = consume_run_budget("run-soft-hard", BudgetConsumeRequest(kind="tool_call"))
    assert decision.allowed is False
    assert decision.soft_exceeded is True
    assert decision.hard_exceeded is False
    assert decision.ledger.status == "soft_exceeded"

    paused_version = decision.ledger.version
    decision = consume_run_budget("run-soft-hard", BudgetConsumeRequest(kind="tool_call"))
    assert decision.allowed is False
    assert decision.soft_exceeded is True
    assert decision.ledger.version == paused_version
    assert decision.ledger.usage.tool_calls == 4


def test_run_budget_hard_stops_when_one_reservation_exceeds_limit() -> None:
    create_run_budget(
        "run-hard",
        limits=RunBudgetLimits(max_tool_calls=5, max_duplicate_actions=10),
    )

    decision = consume_run_budget("run-hard", BudgetConsumeRequest(kind="tool_call", amount=6))

    assert decision.allowed is False
    assert decision.hard_exceeded is True
    assert decision.soft_exceeded is False
    assert decision.ledger.status == "hard_stopped"
    assert "tool call" in decision.reason


def test_run_budget_stops_repeated_side_effects() -> None:
    create_run_budget(
        "run-duplicate",
        limits=RunBudgetLimits(),
    )
    event = BudgetConsumeRequest(kind="external_send", action_fingerprint="wechat:alice:hash-1")

    assert consume_run_budget("run-duplicate", event).allowed is True
    decision = consume_run_budget("run-duplicate", event)

    assert decision.allowed is False
    assert "duplicate-action" in decision.reason


def test_run_budget_persists_only_digests_for_sensitive_destinations() -> None:
    create_run_budget("run-sensitive-destinations")

    decision = consume_run_budget(
        "run-sensitive-destinations",
        BudgetConsumeRequest(
            kind="external_send",
            recipient="alice@example.com",
            domain="private.example.com",
            action_fingerprint="wechat:alice:salary-report",
        ),
    )

    serialized = decision.model_dump_json()
    assert "alice@example.com" not in serialized
    assert "private.example.com" not in serialized
    assert "wechat:alice:salary-report" not in serialized
    assert decision.ledger.usage.recipients[0].startswith("sha256:")
    assert decision.ledger.usage.domains[0].startswith("sha256:")
    assert next(iter(decision.ledger.usage.duplicate_actions)).startswith("sha256:")


def test_run_budget_cannot_be_expanded_after_creation() -> None:
    create_run_budget("run-tighten", limits=RunBudgetLimits(max_writes=5, max_tool_calls=20))

    tightened = tighten_run_budget(
        "run-tighten",
        RunBudgetLimits(max_writes=2, max_tool_calls=10),
    )
    assert tightened.limits.max_writes == 2

    with pytest.raises(ValueError, match="cannot be expanded"):
        tighten_run_budget(
            "run-tighten",
            RunBudgetLimits(max_writes=3, max_tool_calls=10),
        )

    assert get_run_budget("run-tighten").limits.max_writes == 2  # type: ignore[union-attr]


def test_zero_subprocess_budget_fails_closed() -> None:
    create_run_budget("run-no-process", limits=RunBudgetLimits(max_subprocesses=0))

    decision = consume_run_budget("run-no-process", BudgetConsumeRequest(kind="subprocess"))

    assert decision.allowed is False
    assert "subprocess" in decision.reason


def test_batch_consumption_is_atomic_and_binds_all_targets() -> None:
    create_run_budget(
        "run-batch",
        limits=RunBudgetLimits(max_writes=0, max_recipients=3, max_domains=3),
    )

    decision = consume_run_budget_events(
        "run-batch",
        [
            BudgetConsumeRequest(kind="tool_call", action_fingerprint="action-1"),
            BudgetConsumeRequest(kind="write"),
        ],
        recipients=["alice@example.test", "bob@example.test"],
        domains=["forms.example.test", "example.test"],
    )

    assert decision.allowed is False
    assert decision.ledger.version == 2
    assert decision.ledger.usage.tool_calls == 1
    assert decision.ledger.usage.writes == 1
    assert len(decision.ledger.usage.recipients) == 2
    assert len(decision.ledger.usage.domains) == 2
