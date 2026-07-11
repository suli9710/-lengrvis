from __future__ import annotations

import math
from datetime import UTC, datetime

from app.automation.models import (
    BudgetConsumeRequest,
    BudgetDecision,
    RunBudgetLedger,
    RunBudgetLimits,
    RunBudgetUsage,
    parse_utc,
)
from app.core import audit, db
from app.core.content_provenance import stable_content_hash
from app.core.schemas import now_iso

SOFT_THRESHOLD_RATIO = 0.8


class BudgetExceededError(RuntimeError):
    def __init__(self, decision: BudgetDecision) -> None:
        self.decision = decision
        super().__init__(decision.reason or "run budget was exhausted")


def create_run_budget(run_id: str, *, limits: RunBudgetLimits | None = None) -> RunBudgetLedger:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = _load_ledger_for_update(conn, run_id)
        if existing is not None:
            return existing
        ledger = RunBudgetLedger(run_id=run_id, limits=limits or RunBudgetLimits())
        _store_ledger(ledger, conn=conn)
    audit.record(
        "run_budget.created",
        "RunBudgetService",
        {"run_id": run_id, "limits": ledger.limits.model_dump(mode="json")},
    )
    return ledger


def get_run_budget(run_id: str) -> RunBudgetLedger | None:
    db.init_db()
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM run_budget_ledgers WHERE run_id = ?", (run_id,)).fetchone()
    return RunBudgetLedger.model_validate_json(row["data"]) if row else None


def tighten_run_budget(run_id: str, limits: RunBudgetLimits) -> RunBudgetLedger:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ledger = _load_ledger_for_update(conn, run_id)
        if ledger is None:
            raise KeyError(run_id)
        current = ledger.limits.model_dump()
        candidate = limits.model_dump()
        expanded = [key for key, value in candidate.items() if int(value) > int(current[key])]
        if expanded:
            raise ValueError(f"run budget limits cannot be expanded: {', '.join(sorted(expanded))}")
        ledger.limits = limits
        ledger.version += 1
        ledger.updated_at = now_iso()
        _store_ledger(ledger, conn=conn)
    return ledger


def consume_run_budget(run_id: str, event: BudgetConsumeRequest) -> BudgetDecision:
    return consume_run_budget_events(run_id, [event])


def consume_run_budget_events(
    run_id: str,
    events: list[BudgetConsumeRequest],
    *,
    recipients: list[str] | None = None,
    domains: list[str] | None = None,
) -> BudgetDecision:
    if not events:
        raise ValueError("at least one run budget event is required")
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ledger = _load_ledger_for_update(conn, run_id)
        if ledger is None:
            raise KeyError(run_id)
        if ledger.status == "hard_stopped":
            return BudgetDecision(
                allowed=False,
                hard_exceeded=True,
                reason=ledger.hard_stop_reason or "run budget is already hard-stopped",
                ledger=ledger,
            )
        if ledger.status == "soft_exceeded":
            return BudgetDecision(
                allowed=False,
                soft_exceeded=True,
                reason="; ".join(_soft_limit_reasons(ledger, ledger.usage))
                or "run budget is paused for user review",
                ledger=ledger,
            )

        usage = ledger.usage.model_copy(deep=True)
        for event in events:
            _apply_event(usage, event)
        _bind_scope_identifiers(usage, recipients=recipients or [], domains=domains or [])
        hard_reasons = _hard_limit_reasons(ledger, usage)
        if hard_reasons:
            ledger.status = "hard_stopped"
            ledger.hard_stop_reason = "; ".join(hard_reasons)
        else:
            ledger.status = "soft_exceeded" if _soft_limit_reasons(ledger, usage) else "active"
            ledger.hard_stop_reason = ""
        ledger.usage = usage
        ledger.version += 1
        ledger.updated_at = now_iso()
        _store_ledger(ledger, conn=conn)

    hard = ledger.status == "hard_stopped"
    soft = ledger.status == "soft_exceeded"
    allowed = not hard and not soft
    reason = ledger.hard_stop_reason or "; ".join(_soft_limit_reasons(ledger, ledger.usage))
    audit.record(
        "run_budget.hard_stopped" if hard else "run_budget.soft_paused" if soft else "run_budget.consumed",
        "RunBudgetService",
        {
            "run_id": run_id,
            "events": [_audit_event(event) for event in events],
            "recipient_bindings": len(set(recipients or [])),
            "domain_bindings": len(set(domains or [])),
            "allowed": allowed,
            "soft_exceeded": soft,
            "hard_exceeded": hard,
            "reason": reason,
            "version": ledger.version,
        },
    )
    return BudgetDecision(
        allowed=allowed,
        soft_exceeded=soft,
        hard_exceeded=hard,
        reason=reason,
        ledger=ledger,
    )


def require_run_budget(run_id: str, event: BudgetConsumeRequest) -> RunBudgetLedger:
    decision = consume_run_budget(run_id, event)
    if not decision.allowed:
        raise BudgetExceededError(decision)
    return decision.ledger


