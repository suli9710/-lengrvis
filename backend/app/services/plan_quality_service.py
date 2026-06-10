"""Read-only plan quality metrics (playbook P5).

Every LLM-planned step carries a model_action envelope whose runtime metadata
records ``model_supplied_risk_level`` (what the model claimed) next to
``derived_risk_level`` (what the registry enforced). The consistency rate
between the two is an offline signal for planner prompt tuning: a rising rate
means the model understands task risk better.
"""

from __future__ import annotations

from typing import Any

from app.core import db


def risk_annotation_consistency(limit: int = 500) -> dict[str, Any]:
    """Aggregate model-vs-derived risk agreement over recently stored plans."""
    plans = db.fetch_many("plans", limit=max(1, int(limit)))
    steps_total = 0
    annotated = 0
    consistent = 0
    mismatches: dict[str, dict[str, Any]] = {}

    for plan in plans:
        for step in plan.get("steps") or []:
            steps_total += 1
            envelope = step.get("model_action") or {}
            metadata = envelope.get("runtime_metadata") or {}
            supplied = str(metadata.get("model_supplied_risk_level") or "").strip()
            derived = str(metadata.get("derived_risk_level") or "").strip()
            if not supplied or not derived:
                continue
            annotated += 1
            if supplied == derived:
                consistent += 1
                continue
            tool_name = str(step.get("tool_name") or "unknown")
            entry = mismatches.setdefault(
                tool_name,
                {"tool": tool_name, "count": 0, "examples": []},
            )
            entry["count"] += 1
            if len(entry["examples"]) < 3:
                entry["examples"].append({"model_supplied": supplied, "derived": derived})

    return {
        "plans_scanned": len(plans),
        "steps_total": steps_total,
        "steps_annotated": annotated,
        "steps_consistent": consistent,
        "consistency_rate": round(consistent / annotated, 4) if annotated else None,
        "mismatches_by_tool": sorted(mismatches.values(), key=lambda item: -item["count"]),
    }
