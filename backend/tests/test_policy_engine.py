from __future__ import annotations

import pytest

from conftest import import_first, load_json_fixture, require_attr


POLICY_MODULES = (
    "backend.policy.engine",
    "backend.core.policy",
    "backend.security.policy",
    "mavris.policy.engine",
)


def _decision_allowed(decision) -> bool:
    if isinstance(decision, bool):
        return decision
    if isinstance(decision, dict):
        if "allowed" in decision:
            return bool(decision["allowed"])
        if "allow" in decision:
            return bool(decision["allow"])
        if "decision" in decision:
            return str(decision["decision"]).lower() in {"allow", "allowed"}
    for attr in ("allowed", "allow"):
        if hasattr(decision, attr):
            return bool(getattr(decision, attr))
    if hasattr(decision, "decision"):
        return str(getattr(decision, "decision")).lower() in {"allow", "allowed"}
    raise AssertionError(f"Cannot interpret policy decision: {decision!r}")


@pytest.fixture
def policy_api():
    module = import_first(POLICY_MODULES)
    evaluator = require_attr(
        module,
        ("evaluate_policy", "evaluate", "authorize", "is_allowed", "PolicyEngine"),
    )
    if isinstance(evaluator, type):
        instance = evaluator(load_json_fixture("policies/basic_policy.json"))
        return instance.evaluate
    return evaluator


def _evaluate(policy_api, action: str, resource: str, subject: str = "user"):
    context = {
        "subject": subject,
        "action": action,
        "resource": resource,
        "metadata": {"source": "pytest"},
    }
    try:
        return policy_api(context)
    except TypeError:
        return policy_api(subject=subject, action=action, resource=resource, context=context)


def test_policy_allows_declared_read_action(policy_api):
    decision = _evaluate(policy_api, action="files.read", resource="workspace://notes/safe.txt")

    assert _decision_allowed(decision) is True


@pytest.mark.parametrize(
    ("action", "resource"),
    [
        ("files.delete", "workspace://notes/safe.txt"),
        ("shell.exec", "system://powershell"),
        ("files.read", "file:///etc/passwd"),
    ],
)
def test_policy_denies_disallowed_actions_by_default(policy_api, action: str, resource: str):
    decision = _evaluate(policy_api, action=action, resource=resource)

    assert _decision_allowed(decision) is False
