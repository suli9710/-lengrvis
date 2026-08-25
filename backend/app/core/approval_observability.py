"""Privacy-safe, bounded-cardinality approval transition metrics.

The atomic decision and execution-claim functions are the feature seams.  Keep
their taxonomy here so desktop, mobile, UI automation, browser, rollback, and
orchestration callers cannot attach approval ids, task ids, tool names,
authentication evidence, resource state, or error text to metric labels.

These helpers are deliberately best effort: removing this module and its two
call sites removes only operational signals, never approval behavior.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Literal, ParamSpec, TypeVar, cast

from app.observability import metrics
from app.observability.best_effort import log_best_effort_failure

logger = logging.getLogger(__name__)

ApprovalDecision = Literal["approved", "rejected", "other"]
ApprovalDecisionOutcome = Literal["applied", "expired", "unavailable", "error", "invalid_result"]
ApprovalClaimOutcome = Literal[
    "claimed",
    "already_consumed",
    "expired",
    "authorization_invalidated",
    "unavailable",
    "conflict",
    "error",
    "invalid_result",
]

APPROVAL_DECISIONS = frozenset({"approved", "rejected", "other"})
APPROVAL_DECISION_OUTCOMES = frozenset(
    {
        "applied",
        "expired",
        "unavailable",
        "error",
        "invalid_result",
    }
)
APPROVAL_CLAIM_OUTCOMES = frozenset(
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
MAX_APPROVAL_METRIC_SERIES = len(APPROVAL_DECISIONS) * len(APPROVAL_DECISION_OUTCOMES) + len(APPROVAL_CLAIM_OUTCOMES)

_P = ParamSpec("_P")
_R = TypeVar("_R")
_T = TypeVar("_T")


def observe_atomic_decision(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Observe one post-transaction decision result while preserving its API."""

    @wraps(func)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        decision = kwargs.get("status")
        if decision is None and len(args) > 1:
            decision = args[1]
        try:
            result = func(*args, **kwargs)
        except Exception:  # noqa: BLE001 - broad-exception-boundary: preserve the original decision failure.
            _increment_decision(decision, "error")
            raise
        return record_decision_result(decision, result)

    return cast(Callable[_P, _R], wrapped)


def record_decision_result(decision: object, result: _T) -> _T:
    """Record one committed atomic decision attempt and return it unchanged."""

    try:
        normalized_decision = _normalize(decision, APPROVAL_DECISIONS, fallback="other")
        if result is None:
            outcome: ApprovalDecisionOutcome = "unavailable"
        elif not isinstance(result, dict):
            outcome = "invalid_result"
        else:
            stored_status = str(result.get("status") or "").strip().casefold()
            if stored_status == "expired":
                outcome = "expired"
            elif normalized_decision in {"approved", "rejected"} and stored_status == normalized_decision:
                outcome = "applied"
            else:
                outcome = "invalid_result"
        _increment_decision(normalized_decision, outcome)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: metrics cannot alter decisions.
        _log_failure("classify approval decision metric", exc)
    return result


def record_claim_outcome(outcome: ApprovalClaimOutcome | str) -> None:
    """Record one completed atomic execution-claim attempt."""

    try:
        normalized = _normalize(outcome, APPROVAL_CLAIM_OUTCOMES, fallback="invalid_result")
        metrics.increment_counter(
            "approval_claim_outcomes_total",
            labels={"outcome": normalized},
        )
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: metrics cannot alter claims.
        _log_failure("record approval claim metric", exc)


def _increment_decision(decision: object, outcome: ApprovalDecisionOutcome | str) -> None:
    labels = {
        "decision": _normalize(decision, APPROVAL_DECISIONS, fallback="other"),
        "outcome": _normalize(outcome, APPROVAL_DECISION_OUTCOMES, fallback="invalid_result"),
    }
    try:
        metrics.increment_counter(
            "approval_decision_outcomes_total",
            labels=labels,
        )
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: metrics cannot alter decisions.
        _log_failure("record approval decision metric", exc)


def _normalize(value: object, allowed: frozenset[str], *, fallback: str) -> str:
    try:
        normalized = str(value or "").strip().casefold()
    except Exception:  # noqa: BLE001 - broad-exception-boundary: hostile labels must collapse, never escape.
        return fallback
    return normalized if normalized in allowed else fallback


def _log_failure(operation: str, exc: BaseException) -> None:
    try:
        log_best_effort_failure(logger, operation, exc)
    except Exception:  # noqa: BLE001, S110 - broad-exception-boundary: recovery logging cannot alter behavior.
        pass
