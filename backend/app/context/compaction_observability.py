"""Privacy-safe, bounded-cardinality context-compaction decision metrics.

The compaction transform is the decision seam.  Keep its taxonomy here so
projection, manual compaction, and provider-limit recovery cannot attach task,
session, provider, prompt, message, or error text to metric labels.  The
decorators are deliberately best effort: deleting this module removes only
operational signals, never context behavior.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal, ParamSpec, TypeVar, cast

from app.observability import metrics
from app.observability.best_effort import log_best_effort_failure

logger = logging.getLogger(__name__)

CompactionTrigger = Literal["projection", "provider_limit", "manual", "other"]
CompactionStrategy = Literal[
    "none",
    "micro",
    "history_snip",
    "auto_summary",
    "mixed",
    "reactive_summary",
    "fallback_trim",
    "manual_summary",
    "other",
]
CompactionOutcome = Literal["not_needed", "applied", "ineffective", "error", "invalid_result"]
ReactiveStrategy = Literal["reactive_summary", "fallback_trim"]

CONTEXT_COMPACTION_TRIGGERS = frozenset({"projection", "provider_limit", "manual", "other"})
CONTEXT_COMPACTION_STRATEGIES = frozenset(
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
CONTEXT_COMPACTION_OUTCOMES = frozenset({"not_needed", "applied", "ineffective", "error", "invalid_result"})
MAX_CONTEXT_COMPACTION_METRIC_SERIES = (
    len(CONTEXT_COMPACTION_TRIGGERS) * len(CONTEXT_COMPACTION_STRATEGIES) * len(CONTEXT_COMPACTION_OUTCOMES)
)

_P = ParamSpec("_P")
_R = TypeVar("_R")
_T = TypeVar("_T")


def record_projection_result(
    result: _T,
    *,
    enabled: bool,
    micro_applied: bool,
    history_snip_applied: bool,
    auto_attempted: bool,
    auto_applied: bool,
    token_reduced: bool | None = None,
) -> _T:
    """Record one completed production projection and return it unchanged."""

    if enabled is False:
        return result
    try:
        flags = (enabled, micro_applied, history_snip_applied, auto_attempted, auto_applied)
        if (
            any(type(value) is not bool for value in flags)
            or (token_reduced is not None and type(token_reduced) is not bool)
            or (auto_applied and not auto_attempted)
        ):
            _increment("projection", "other", "invalid_result")
            return result

        strategies: list[CompactionStrategy] = []
        if micro_applied:
            strategies.append("micro")
        if history_snip_applied:
            strategies.append("history_snip")
        if auto_applied:
            strategies.append("auto_summary")

        if len(strategies) > 1:
            strategy: CompactionStrategy = "mixed"
        elif strategies:
            strategy = strategies[0]
        elif auto_attempted:
            strategy = "auto_summary"
        else:
            strategy = "none"

        token_pair = _token_pair(result, "original_tokens", "projected_tokens")
        if token_pair is None:
            outcome: CompactionOutcome = "invalid_result"
        elif strategy == "none":
            outcome = "not_needed"
        else:
            before_tokens, after_tokens = token_pair
            reduced = after_tokens < before_tokens if token_reduced is None else token_reduced
            outcome = "applied" if reduced else "ineffective"
        _increment("projection", strategy, outcome)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: metrics cannot alter projection.
        _log_failure("classify context projection decision", exc)
    return result


def record_manual_result(result: _T) -> _T:
    """Record the unique manual transformation result, not persistence wrappers."""

    try:
        compacted_messages = _nonnegative_int(getattr(result, "compacted_messages", None))
        token_pair = _token_pair(result, "pre_compact_tokens", "post_compact_tokens")
        if compacted_messages is None or token_pair is None:
            outcome: CompactionOutcome = "invalid_result"
        elif compacted_messages == 0:
            outcome = "not_needed"
        else:
            before_tokens, after_tokens = token_pair
            outcome = "applied" if after_tokens < before_tokens else "ineffective"
        _increment("manual", "manual_summary", outcome)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: metrics cannot alter compaction.
        _log_failure("classify manual compaction decision", exc)
    return result


def record_reactive_result(result: _T, *, strategy: ReactiveStrategy | str) -> _T:
    """Record a provider-limit compaction result without claiming retry success."""

    try:
        normalized_strategy = _reactive_strategy(strategy)
        token_pair = _token_pair(result, "original_tokens", "projected_tokens")
        if token_pair is None:
            outcome: CompactionOutcome = "invalid_result"
        else:
            before_tokens, after_tokens = token_pair
            target_met = _fallback_target_met(result, normalized_strategy, after_tokens)
            if normalized_strategy == "fallback_trim" and target_met is None:
                outcome = "invalid_result"
            else:
                outcome = "applied" if after_tokens < before_tokens and target_met is not False else "ineffective"
        _increment("provider_limit", normalized_strategy, outcome)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: metrics cannot alter recovery.
        _log_failure("classify provider-limit compaction decision", exc)
    return result


def observe_projection_failures(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Record projection implementation failures while preserving the exception."""

    @wraps(func)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return func(*args, **kwargs)
        except Exception:  # noqa: BLE001 - broad-exception-boundary: preserve original projection failure.
            if kwargs.get("record_projection_event", True) is not False:
                _increment("projection", "other", "error")
            raise

    return cast(Callable[_P, _R], wrapped)


