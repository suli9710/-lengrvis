from __future__ import annotations

from pathlib import Path

import pytest

from app.core import db
from app.tools import cluster_tools, vision_tools
from app.tools.registry import register_all_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


def _write_fixture_image(
    path: Path,
    *,
    description: str,
    scene_type: str,
    people_count: int,
    captured_at: str | None = None,
    gps: tuple[float, float] | None = None,
) -> None:
    from PIL import Image, PngImagePlugin

    image = Image.new("RGB", (16, 16), "steelblue")
    info = PngImagePlugin.PngInfo()
    info.add_text("marvis_description", description)
    info.add_text("marvis_scene_type", scene_type)
    info.add_text("marvis_people_count", str(people_count))
    if captured_at:
        info.add_text("marvis_captured_at", captured_at)
    if gps:
        info.add_text("marvis_gps_latitude", str(gps[0]))
        info.add_text("marvis_gps_longitude", str(gps[1]))
    image.save(path, pnginfo=info)


def _semantic_image_embedder(texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        lowered = text.lower()
        vectors.append(
            [
                float(any(term in lowered for term in ("beach", "ocean", "sand", "surf"))),
                float(any(term in lowered for term in ("office", "desk", "laptop", "screen"))),
                1.0,
            ]
        )
    return vectors


def _cluster_for_path(result: dict, name: str) -> int:
    for cluster in result["clusters"]:
        for image in cluster["images"]:
            if Path(image["path"]).name == name:
                return int(cluster["cluster_id"])
    raise AssertionError(f"{name} not present in image clusters")


def test_describe_image_returns_structured_labels_from_local_metadata(tmp_path: Path):
    image = tmp_path / "beach-family.png"
    _write_fixture_image(
        image,
        description="Two people on a beach with ocean, umbrellas, and picnic towels.",
        scene_type="beach",
        people_count=2,
        captured_at="2024:05:01 10:00:00",
        gps=(37.7749, -122.4194),
    )

    result = vision_tools.describe_image({"path": str(image)}, {"allowed_directories": [str(tmp_path)]})

    assert result["ok"] is True
    assert result["structured_labels"]["people_count"] == 2
    assert result["structured_labels"]["scene_type"] == "beach"
    assert {"ocean", "umbrella"} & set(result["structured_labels"]["visible_objects"])
    assert result["metadata"]["captured_at"].startswith("2024-05-01")
    assert result["metadata"]["gps"]["latitude"] == pytest.approx(37.7749)


def test_cluster_images_uses_semantic_labels_and_exif_context(tmp_path: Path):
    _write_fixture_image(
        tmp_path / "sf-beach-1.png",
        description="Two people on a beach with ocean, sand, umbrellas, and picnic towels.",
        scene_type="beach",
        people_count=2,
        captured_at="2024:05:01 09:00:00",
        gps=(37.7749, -122.4194),
    )
    _write_fixture_image(
        tmp_path / "sf-beach-2.png",
        description="A small group at the beach beside the ocean and umbrellas.",
        scene_type="beach",
        people_count=3,
        captured_at="2024:05:01 11:00:00",
        gps=(37.7750, -122.4195),
    )
    _write_fixture_image(
        tmp_path / "tokyo-beach.png",
        description="Two people on a beach with ocean, sand, umbrellas, and picnic towels.",
        scene_type="beach",
        people_count=2,
        captured_at="2024:06:15 09:00:00",
        gps=(35.6762, 139.6503),
    )
    _write_fixture_image(
        tmp_path / "office.png",
        description="One person working in an office with a laptop, desk, and screen.",
        scene_type="office",
        people_count=1,
        captured_at="2024:05:01 10:00:00",
        gps=(37.7749, -122.4194),
    )

    result = cluster_tools.cluster_images(
        {"k": 3},
        {"allowed_directories": [str(tmp_path)], "embedder": _semantic_image_embedder},
    )

    assert result["ok"] is True
    assert result["count"] == 3
    assert _cluster_for_path(result, "sf-beach-1.png") == _cluster_for_path(result, "sf-beach-2.png")
    assert _cluster_for_path(result, "sf-beach-1.png") != _cluster_for_path(result, "tokyo-beach.png")
    assert _cluster_for_path(result, "sf-beach-1.png") != _cluster_for_path(result, "office.png")
    assert all("structured_labels" in image for cluster in result["clusters"] for image in cluster["images"])


def test_image_cluster_tool_is_registered_for_file_agent():
    registry = register_all_tools(load_skills=False)
    tool = registry.get("image.cluster_images")

    assert tool.agent_owner == "FileAgent"
