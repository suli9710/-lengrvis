"""Deterministic business-field checks over freshly observed data."""

from __future__ import annotations

from app.core.butler_models import CompletionCondition, OutcomeCheck

_MISSING = object()


def expected_matches(actual, check: OutcomeCheck) -> bool:
    if actual is _MISSING:
        return False
    if check.operator == "nonempty":
        return actual is not None and (bool(actual.strip()) if isinstance(actual, str) else bool(actual))
    if isinstance(actual, bool) != isinstance(check.expected, bool):
        return False
    return actual == check.expected


def check_json_outcome(data, condition: CompletionCondition) -> list[dict]:
    result = []
    for check in condition.checks:
        value = data
        for key in check.path:
            if isinstance(value, dict) and isinstance(key, str):
                value = value.get(key, _MISSING)
            elif (
                isinstance(value, list) and isinstance(key, int) and not isinstance(key, bool) and 0 <= key < len(value)
            ):
                value = value[key]
            else:
                value = _MISSING
                break
        result.append({"label": check.label, "matched": expected_matches(value, check)})
    return result


def check_browser_outcome(observation: dict, condition: CompletionCondition) -> list[dict]:
    result = []
    for check in condition.checks:
        field = (observation.get("extracted") or {}).get(check.label) or {}
        value = field.get("text")
        expected = check.model_copy(update={"expected": str(check.expected) if check.expected is not None else None})
        matched = field.get("matched") is True and not field.get("truncated") and expected_matches(value, expected)
        result.append({"label": check.label, "matched": bool(matched)})
    return result
