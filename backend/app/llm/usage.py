from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.config import AppSettings
from app.core import db
from app.core.schemas import now_iso
from app.llm.types import LLMCost, LLMResponse, LLMUsage


CLAUDE_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def estimate_usage(messages: list[dict[str, Any]], content: str) -> LLMUsage:
    prompt_tokens = _count_messages(messages)
    completion_tokens = _rough_tokens(content)
    return LLMUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        estimated=True,
    )


def usage_breakdown(usage: LLMUsage) -> dict[str, Any]:
    details = usage.details or {}
    prompt_details = details.get("prompt_tokens_details") if isinstance(details.get("prompt_tokens_details"), dict) else {}
    completion_details = (
        details.get("completion_tokens_details") if isinstance(details.get("completion_tokens_details"), dict) else {}
    )
    cache_read = _int_token(
        details.get("cache_read_input_tokens"),
        details.get("cached_input_tokens"),
        prompt_details.get("cached_tokens"),
    )
    cache_creation = _int_token(
        details.get("cache_creation_input_tokens"),
        details.get("cache_write_input_tokens"),
        details.get("uncached_input_tokens"),
    )
    input_tokens = max(0, int(usage.prompt_tokens or 0) - cache_read - cache_creation)
    output_tokens = max(0, int(usage.completion_tokens or 0))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "reasoning_output_tokens": _int_token(details.get("reasoning_output_tokens"), completion_details.get("reasoning_tokens")),
        "estimated": bool(usage.estimated),
        "total_tokens": int(usage.total_tokens or (usage.prompt_tokens + usage.completion_tokens)),
    }


def claude_usage_view(usage: LLMUsage) -> dict[str, int]:
    breakdown = usage_breakdown(usage)
    return {key: int(breakdown.get(key) or 0) for key in CLAUDE_USAGE_KEYS}


def record_llm_response(
    response: LLMResponse,
    settings: AppSettings,
    *,
    task: str,
    purpose: str,
    profile: dict[str, Any] | None = None,
    projection: dict[str, Any] | None = None,
) -> None:
    try:
        db.init_db()
        cost = response.cost or LLMCost(estimated=True)
        data = {
            "id": f"llm_usage_{uuid4().hex}",
            "provider": response.provider,
            "model": response.model,
            "mode": settings.mode,
            "task": task,
            "purpose": purpose,
            "usage": response.usage.to_dict(),
            "usage_breakdown": usage_breakdown(response.usage),
            "claude_usage": claude_usage_view(response.usage),
            "cost": cost.to_dict(),
            "finish_reason": response.finish_reason,
            "metadata": response.metadata,
            "profile": profile or {},
            "projection": projection or {},
            "created_at": now_iso(),
        }
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_usage_events (
                    id, provider, model, mode, task, purpose,
                    prompt_tokens, completion_tokens, total_tokens,
                    total_cost_usd, estimated, data, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["id"],
                    response.provider,
                    response.model,
                    settings.mode,
                    task,
                    purpose,
                    int(response.usage.prompt_tokens),
                    int(response.usage.completion_tokens),
                    int(response.usage.total_tokens),
                    cost.total_cost_usd,
                    1 if response.usage.estimated or cost.estimated else 0,
                    json.dumps(data, ensure_ascii=False),
                    data["created_at"],
                ),
            )
    except Exception:
        # Usage telemetry must never make an LLM call fail.
        return


def list_usage_events(*, limit: int = 100) -> list[dict[str, Any]]:
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT data FROM llm_usage_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(1000, int(limit))),),
        ).fetchall()
    return [json.loads(row["data"]) for row in rows]


def usage_summary(*, hours: int = 24) -> dict[str, Any]:
    db.init_db()
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT provider, model, prompt_tokens, completion_tokens, total_tokens,
                   total_cost_usd, estimated, data, created_at
            FROM llm_usage_events
            WHERE created_at >= ?
            ORDER BY created_at DESC
            """,
            (since.isoformat(),),
        ).fetchall()
    total_prompt = 0
    total_completion = 0
    total_tokens = 0
    total_cost = 0.0
    cost_known = False
    estimated = False
    claude_totals = {key: 0 for key in CLAUDE_USAGE_KEYS}
    by_model: dict[str, dict[str, Any]] = {}
    last_event_at = ""
    for row in rows:
        prompt = int(row["prompt_tokens"] or 0)
        completion = int(row["completion_tokens"] or 0)
        tokens = int(row["total_tokens"] or 0)
        cost = row["total_cost_usd"]
        model_key = f"{row['provider']}:{row['model']}"
        item = by_model.setdefault(
            model_key,
            {
                "provider": row["provider"],
                "model": row["model"],
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "estimated": False,
            },
        )
        item["calls"] += 1
        item["prompt_tokens"] += prompt
        item["completion_tokens"] += completion
        item["total_tokens"] += tokens
        if cost is not None:
            cost_known = True
            total_cost += float(cost)
            item["total_cost_usd"] += float(cost)
        if row["estimated"]:
            estimated = True
            item["estimated"] = True
        total_prompt += prompt
        total_completion += completion
        total_tokens += tokens
        last_event_at = max(last_event_at, str(row["created_at"] or ""))
        try:
            data = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            data = {}
        claude_usage = data.get("claude_usage") if isinstance(data, dict) else {}
        if isinstance(claude_usage, dict):
            for key in CLAUDE_USAGE_KEYS:
                value = int(claude_usage.get(key) or 0)
                claude_totals[key] += value
                item.setdefault("claude_usage", {usage_key: 0 for usage_key in CLAUDE_USAGE_KEYS})
                item["claude_usage"][key] += value
    return {
        "window_hours": max(1, int(hours)),
        "calls": len(rows),
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 8) if cost_known else None,
        "estimated": estimated or not cost_known,
        "last_event_at": last_event_at,
        "claude_usage": claude_totals,
        "by_model": list(by_model.values()),
    }


def _count_messages(messages: list[dict[str, Any]]) -> int:
    return sum(_rough_tokens(message.get("content", "")) + 4 for message in messages)


def _rough_tokens(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return max(0, round(len(value) / 4))
    if isinstance(value, list):
        return sum(_rough_tokens(item) for item in value)
    if isinstance(value, dict):
        return max(1, round(len(json.dumps(value, ensure_ascii=False, default=str)) / 2))
    return max(0, round(len(str(value)) / 4))


def _int_token(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0
