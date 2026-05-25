from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.indexer.fts_index import FTSIndex
from app.indexer.vector_index import VectorIndex
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))


def semantic_embedder(texts: list[str]) -> list[list[float]]:
    return [_semantic_vector(text) for text in texts]


def _semantic_vector(text: str) -> list[float]:
    lowered = text.lower()
    groups = [
        {"car", "cars", "vehicle", "vehicles", "automobile", "engine", "road", "garage"},
        {"recipe", "recipes", "sourdough", "bread", "starter", "flour", "baking", "oven"},
        {"cat", "cats", "feline", "kitten", "purring", "whiskers"},
        {"budget", "invoice", "invoices", "finance", "payment"},
    ]
    vector = []
    for terms in groups:
        vector.append(float(sum(1 for term in terms if term in lowered)))
    vector.append(1.0)
    return vector


def test_rebuild_persists_embeddings_and_semantic_search_reranks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "garage.txt").write_text(
        "Garage maintenance notes for car engines, road tires, and vehicle repairs.",
        encoding="utf-8",
    )
    (workspace / "kitchen.txt").write_text(
        "Kitchen notebook about sourdough starter, bread recipes, flour, and oven timing.",
        encoding="utf-8",
    )
    (workspace / "pets.txt").write_text(
        "A quiet feline care note about cats, kittens, purring, and whiskers.",
        encoding="utf-8",
    )

    rebuild = FTSIndex(embedder=semantic_embedder, embedding_batch_size=2).rebuild([str(workspace)])

    assert rebuild["files_indexed"] == 3
    assert rebuild["chunks_indexed"] == 3
    assert rebuild["embeddings_indexed"] == 3
    with db.connect() as conn:
        persisted = conn.execute("SELECT COUNT(*) AS count FROM document_chunk_embeddings").fetchone()["count"]
    assert persisted == 3

    result = VectorIndex(embedder=semantic_embedder).search("automobile service", limit=3)

    assert result["source"] in {"fts_vector_rerank", "vector_scan"}
    assert result["count"] == 3
    assert result["results"][0]["name"] == "garage.txt"
    assert result["results"][0]["vector_score"] > result["results"][1]["vector_score"]


def test_semantic_search_route_no_longer_returns_reserved_placeholder(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "garage.txt").write_text("Vehicle engine and automobile repair notes.", encoding="utf-8")
    (workspace / "bread.txt").write_text("Sourdough bread recipe and starter schedule.", encoding="utf-8")
    (workspace / "cat.txt").write_text("Feline kitten care and cat toys.", encoding="utf-8")

    FTSIndex(embedder=semantic_embedder).rebuild([str(workspace)])
    client = TestClient(create_app())

    response = client.get("/api/files/semantic-search", params={"q": "automobile"})

    assert response.status_code == 200
    payload = response.json()
    assert "reserved" not in str(payload).lower()
    assert payload["results"][0]["name"] == "garage.txt"


def test_semantic_search_uses_fts_candidates_before_rerank(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "slow-car.txt").write_text(
        "Ticket says repair. The customer describes an automobile problem and engine noise.",
        encoding="utf-8",
    )
    (workspace / "repair-bread.txt").write_text(
        "Ticket says repair. This note is only about sourdough starter and oven calibration.",
        encoding="utf-8",
    )
    (workspace / "repair-cat.txt").write_text(
        "Ticket says repair. This note is only about feline whiskers and kitten play.",
        encoding="utf-8",
    )

    FTSIndex(embedder=semantic_embedder).rebuild([str(workspace)])
    result = VectorIndex(embedder=semantic_embedder).search("repair automobile", limit=3)

    assert result["source"] == "fts_vector_rerank"
    assert result["candidate_count"] >= 3
    assert result["results"][0]["name"] == "slow-car.txt"


def test_bounded_1000_document_indexing_perf(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(1000):
        topic = "vehicle automobile garage" if index == 777 else "sourdough recipe kitchen"
        (workspace / f"doc-{index:04d}.txt").write_text(
            f"{topic}. Small document {index} for bounded semantic indexing performance.",
            encoding="utf-8",
        )

    rebuild = FTSIndex(embedder=semantic_embedder, embedding_batch_size=128).rebuild([str(workspace)])
    result = VectorIndex(embedder=semantic_embedder).search("automobile garage", limit=5, scan_limit=1000)

    assert rebuild["files_indexed"] == 1000
    assert rebuild["embeddings_indexed"] == 1000
    assert rebuild["elapsed_seconds"] >= 0
    assert result["results"][0]["name"] == "doc-0777.txt"


def test_vector_scan_is_bounded_to_1000_chunks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(1005):
        (workspace / f"doc-{index:04d}.txt").write_text(
            f"Generic document {index} about bread recipes.",
            encoding="utf-8",
        )
    FTSIndex(embedder=semantic_embedder, embedding_batch_size=128).rebuild([str(workspace)])

    result = VectorIndex(embedder=semantic_embedder).search("spaceship orbit", scan_limit=1000)

    assert result["source"] == "vector_scan"
    assert result["candidate_count"] == 1000
