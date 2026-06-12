"""Isolation helpers for parallel plan-step execution (R4-H1).

Parallel batches must not hand the same mutable ``PlanStep`` object to
concurrently running executor tasks: an executor mutates step fields across
await points (status, args, model_action, ...), so a sibling that persists or
inspects the shared plan can observe—or clobber—a half-updated step.

The contract implemented here:

- each parallel executor receives a deep-copied *snapshot* of its step and
  mutates only that snapshot;
- the scheduler writes the snapshot back into the real plan step *serially*
  (in its single collector coroutine) once the executor task has finished.
"""

from __future__ import annotations

from app.core.schemas import PlanStep


def snapshot_step(step: PlanStep) -> PlanStep:
    """Deep-copy a plan step for isolated execution in a parallel batch."""
    return step.model_copy(deep=True)


def write_back_step(real: PlanStep, snapshot: PlanStep) -> None:
    """Serially copy every field of the executed snapshot into the real step."""
    if real is snapshot:
        return
    for name in PlanStep.model_fields:
        setattr(real, name, getattr(snapshot, name))