def _apply_event(usage: RunBudgetUsage, event: BudgetConsumeRequest) -> None:
    amount = int(event.amount)
    if event.kind == "tool_call":
        usage.tool_calls += amount
    elif event.kind == "write":
        usage.writes += amount
    elif event.kind == "external_send":
        usage.external_sends += amount
    elif event.kind == "ui_input":
        usage.ui_inputs += amount
    elif event.kind == "retry":
        usage.retries += amount
    elif event.kind == "subprocess":
        usage.subprocesses += amount
    elif event.kind == "parallel":
        usage.max_parallel_fanout_seen = max(usage.max_parallel_fanout_seen, event.parallel_fanout or amount)
    if event.recipient:
        usage.recipients = sorted({*usage.recipients, _identifier_digest("recipient", event.recipient)})
    if event.domain:
        usage.domains = sorted({*usage.domains, _identifier_digest("domain", event.domain)})
    if event.action_fingerprint:
        fingerprint = _identifier_digest("action", event.action_fingerprint)
        usage.duplicate_actions[fingerprint] = usage.duplicate_actions.get(fingerprint, 0) + 1


def _bind_scope_identifiers(
    usage: RunBudgetUsage,
    *,
    recipients: list[str],
    domains: list[str],
) -> None:
    for recipient in recipients:
        if recipient:
            usage.recipients = sorted({*usage.recipients, _identifier_digest("recipient", recipient)})
    for domain in domains:
        if domain:
            usage.domains = sorted({*usage.domains, _identifier_digest("domain", domain)})


def _identifier_digest(kind: str, value: str) -> str:
    return stable_content_hash({"kind": kind, "value": str(value).strip().casefold()})


def _audit_event(event: BudgetConsumeRequest) -> dict[str, object]:
    return {
        "kind": event.kind,
        "amount": event.amount,
        "has_recipient": bool(event.recipient),
        "has_domain": bool(event.domain),
        "has_action_fingerprint": bool(event.action_fingerprint),
        "parallel_fanout": event.parallel_fanout,
    }


def _hard_limit_reasons(ledger: RunBudgetLedger, usage: RunBudgetUsage) -> list[str]:
    limits = ledger.limits
    reasons: list[str] = []
    checks = (
        (usage.tool_calls, limits.max_tool_calls, "tool call budget exceeded"),
        (usage.writes, limits.max_writes, "write budget exceeded"),
        (usage.external_sends, limits.max_external_sends, "external send budget exceeded"),
        (len(usage.recipients), limits.max_recipients, "recipient budget exceeded"),
        (len(usage.domains), limits.max_domains, "destination-domain budget exceeded"),
        (usage.ui_inputs, limits.max_ui_inputs, "UI input budget exceeded"),
        (usage.retries, limits.max_retries, "retry budget exceeded"),
        (usage.subprocesses, limits.max_subprocesses, "subprocess budget exceeded"),
        (usage.max_parallel_fanout_seen, limits.max_parallel_fanout, "parallel fan-out budget exceeded"),
    )
    for actual, maximum, message in checks:
        if actual > maximum:
            reasons.append(message)
    elapsed = (datetime.now(UTC) - parse_utc(ledger.created_at)).total_seconds()
    if elapsed > limits.max_wall_clock_seconds:
        reasons.append("wall-clock budget exceeded")
    duplicates = max(usage.duplicate_actions.values(), default=0)
    if duplicates > limits.max_duplicate_actions:
        reasons.append("duplicate-action budget exceeded")
    return reasons


def _soft_limit_reasons(ledger: RunBudgetLedger, usage: RunBudgetUsage) -> list[str]:
    limits = ledger.limits
    reasons: list[str] = []
    checks = (
        (usage.tool_calls, limits.max_tool_calls, "tool calls near limit"),
        (usage.writes, limits.max_writes, "writes near limit"),
        (usage.external_sends, limits.max_external_sends, "external sends near limit"),
        (len(usage.recipients), limits.max_recipients, "recipients near limit"),
        (len(usage.domains), limits.max_domains, "domains near limit"),
        (usage.ui_inputs, limits.max_ui_inputs, "UI inputs near limit"),
        (usage.retries, limits.max_retries, "retries near limit"),
        (usage.subprocesses, limits.max_subprocesses, "subprocesses near limit"),
        (usage.max_parallel_fanout_seen, limits.max_parallel_fanout, "parallel fan-out near limit"),
    )
    for actual, maximum, message in checks:
        if maximum > 0 and actual >= math.ceil(maximum * SOFT_THRESHOLD_RATIO):
            reasons.append(message)
    return reasons


def _load_ledger_for_update(conn, run_id: str) -> RunBudgetLedger | None:
    row = conn.execute("SELECT data FROM run_budget_ledgers WHERE run_id = ?", (run_id,)).fetchone()
    return RunBudgetLedger.model_validate_json(row["data"]) if row else None


def _store_ledger(ledger: RunBudgetLedger, *, conn=None) -> None:
    db.init_db()
    if conn is None:
        with db.connect() as connection:
            _store_ledger(ledger, conn=connection)
        return
    conn.execute(
        """
        INSERT INTO run_budget_ledgers (
            id, run_id, status, version, data, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            status=excluded.status,
            version=excluded.version,
            data=excluded.data,
            updated_at=excluded.updated_at
        """,
        (
            ledger.id,
            ledger.run_id,
            ledger.status,
            ledger.version,
            ledger.model_dump_json(),
            ledger.created_at,
            ledger.updated_at,
        ),
    )
