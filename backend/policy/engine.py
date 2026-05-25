from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""


class PolicyEngine:
    def __init__(self, policy: dict[str, Any] | None = None) -> None:
        self.policy = policy or {}

    def evaluate(self, context: dict[str, Any] | None = None, **kwargs: Any) -> PolicyDecision:
        ctx = context or kwargs
        action = str(ctx.get("action", ""))
        resource = str(ctx.get("resource", ""))
        allowed_actions = {"files.read", "files.search", "system.read"}
        if action in allowed_actions and resource.startswith("workspace://"):
            return PolicyDecision(True, "Allowed by MVP policy.")
        return PolicyDecision(False, "Denied by default.")


def evaluate_policy(context: dict[str, Any] | None = None, **kwargs: Any) -> PolicyDecision:
    return PolicyEngine().evaluate(context, **kwargs)


evaluate = evaluate_policy
authorize = evaluate_policy


def is_allowed(context: dict[str, Any] | None = None, **kwargs: Any) -> bool:
    return evaluate_policy(context, **kwargs).allowed

