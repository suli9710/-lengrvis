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
    assert payload["principal_id"] == "local-user"
    assert payload["workspace_id"] == "default"
    assert payload["domain_scope"] == "general"
    assert payload["version"] == 1
    assert payload["supersedes"] == ""
    assert payload["conflict_status"] == "none"


def test_memory_api_isolates_namespaces_and_scopes_id_based_operations(client: TestClient) -> None:
    finance = {
        "principal_id": "alice",
        "workspace_id": "northwind",
        "domain_scope": "finance",
    }
    legal = {**finance, "domain_scope": "legal"}
    memory = asyncio.run(
        routes_memories._agent().remember(
            "Finance filing rule awaiting confirmation",
            source="PlannerAgent",
            user_confirmed=False,
            **finance,
        )
    )
    legal_memory = client.post(
        "/api/memories",
        json={"content": "Legal filing rule", **legal},
    ).json()

    finance_before = client.post("/api/memories/recall", json={"query": "filing rule", **finance})
    legal_recall = client.post("/api/memories/recall", json={"query": "filing rule", **legal})
    wrong_promote = client.post(f"/api/memories/{memory.id}/promote", json=legal)
    wrong_revoke = client.post(f"/api/memories/{memory.id}/revoke", json=legal)
    wrong_delete = client.delete(f"/api/memories/{memory.id}", params=legal)
    promoted = client.post(f"/api/memories/{memory.id}/promote", json=finance)
    finance_after = client.post("/api/memories/recall", json={"query": "filing rule", **finance})

    assert finance_before.status_code == 200 and finance_before.json() == []
    assert [item["id"] for item in legal_recall.json()] == [legal_memory["id"]]
    assert wrong_promote.status_code == 404
    assert wrong_revoke.status_code == 404
    assert wrong_delete.status_code == 404
    assert promoted.status_code == 200
    assert [item["id"] for item in finance_after.json()] == [memory.id]

    deleted = client.delete(f"/api/memories/{memory.id}", params=finance)
    assert deleted.status_code == 200
    with db.connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM memory_namespace WHERE memory_id = ?",
            (memory.id,),
        ).fetchone() is None


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
