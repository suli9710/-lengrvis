"""Business intent is distinct from credential access; effects still need approval."""

from __future__ import annotations

import sqlite3

TRANSACTION_TERMS = frozenset({"pay", "payment", "支付", "付款", "order", "下单"})


def managed_butler_task(task_id: str | None) -> bool:
    if not task_id:
        return False
    from app.core import db
    from app.core.schemas import Task
    from app.orchestration.butler_runtime import bound_matter

    try:
        row = db.fetch_one("tasks", task_id)
        return bool(row and bound_matter(Task.model_validate(row)))
    except (ValueError, RuntimeError, sqlite3.Error):
        return False


def restricted_hits(hits: list[str], task_id: str | None) -> list[str]:
    # Signed matter/task binding grants no permission to commit. It only lets
    # the ordinary planner prepare a business workflow whose writes still pass
    # the concrete tool approval and fresh resource-snapshot gates.
    return [term for term in hits if term not in TRANSACTION_TERMS] if managed_butler_task(task_id) else hits
