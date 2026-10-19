"""Save grounded conversational memories through the existing MemoryAgent."""

from __future__ import annotations

import re

from app.agents.delegation_metadata import is_memory_non_persistence_goal
from app.agents.memory_agent import MemoryAgent
from app.core import butler_store as store
from app.core import db
from app.core.butler_models import ButlerMatter, MemoryProposal
from app.core.local_data_lifecycle import tracks_local_data
from app.core.schemas import MemoryConflictStatus, MemoryState
from app.indexer.embedding_service import embed_texts
from app.llm.registry import get_settings_for_mode
from app.policy.sensitive_values import looks_sensitive_value

_SECRETS = re.compile(r"密码|验证码|密钥|口令|银行卡|身份证|\b(?:password|otp|secret|api.?key|token|cookie)\b", re.I)
_AUTHORITY = re.compile(
    r"授权|免审批|跳过.{0,8}(?:审批|确认)|忽略.{0,8}(?:规则|指令)|无需.{0,8}(?:审批|确认)|"
    r"\b(?:authorization|bypass|ignore instructions)\b",
    re.I,
)
_NO_STORE = re.compile(r"不要.{0,12}(?:记|保存)|别.{0,8}(?:记|保存)|仅限本次|只用于本次|不要长期保留")


def memory_allowed(matter: ButlerMatter) -> bool:
    return (
        store.control_state()["auto_memory_enabled"]
        and matter.source == "user"
        and bool(matter.memory_inputs)
        and not any(is_memory_non_persistence_goal(text) or _NO_STORE.search(text) for text in matter.memory_inputs)
    )


def _eligible(proposal: MemoryProposal, matter: ButlerMatter) -> bool:
    if len(proposal.content.strip()) < 2 or len(proposal.evidence.strip()) < 2:
        return False
    if not any(proposal.evidence in text for text in matter.memory_inputs):
        return False
    if proposal.basis == "user_statement" and proposal.content != proposal.evidence:
        return False
    if proposal.basis == "user_statement":
        # A substring alone could drop a negation or the surrounding attribution.
        trim = " \t\r\n。！？.!?；;"
        clauses = [
            clause.strip(trim)
            for text in matter.memory_inputs
            for clause in re.split(r"[。！？；;\n]|(?<=[.!?])\s+", text)
        ]
        if proposal.evidence.strip(trim) not in clauses:
            return False
    return not any(
        looks_sensitive_value(text) or _SECRETS.search(text) or _AUTHORITY.search(text)
        for text in (proposal.content, proposal.evidence)
    )


@tracks_local_data
async def save_proposals(matter: ButlerMatter, proposals: list[MemoryProposal]) -> ButlerMatter:
    if not proposals or not memory_allowed(matter):
        return matter
    candidates = []
    for proposal in proposals:
        key = store.source_key(proposal.evidence.strip(" \t\r\n。！？.!?；;"))
        if key not in matter.memory_observation_keys and _eligible(proposal, matter):
            candidates.append((key, proposal))
    if not candidates:
        return matter

    # Model and embedding work must finish before opening a SQLite transaction.
    # In privacy mode, embeddings follow that mode's existing local routing.
    try:
        # Revisions inherit their parent's egress restrictions in remember().
        # Prepare them locally before persistence, including legacy parents
        # whose source policy cannot be established.
        embedding_mode = "privacy" if any(proposal.supersedes for _, proposal in candidates) else matter.mode
        vectors = await embed_texts(
            [proposal.content for _, proposal in candidates],
            settings=get_settings_for_mode(embedding_mode),
        )
    except (OSError, RuntimeError, ValueError, TimeoutError):
        # Provider failure degrades to text-only memory; programming errors and
        # cancellation still propagate rather than silently losing diagnostics.
        vectors = []
    agent = MemoryAgent()
    created = []
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        latest = store.get(ButlerMatter, matter.id)
        if latest is None or latest.version != matter.version:
            raise store.ButlerConflict("事项已变化，稍后重新整理记忆。")
        if not memory_allowed(latest):
            return latest
        existing = db.list_memories(limit=-1)
        for index, (key, proposal) in enumerate(candidates):
            if key in latest.memory_observation_keys:
                continue
            latest.memory_observation_keys.append(key)
            duplicate = next(
                (
                    row
                    for row in existing
                    if row["kind"] == proposal.kind
                    and row["content"] == proposal.content
                    and row.get("source_mode") == matter.mode
                ),
                None,
            )
            if duplicate:
                # Never reactivate a revoked or superseded memory through automatic learning.
                if duplicate["state"] != MemoryState.REVOKED and duplicate["conflict_status"] != "superseded":
                    latest.memory_ids.append(duplicate["id"])
                continue
            parent = next((row for row in existing if row["id"] == proposal.supersedes), None)
            if proposal.supersedes and (parent is None or parent["kind"] != proposal.kind):
                continue
            confirmed = proposal.basis == "user_statement" and parent is None
            memory = await agent.remember(
                proposal.content,
                kind=proposal.kind,
                task_id=matter.id,
                mode=matter.mode,
                source="user" if proposal.basis == "user_statement" else "butler_inference",
                user_confirmed=confirmed,
                tags=["butler", "自动保存", "用户陈述" if confirmed else "待核对"],
                ttl_seconds=proposal.ttl_days * 86400 if proposal.ttl_days else (None if confirmed else 30 * 86400),
                supersedes=proposal.supersedes,
                conflict_status=MemoryConflictStatus.CONFLICTING if parent else MemoryConflictStatus.NONE,
                embedding=vectors[index] if index < len(vectors) else [],
                publish=False,
            )
            # Supplied embeddings and publish=False keep remember free of suspension here.
            latest.memory_ids.append(memory.id)
            created.append(memory)
            existing.append(memory.model_dump(mode="json"))
        latest.memory_ids = list(dict.fromkeys(latest.memory_ids))
        saved = store.save(latest, expected_version=latest.version)
    for memory in created:
        agent.publish_remembered(memory)
    return saved
