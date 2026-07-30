"""Memory-isolation evidence helpers for the real-LLM evaluation harness."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


def _memory_lifecycle_snapshot() -> dict[str, tuple[str, str]]:
    """Return internal comparison material; callers must never serialize it."""

    from app.core import db

    snapshot: dict[str, tuple[str, str]] = {}
    for memory in db.fetch_many("memories", limit=1_000_000):
        memory_id = str(memory.get("id") or "").strip()
        if not memory_id:
            raise ValueError("persisted memory is missing an id")
        state = str(memory.get("state") or "other").strip().casefold()
        if state not in {"active", "quarantined", "revoked"}:
            state = "other"
        canonical = json.dumps(
            memory,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot[memory_id] = (
            state,
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
    return snapshot


def _memory_lifecycle_summary(
    snapshot: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    states = {"active": 0, "quarantined": 0, "revoked": 0, "other": 0}
    for state, _fingerprint in snapshot.values():
        states[state if state in states else "other"] += 1
    return {"total": len(snapshot), "states": states}


def _empty_memory_lifecycle_evidence() -> dict[str, Any]:
    empty = _memory_lifecycle_summary({})
    return {
        "verified": False,
        "before": empty,
        "after": empty,
        "created_count": 0,
        "removed_count": 0,
        "modified_count": 0,
        "state_transition_count": 0,
        "unexpected_persistence_count": 0,
    }


def _memory_lifecycle_evidence(
    before: dict[str, tuple[str, str]],
    after: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    before_ids = set(before)
    after_ids = set(after)
    shared_ids = before_ids & after_ids
    created_count = len(after_ids - before_ids)
    removed_count = len(before_ids - after_ids)
    modified_count = sum(
        1 for memory_id in shared_ids if before[memory_id][1] != after[memory_id][1]
    )
    state_transition_count = sum(
        1 for memory_id in shared_ids if before[memory_id][0] != after[memory_id][0]
    )
    return {
        "verified": True,
        "before": _memory_lifecycle_summary(before),
        "after": _memory_lifecycle_summary(after),
        "created_count": created_count,
        "removed_count": removed_count,
        "modified_count": modified_count,
        "state_transition_count": state_transition_count,
        "unexpected_persistence_count": created_count + modified_count,
    }


@dataclass(frozen=True, slots=True)
class _MemoryFixtureHandle:
    """Sensitive fixture identity and baselines that must remain evaluator-internal."""

    memory_id: str
    expired: bool
    initial_use_count: int
    initial_last_used_at: str
    seed_active: bool
    seed_user_confirmed: bool
    seed_expired: bool


def _empty_memory_fixture_evidence() -> dict[str, Any]:
    return {
        "verified": False,
        "seeded": False,
        "seed_active": False,
        "seed_user_confirmed": False,
        "seed_expired": False,
        "recall_probe_executed": False,
        "fixture_recalled": False,
        "fixture_present_after": False,
        "use_count_unchanged": False,
        "last_used_at_unchanged": False,
    }


def _parse_utc_datetime(value: Any) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seed_memory_fixture(task: dict[str, Any]) -> _MemoryFixtureHandle | None:
    """Seed one isolated memory without ever copying its identity into a report."""

    if "memory_fixture" not in task:
        return None
    fixture = task.get("memory_fixture")
    if not isinstance(fixture, dict):
        raise ValueError("memory_fixture must be an object")
    content = fixture.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("memory_fixture.content must be non-empty text")
    kind = fixture.get("kind", "preference")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("memory_fixture.kind must be non-empty text")
    expired = fixture.get("expired")
    if type(expired) is not bool:
        raise ValueError("memory_fixture.expired must be a boolean")
    recall_query = fixture.get("recall_query")
    if recall_query is not None and (
        not isinstance(recall_query, str) or not recall_query.strip()
    ):
        raise ValueError("memory_fixture.recall_query must be non-empty text")

    from app.core import db
    from app.core.content_provenance import create_content_envelope

    # Match the production ``Memory`` identifier shape so the deterministic
    # planner exercises the same revoke parser used for real saved memories.
    memory_id = f"mem_eval_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    expires_at = (now - timedelta(days=1)).isoformat() if expired else ""
    last_used_at = (now - timedelta(days=2)).isoformat()
    use_count = 7
    content_envelope = create_content_envelope(
        content,
        source_kind="user_input",
        source_id=memory_id,
        origin="user",
        trust_level="user_confirmed",
        task_scope=memory_id,
        user_confirmed=True,
    )
    db.upsert_memory(
        {
            "id": memory_id,
            "kind": kind.strip(),
            "content": content,
            "tags": ["real-llm-eval-fixture"],
            "task_id": memory_id,
            "source": "user",
            "state": "active",
            "user_confirmed": True,
            "expires_at": expires_at,
            "use_count": use_count,
            "last_used_at": last_used_at,
            "content_envelope": content_envelope,
        }
    )
    seeded = db.get_memory(memory_id)
    if not isinstance(seeded, dict):
        raise RuntimeError("memory fixture was not persisted")
    seed_expiry = _parse_utc_datetime(seeded.get("expires_at"))
    return _MemoryFixtureHandle(
        memory_id=memory_id,
        expired=expired,
        initial_use_count=int(seeded.get("use_count") or 0),
        initial_last_used_at=str(seeded.get("last_used_at") or ""),
        seed_active=str(seeded.get("state") or "").casefold() == "active",
        seed_user_confirmed=seeded.get("user_confirmed") is True,
        seed_expired=bool(
            expired
            and seed_expiry is not None
            and seed_expiry <= datetime.now(timezone.utc)
        ),
    )


def _probe_memory_fixture_recall(
    client: Any,
    query: str,
    fixture: _MemoryFixtureHandle,
) -> bool:
    """Exercise the production recall route and retain only a membership boolean."""

    response = client.post(
        "/api/memories/recall",
        json={"query": query, "k": 10},
    )
    if response.status_code != 200:
        raise RuntimeError(f"memory recall probe failed: HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, list) or any(
        not isinstance(item, dict) for item in payload
    ):
        raise ValueError("memory recall probe returned an invalid response")
    return any(str(item.get("id") or "") == fixture.memory_id for item in payload)


def _memory_fixture_evidence(
    fixture: _MemoryFixtureHandle,
    *,
    recall_probe_executed: bool,
    fixture_recalled: bool,
    verification_error: str = "",
) -> dict[str, Any]:
    """Build content/identity-free proof for one specified expired fixture."""

    from app.core import db

    evidence = _empty_memory_fixture_evidence()
    evidence.update(
        {
            "seeded": True,
            "seed_active": fixture.seed_active,
            "seed_user_confirmed": fixture.seed_user_confirmed,
            "seed_expired": fixture.seed_expired,
            "recall_probe_executed": recall_probe_executed,
            "fixture_recalled": fixture_recalled,
        }
    )
    try:
        stored = db.get_memory(fixture.memory_id)
        evidence["fixture_present_after"] = isinstance(stored, dict)
        if isinstance(stored, dict):
            try:
                current_use_count = int(stored.get("use_count") or 0)
            except (TypeError, ValueError):
                current_use_count = -1
            evidence["use_count_unchanged"] = (
                current_use_count == fixture.initial_use_count
            )
            evidence["last_used_at_unchanged"] = (
                str(stored.get("last_used_at") or "") == fixture.initial_last_used_at
            )
    except Exception as exc:  # noqa: BLE001 - evidence must fail closed.
        verification_error = verification_error or type(exc).__name__
    evidence["verified"] = bool(
        not verification_error and recall_probe_executed and fixture.expired
    )
    if verification_error:
        evidence["verification_error"] = verification_error
    return evidence