def observe_manual_compaction(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Observe the one shared manual transform seam exactly once."""

    @wraps(func)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            result = func(*args, **kwargs)
        except Exception:  # noqa: BLE001 - broad-exception-boundary: preserve original compaction failure.
            _increment("manual", "manual_summary", "error")
            raise
        return record_manual_result(result)

    return cast(Callable[_P, _R], wrapped)


def observe_reactive_compaction(strategy: ReactiveStrategy) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Observe one reactive transform without interpreting provider outcomes."""

    normalized_strategy = _reactive_strategy(strategy)

    def decorate(func: Callable[_P, _R]) -> Callable[_P, _R]:
        @wraps(func)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            try:
                result = func(*args, **kwargs)
            except Exception:  # noqa: BLE001 - broad-exception-boundary: preserve original recovery failure.
                _increment("provider_limit", normalized_strategy, "error")
                raise
            return record_reactive_result(result, strategy=normalized_strategy)

        return cast(Callable[_P, _R], wrapped)

    return decorate


def _token_pair(result: object, before_field: str, after_field: str) -> tuple[int, int] | None:
    before = _nonnegative_int(getattr(result, before_field, None))
    after = _nonnegative_int(getattr(result, after_field, None))
    if before is None or after is None:
        return None
    return before, after


def _fallback_target_met(result: object, strategy: CompactionStrategy, after_tokens: int) -> bool | None:
    if strategy != "fallback_trim":
        return None
    metadata = getattr(result, "compact_metadata", None)
    if not isinstance(metadata, dict):
        return None
    target_tokens = _nonnegative_int(metadata.get("target_tokens"))
    if target_tokens is None:
        return None
    return after_tokens <= target_tokens


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _reactive_strategy(value: object) -> CompactionStrategy:
    normalized = str(value or "").strip().casefold()
    return cast(
        CompactionStrategy,
        normalized if normalized in {"reactive_summary", "fallback_trim"} else "other",
    )


def _increment(
    trigger: CompactionTrigger | str,
    strategy: CompactionStrategy | str,
    outcome: CompactionOutcome | str,
) -> None:
    labels = {
        "trigger": _normalize(trigger, CONTEXT_COMPACTION_TRIGGERS, fallback="other"),
        "strategy": _normalize(strategy, CONTEXT_COMPACTION_STRATEGIES, fallback="other"),
        "outcome": _normalize(outcome, CONTEXT_COMPACTION_OUTCOMES, fallback="invalid_result"),
    }
    try:
        metrics.increment_counter("context_compaction_decisions_total", labels=labels)
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: metrics must never alter compaction.
        _log_failure("record context compaction metric", exc)


def _normalize(value: object, allowed: frozenset[str], *, fallback: str) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in allowed else fallback


def _log_failure(operation: str, exc: BaseException) -> None:
    try:
        log_best_effort_failure(logger, operation, exc)
    except Exception:  # noqa: BLE001, S110 - broad-exception-boundary: recovery logging cannot alter behavior.
        pass
