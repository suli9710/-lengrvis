from __future__ import annotations

import hmac
from typing import Literal

from app.core.schemas import Plan, PlanStep
from app.policy.approval_binding import args_binding_hmac

DETERMINISTIC_PLAN_CREATOR = "PlannerAgent:deterministic-v1"
DETERMINISTIC_CONTRACT_SCHEMA = 1
DETERMINISTIC_CONTRACT_KEY = "deterministic_contract"

DeterministicContractStatus = Literal["none", "valid", "invalid"]


def seal_deterministic_plan(plan: Plan) -> Plan:
    """Bind every code-authored deterministic step to its exact tool and args."""

    plan.created_by_agent = DETERMINISTIC_PLAN_CREATOR
    for step in plan.steps:
        marker = {
            "schema_version": DETERMINISTIC_CONTRACT_SCHEMA,
            "binding_hmac": _step_binding(step),
        }
        step.model_action = {
            **dict(step.model_action or {}),
            DETERMINISTIC_CONTRACT_KEY: marker,
        }
    return plan


def deterministic_contract_status(step: PlanStep) -> DeterministicContractStatus:
    model_action = step.model_action if isinstance(step.model_action, dict) else {}
    marker = model_action.get(DETERMINISTIC_CONTRACT_KEY)
    if marker is None:
        return "none"
    if not isinstance(marker, dict):
        return "invalid"
    if marker.get("schema_version") != DETERMINISTIC_CONTRACT_SCHEMA:
        return "invalid"
    stored = str(marker.get("binding_hmac") or "")
    expected = _step_binding(step)
    if not stored or not hmac.compare_digest(stored, expected):
        return "invalid"
    return "valid"


def plan_has_deterministic_contract(plan: Plan) -> bool:
    return bool(
        plan.created_by_agent == DETERMINISTIC_PLAN_CREATOR
        or any(DETERMINISTIC_CONTRACT_KEY in dict(step.model_action or {}) for step in plan.steps)
    )


def _step_binding(step: PlanStep) -> str:
    return args_binding_hmac(
        step.tool_name,
        dict(step.args or {}),
        task_id=step.task_id,
        step_id=step.id,
    )
