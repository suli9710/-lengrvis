from __future__ import annotations

from enum import StrEnum


class RiskLevel(StrEnum):
    R0_READ_ONLY = "R0_READ_ONLY"
    R1_OPEN_ONLY = "R1_OPEN_ONLY"
    R2_REVERSIBLE_MODIFY = "R2_REVERSIBLE_MODIFY"
    R3_DESTRUCTIVE_OR_SYSTEM = "R3_DESTRUCTIVE_OR_SYSTEM"
    R4_FORBIDDEN_OR_HANDOFF = "R4_FORBIDDEN_OR_HANDOFF"


class SafetyVerdict(StrEnum):
    ALLOW = "allow"
    NEEDS_USER_APPROVAL = "needs_user_approval"
    REVISE_PLAN = "revise_plan"
    DENY = "deny"


RISK_ORDER = {
    RiskLevel.R0_READ_ONLY: 0,
    RiskLevel.R1_OPEN_ONLY: 1,
    RiskLevel.R2_REVERSIBLE_MODIFY: 2,
    RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM: 3,
    RiskLevel.R4_FORBIDDEN_OR_HANDOFF: 4,
}


def max_risk(levels: list[RiskLevel]) -> RiskLevel:
    if not levels:
        return RiskLevel.R0_READ_ONLY
    return max(levels, key=lambda level: RISK_ORDER[level])

