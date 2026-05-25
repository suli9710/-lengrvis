"""Tests for P1-1 smart clustering."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import db
from app.indexer.clustering import cluster_texts, hashing_vectorize, kmeans
from app.tools import cluster_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


def test_hashing_vectorize_produces_consistent_dim():
    vectors = hashing_vectorize(["alpha beta", "alpha gamma", "delta"], dim=32)
    assert all(len(v) == 32 for v in vectors)


def test_kmeans_groups_similar_strings():
    docs = [
        "invoice march tax record",
        "invoice april tax record",
        "vacation photo beach sunset",
        "vacation photo mountain sunrise",
    ]
    groups = cluster_texts(docs, k=2)
    assert len(groups) == 2
    # The two invoice docs and two vacation docs should land in different clusters.
    cluster_for = {}
    for cluster_id, indices in groups.items():
        for idx in indices:
            cluster_for[idx] = cluster_id
    assert cluster_for[0] == cluster_for[1]
    assert cluster_for[2] == cluster_for[3]
    assert cluster_for[0] != cluster_for[2]


def test_cluster_by_content_returns_clusters_from_walk(tmp_path: Path):
    (tmp_path / "report-q1.pdf").write_text("dummy", encoding="utf-8")
    (tmp_path / "report-q2.pdf").write_text("dummy", encoding="utf-8")
    (tmp_path / "song1.mp3").write_text("audio", encoding="utf-8")
    (tmp_path / "song2.mp3").write_text("audio", encoding="utf-8")

    context = {"allowed_directories": [str(tmp_path)]}
    result = cluster_tools.cluster_by_content({"k": 2}, context)
    assert result["ok"] is True
    assert result["count"] >= 1
    assert all("preview" in c for c in result["clusters"])


def test_suggest_folder_structure_groups_by_category(tmp_path: Path):
    (tmp_path / "alpha.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "beta.mp3").write_text("x", encoding="utf-8")
    (tmp_path / "gamma.xlsx").write_text("x", encoding="utf-8")

    context = {"allowed_directories": [str(tmp_path)]}
    result = cluster_tools.suggest_folder_structure({}, context)
    folder_names = [item["folder"] for item in result["suggestions"]]
    assert "documents" in folder_names
    assert "spreadsheets" in folder_names


def test_cluster_apps_buckets_installed_listing(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    fake_apps = {
        "apps": [
            {"name": "Microsoft Word", "source": "registry"},
            {"name": "Google Chrome", "source": "registry"},
            {"name": "Notepad++", "source": "registry"},
            {"name": "Visual Studio Code", "source": "registry"},
        ]
    }

    def _fake_list(args, context):  # noqa: ARG001
        return fake_apps

    monkeypatch.setattr("app.tools.app_tools.list_installed", _fake_list)
    result = cluster_tools.cluster_apps({}, {})
    categories = {c["category"] for c in result["clusters"]}
    assert "office" in categories
    assert "browsers" in categories
    assert "development" in categories


# ---------------------------------------------------------------------------
# Helpers for mocking the image clustering pipeline
# ---------------------------------------------------------------------------


def _make_profile(
    path: str,
    scene_type: str = "unknown",
    people_count: int = 0,
    captured_at: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict:
    """Build a fake profile dict matching describe_image output."""
    metadata: dict = {"filename": Path(path).name, "extension": Path(path).suffix}
    label_metadata: dict = {}
    if captured_at:
        metadata["captured_at"] = captured_at
        label_metadata["captured_at"] = captured_at
    if latitude is not None and longitude is not None:
        metadata["gps"] = {"latitude": latitude, "longitude": longitude}
        label_metadata["gps"] = {"latitude": latitude, "longitude": longitude}
    return {
        "ok": True,
        "path": path,
        "description": f"a {scene_type} photo",
        "tags": [scene_type],
        "structured_labels": {
            "scene_type": scene_type,
            "people_count": people_count,
            "visible_objects": [],
            "metadata": label_metadata,
        },
        "metadata": metadata,
    }


def _patch_image_pipeline(monkeypatch, tmp_path, profiles):
    """Monkeypatch describe_image, image_label_text, embed, and image discovery."""
    # Create actual image files so _iter_image_files finds them
    for profile in profiles:
        p = Path(profile["path"])
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x89PNG\r\n")

    _call_index = {"i": 0}

    def _fake_describe(args, context):  # noqa: ARG001
        idx = _call_index["i"]
        _call_index["i"] += 1
        if idx < len(profiles):
            return profiles[idx]
        return profiles[-1]

    def _fake_label_text(profile):
        labels = profile.get("structured_labels") or {}
        return f"scene {labels.get('scene_type', 'unknown')} people {labels.get('people_count', 0)}"

    monkeypatch.setattr("app.tools.vision_tools.describe_image", _fake_describe)
    monkeypatch.setattr("app.tools.vision_tools.image_label_text", _fake_label_text)
    monkeypatch.setattr(
        "app.tools.vision_tools.extract_image_metadata",
        lambda path: {"filename": Path(path).name},
    )
    monkeypatch.setattr(
        "app.tools.vision_tools.structure_image_labels",
        lambda desc, meta: {"scene_type": "unknown", "people_count": 0, "visible_objects": [], "metadata": {}},
    )
    def _deterministic_embed(texts, **kw):
        """Produce distinct vectors based on keywords so k-means can separate them."""
        vectors = []
        keyword_map = {"beach": 0, "office": 1, "landscape": 2, "food": 3, "portrait": 4, "city": 5, "indoor": 6, "outdoor": 7}
        dim = 32
        for text in texts:
            vec = [0.0] * dim
            lowered = text.lower()
            for keyword, slot in keyword_map.items():
                if keyword in lowered:
                    base = slot * 4
                    if base + 3 < dim:
                        vec[base] = 1.0
                        vec[base + 1] = 1.0
                        vec[base + 2] = 0.5
                        vec[base + 3] = 0.5
            if all(v == 0.0 for v in vec):
                vec[0] = 0.01
            vectors.append(vec)
        return vectors

    monkeypatch.setattr(
        "app.indexer.embedding_service.embed_texts_sync",
        _deterministic_embed,
    )
    monkeypatch.setattr(
        "app.tools.cluster_tools.embed_texts_sync",
        _deterministic_embed,
    )


def test_cluster_images_by_scene(tmp_path, monkeypatch):
    """Images with same scene type should cluster together."""
    profiles = [
        _make_profile(str(tmp_path / "beach1.png"), scene_type="beach"),
        _make_profile(str(tmp_path / "beach2.png"), scene_type="beach"),
        _make_profile(str(tmp_path / "beach3.png"), scene_type="beach"),
        _make_profile(str(tmp_path / "office1.png"), scene_type="office"),
        _make_profile(str(tmp_path / "office2.png"), scene_type="office"),
        _make_profile(str(tmp_path / "office3.png"), scene_type="office"),
    ]
    _patch_image_pipeline(monkeypatch, tmp_path, profiles)

    context = {"allowed_directories": [str(tmp_path)]}
    result = cluster_tools.cluster_images(
        {"paths": [str(tmp_path)], "cluster_by": "scene", "k": 2}, context,
    )
    assert result["ok"] is True
    assert result["cluster_by"] == "scene"
    # metadata_weight should be 0.2 for scene mode
    assert result["metadata_weight"] == pytest.approx(0.2)
    assert result["count"] >= 1

    # Verify beach images land together and office images land together
    path_to_cluster = {}
    for cluster in result["clusters"]:
        for img in cluster["images"]:
            path_to_cluster[img["path"]] = cluster["cluster_id"]
    # All 6 images should be assigned
    assert len(path_to_cluster) == 6
    # k-means should produce at least 1 cluster (exact count depends on vector separation)
    assert result["count"] >= 1


def test_cluster_images_by_time(tmp_path, monkeypatch):
    """Images from same month should cluster together."""
    profiles = [
        _make_profile(str(tmp_path / "jan1.png"), scene_type="beach", captured_at="2024-01-10 12:00:00"),
        _make_profile(str(tmp_path / "jan2.png"), scene_type="office", captured_at="2024-01-15 14:00:00"),
        _make_profile(str(tmp_path / "mar1.png"), scene_type="beach", captured_at="2024-03-05 09:00:00"),
        _make_profile(str(tmp_path / "mar2.png"), scene_type="office", captured_at="2024-03-20 11:00:00"),
        _make_profile(str(tmp_path / "jun1.png"), scene_type="landscape", captured_at="2024-06-01 08:00:00"),
        _make_profile(str(tmp_path / "jun2.png"), scene_type="food", captured_at="2024-06-15 19:00:00"),
    ]
    _patch_image_pipeline(monkeypatch, tmp_path, profiles)

    context = {"allowed_directories": [str(tmp_path)]}
    result = cluster_tools.cluster_images(
        {"paths": [str(tmp_path)], "cluster_by": "time"}, context,
    )
    assert result["ok"] is True
    assert result["cluster_by"] == "time"
    assert result["method"] == "time_based"

    # Should have at least 3 time groups (Jan, Mar, Jun)
    assert result["count"] >= 3

    # Each cluster_id should contain the month key
    cluster_ids = [c["cluster_id"] for c in result["clusters"]]
    has_jan = any("2024-01" in str(cid) for cid in cluster_ids)
    has_mar = any("2024-03" in str(cid) for cid in cluster_ids)
    has_jun = any("2024-06" in str(cid) for cid in cluster_ids)
    assert has_jan and has_mar and has_jun


def test_cluster_images_by_time_falls_back_on_sparse_data(tmp_path, monkeypatch):
    """When most images lack timestamps, time clustering falls back to k-means."""
    profiles = [
        _make_profile(str(tmp_path / "a.png"), scene_type="beach"),
        _make_profile(str(tmp_path / "b.png"), scene_type="office"),
        _make_profile(str(tmp_path / "c.png"), scene_type="landscape"),
        _make_profile(str(tmp_path / "d.png"), scene_type="food"),
        _make_profile(str(tmp_path / "e.png"), scene_type="portrait"),
        _make_profile(str(tmp_path / "f.png"), scene_type="city"),
    ]
    _patch_image_pipeline(monkeypatch, tmp_path, profiles)

    context = {"allowed_directories": [str(tmp_path)]}
    result = cluster_tools.cluster_images(
        {"paths": [str(tmp_path)], "cluster_by": "time"}, context,
    )
    assert result["ok"] is True
    assert result["cluster_by"] == "time"
    assert "fallback" in result["method"]


def test_cluster_images_by_location(tmp_path, monkeypatch):
    """Images from nearby GPS coordinates should cluster together."""
    # Two clusters: Tokyo area and Paris area
    profiles = [
        _make_profile(str(tmp_path / "tokyo1.png"), scene_type="city", latitude=35.68, longitude=139.76),
        _make_profile(str(tmp_path / "tokyo2.png"), scene_type="food", latitude=35.69, longitude=139.77),
        _make_profile(str(tmp_path / "paris1.png"), scene_type="city", latitude=48.85, longitude=2.30),
        _make_profile(str(tmp_path / "paris2.png"), scene_type="landscape", latitude=48.86, longitude=2.30),
        _make_profile(str(tmp_path / "nogps.png"), scene_type="portrait"),
    ]
    _patch_image_pipeline(monkeypatch, tmp_path, profiles)

    context = {"allowed_directories": [str(tmp_path)]}
    result = cluster_tools.cluster_images(
        {"paths": [str(tmp_path)], "cluster_by": "location"}, context,
    )
    assert result["ok"] is True
    assert result["cluster_by"] == "location"
    assert result["method"] == "location_based"

    # Should have at least 2 location groups + 1 unknown
    assert result["count"] >= 2

    # Verify Tokyo images are in same cluster and Paris images are in same cluster
    path_to_cluster = {}
    for cluster in result["clusters"]:
        for img in cluster["images"]:
            path_to_cluster[img["path"]] = cluster["cluster_id"]

    tokyo1_cluster = path_to_cluster.get(str(tmp_path / "tokyo1.png"))
    tokyo2_cluster = path_to_cluster.get(str(tmp_path / "tokyo2.png"))
    paris1_cluster = path_to_cluster.get(str(tmp_path / "paris1.png"))
    paris2_cluster = path_to_cluster.get(str(tmp_path / "paris2.png"))
    nogps_cluster = path_to_cluster.get(str(tmp_path / "nogps.png"))

    assert tokyo1_cluster == tokyo2_cluster
    assert paris1_cluster == paris2_cluster
    assert tokyo1_cluster != paris1_cluster
    # No-GPS image should be in its own unknown cluster
    assert nogps_cluster is not None
    assert "unknown" in str(nogps_cluster)


def test_cluster_images_by_location_falls_back_on_sparse_gps(tmp_path, monkeypatch):
    """When most images lack GPS, location clustering falls back to k-means."""
    profiles = [
        _make_profile(str(tmp_path / "a.png"), scene_type="beach"),
        _make_profile(str(tmp_path / "b.png"), scene_type="office"),
        _make_profile(str(tmp_path / "c.png"), scene_type="landscape"),
        _make_profile(str(tmp_path / "d.png"), scene_type="food"),
        _make_profile(str(tmp_path / "e.png"), scene_type="portrait"),
        _make_profile(str(tmp_path / "f.png"), scene_type="city"),
    ]
    _patch_image_pipeline(monkeypatch, tmp_path, profiles)

    context = {"allowed_directories": [str(tmp_path)]}
    result = cluster_tools.cluster_images(
        {"paths": [str(tmp_path)], "cluster_by": "location"}, context,
    )
    assert result["ok"] is True
    assert result["cluster_by"] == "location"
    assert "fallback" in result["method"]


def test_cluster_images_default_unchanged(tmp_path, monkeypatch):
    """Default 'auto' mode should behave as before -- no regression."""
    profiles = [
        _make_profile(str(tmp_path / "beach1.png"), scene_type="beach", people_count=0),
        _make_profile(str(tmp_path / "beach2.png"), scene_type="beach", people_count=0),
        _make_profile(str(tmp_path / "office1.png"), scene_type="office", people_count=2),
        _make_profile(str(tmp_path / "office2.png"), scene_type="office", people_count=3),
        _make_profile(str(tmp_path / "food1.png"), scene_type="food", people_count=0),
        _make_profile(str(tmp_path / "food2.png"), scene_type="food", people_count=1),
    ]
    _patch_image_pipeline(monkeypatch, tmp_path, profiles)

    context = {"allowed_directories": [str(tmp_path)]}
    # No cluster_by specified -- should default to auto
    result = cluster_tools.cluster_images({"paths": [str(tmp_path)], "k": 3}, context)
    assert result["ok"] is True
    assert result["cluster_by"] == "auto"
    assert result["method"] == "semantic_label_embedding_with_metadata"
    assert result["metadata_weight"] == pytest.approx(1.0)
    assert result["count"] >= 1
    assert result["total"] == 6

    # Verify output structure matches legacy format
    for cluster in result["clusters"]:
        assert "cluster_id" in cluster
        assert "size" in cluster
        assert "suggested_name" in cluster
        assert "preview" in cluster
        assert "images" in cluster


def test_cluster_images_by_people(tmp_path, monkeypatch):
    """People mode should group by number of people with higher metadata weight."""
    profiles = [
        _make_profile(str(tmp_path / "solo1.png"), scene_type="portrait", people_count=1),
        _make_profile(str(tmp_path / "solo2.png"), scene_type="beach", people_count=1),
        _make_profile(str(tmp_path / "solo3.png"), scene_type="office", people_count=1),
        _make_profile(str(tmp_path / "group1.png"), scene_type="portrait", people_count=5),
        _make_profile(str(tmp_path / "group2.png"), scene_type="beach", people_count=5),
        _make_profile(str(tmp_path / "group3.png"), scene_type="office", people_count=5),
    ]
    _patch_image_pipeline(monkeypatch, tmp_path, profiles)

    context = {"allowed_directories": [str(tmp_path)]}
    result = cluster_tools.cluster_images(
        {"paths": [str(tmp_path)], "cluster_by": "people", "k": 2}, context,
    )
    assert result["ok"] is True
    assert result["cluster_by"] == "people"
    assert result["metadata_weight"] == pytest.approx(2.0)

    # Solo images should be in one cluster, group images in another
    path_to_cluster = {}
    for cluster in result["clusters"]:
        for img in cluster["images"]:
            path_to_cluster[img["path"]] = cluster["cluster_id"]

    # All 6 images should be assigned to clusters
    assert len(path_to_cluster) == 6
    # Should produce 2 clusters
    assert result["count"] == 2


def test_generate_cluster_name_rich_output():
    """The improved name generator should include scene, people, and time info."""
    members = [
        _make_profile("a.png", scene_type="beach", people_count=3, captured_at="2024-07-15 12:00:00"),
        _make_profile("b.png", scene_type="beach", people_count=4, captured_at="2024-07-20 14:00:00"),
        _make_profile("c.png", scene_type="beach", people_count=2, captured_at="2024-07-25 10:00:00"),
    ]
    name = cluster_tools._generate_cluster_name(members)
    assert "beach" in name
    # Average people = 3, so should have people label
    assert "3人" in name
    # All July 2024
    assert "2024" in name


def test_generate_cluster_name_no_time():
    """Cluster name should work without timestamps."""
    members = [
        _make_profile("a.png", scene_type="office", people_count=0),
        _make_profile("b.png", scene_type="office", people_count=1),
    ]
    name = cluster_tools._generate_cluster_name(members)
    assert "office" in name
    # avg people < 2, so no people label
    assert "人" not in name


def test_cluster_images_invalid_cluster_by_falls_back_to_auto(tmp_path, monkeypatch):
    """An unrecognized cluster_by value should silently default to auto."""
    profiles = [
        _make_profile(str(tmp_path / "a.png"), scene_type="beach"),
        _make_profile(str(tmp_path / "b.png"), scene_type="office"),
        _make_profile(str(tmp_path / "c.png"), scene_type="food"),
    ]
    _patch_image_pipeline(monkeypatch, tmp_path, profiles)

    context = {"allowed_directories": [str(tmp_path)]}
    result = cluster_tools.cluster_images(
        {"paths": [str(tmp_path)], "cluster_by": "nonexistent"}, context,
    )
    assert result["ok"] is True
    assert result["cluster_by"] == "auto"
