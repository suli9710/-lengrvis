from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_memories
from app.core import db


@pytest.fixture()
def client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    routes_memories._agent_singleton = None
    app = FastAPI()
    app.include_router(routes_memories.router, prefix="/api")
    return TestClient(app)


def test_memory_api_treats_post_as_explicit_user_confirmation(client: TestClient) -> None:
    response = client.post(
        "/api/memories",
        json={"content": "Use monthly invoice folders", "kind": "preference", "tags": ["invoice"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "active"
    assert payload["user_confirmed"] is True
    assert payload["content_envelope"]["source_kind"] == "user_input"


def test_memory_api_promotes_quarantined_record_before_recall(client: TestClient) -> None:
    memory = asyncio.run(
        routes_memories._agent().remember(
            "Automatically inferred filing rule",
            source="OrchestratorAgent",
            user_confirmed=False,
        )
    )

    before = client.post("/api/memories/recall", json={"query": "filing rule"})
    promoted = client.post(f"/api/memories/{memory.id}/promote")
    after = client.post("/api/memories/recall", json={"query": "filing rule"})

    assert before.status_code == 200 and before.json() == []
    assert promoted.status_code == 200
    assert promoted.json()["state"] == "active"
    assert [item["id"] for item in after.json()] == [memory.id]


def test_memory_api_uses_normalized_quarantine_state_and_delete_cascades(client: TestClient) -> None:
    memory = asyncio.run(
        routes_memories._agent().remember(
            "Inferred rule that still needs review",
            source="OrchestratorAgent",
            user_confirmed=False,
            ttl_seconds=300,
        )
    )
    with db.connect() as conn:
        normalized = conn.execute(
            """
            SELECT state, source, user_confirmed, expires_at, provenance_source_kind
            FROM memory_quarantine
            WHERE memory_id = ?
            """,
            (memory.id,),
        ).fetchone()
        legacy = conn.execute("SELECT data FROM memories WHERE id = ?", (memory.id,)).fetchone()
        legacy_payload = json.loads(legacy["data"])
        legacy_payload.update(
            {
                "state": "active",
                "source": "user",
                "user_confirmed": True,
                "expires_at": "",
                "content_envelope": None,
            }
        )
        conn.execute(
            "UPDATE memories SET data = ? WHERE id = ?",
            (json.dumps(legacy_payload), memory.id),
        )

    listed = client.get("/api/memories")
    recalled = client.post("/api/memories/recall", json={"query": "needs review"})

    assert normalized is not None
    assert normalized["state"] == "quarantined"
    assert normalized["source"] == "OrchestratorAgent"
    assert normalized["user_confirmed"] == 0
    assert normalized["expires_at"]
    assert normalized["provenance_source_kind"] == "agent_message"
    assert listed.status_code == 200
    listed_memory = next(item for item in listed.json() if item["id"] == memory.id)
    assert listed_memory["state"] == "quarantined"
    assert listed_memory["source"] == "OrchestratorAgent"
    assert listed_memory["user_confirmed"] is False
    assert listed_memory["content_envelope"]["source_kind"] == "agent_message"
    assert recalled.status_code == 200 and recalled.json() == []

    promoted = client.post(f"/api/memories/{memory.id}/promote", json={"reviewed_by": "desktop-user"})
    assert promoted.status_code == 200
    with db.connect() as conn:
        promoted_row = conn.execute(
            "SELECT state, user_confirmed, reviewed_at, reviewed_by FROM memory_quarantine WHERE memory_id = ?",
            (memory.id,),
        ).fetchone()
    assert promoted_row["state"] == "active"
    assert promoted_row["user_confirmed"] == 1
    assert promoted_row["reviewed_at"]
    assert promoted_row["reviewed_by"] == "desktop-user"

    deleted = client.delete(f"/api/memories/{memory.id}")
    assert deleted.status_code == 200
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM memory_quarantine WHERE memory_id = ?",
                (memory.id,),
            ).fetchone()
            is None
        )
