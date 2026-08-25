from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.config import AppSettings
from app.core.schemas import ToolResult
from app.orchestration.result_budget import (
    FULL_RESULT_REVIEW_MARKER,
    discard_large_result_artifact,
    large_result_artifact_path,
    persist_large_result,
    reviewed_large_result_artifact_valid,
)

TASK_ID = "task_0123456789abcdef0123456789abcdef"
RESULT_ID = "result_0123456789abcdef0123456789abcdef"


def test_large_result_artifact_name_is_short_deterministic_and_tool_bound(tmp_path: Path) -> None:
    long_tool_name = "plugin." + ("very_long_tool_name." * 30)

    path = large_result_artifact_path(tmp_path, TASK_ID, RESULT_ID, long_tool_name)

    assert path == large_result_artifact_path(tmp_path, TASK_ID, RESULT_ID, long_tool_name)
    assert path != large_result_artifact_path(tmp_path, TASK_ID, RESULT_ID, f"{long_tool_name}.other")
    assert path.name.startswith("r-")
    assert path.suffix == ".json"
    assert len(path.name) == 39
    assert RESULT_ID not in path.name
    assert long_tool_name[:20] not in path.name


def test_large_result_persistence_stays_below_legacy_win32_path_limit(tmp_path: Path) -> None:
    data_dir = tmp_path
    long_tool_name = "plugin." + ("x" * 400)
    while True:
        canonical = large_result_artifact_path(data_dir, TASK_ID, RESULT_ID, long_tool_name)
        legacy = canonical.parent / f"{RESULT_ID}_{long_tool_name[:80]}.json"
        if len(str(legacy)) >= 260:
            break
        data_dir /= "long-data-root"

    assert len(str(canonical)) <= 240
    assert len(str(legacy)) >= 260
    reference = persist_large_result(
        AppSettings(data_dir=str(data_dir)),
        TASK_ID,
        RESULT_ID,
        long_tool_name,
        "reviewed content",
    )

    artifact = Path(reference.path)
    assert artifact == canonical
    assert artifact.read_text(encoding="utf-8") == "reviewed content"


def test_large_result_persistence_keeps_exclusive_creation(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=str(tmp_path))

    first = persist_large_result(settings, TASK_ID, RESULT_ID, "tool.read", "first")

    with pytest.raises(FileExistsError):
        persist_large_result(settings, TASK_ID, RESULT_ID, "tool.read", "replacement")
    assert Path(first.path).read_text(encoding="utf-8") == "first"


def test_reviewed_artifact_validator_accepts_only_canonical_v2_path(tmp_path: Path) -> None:
    tool_name = "tool.read"
    content = "reviewed content"
    reference = persist_large_result(
        AppSettings(data_dir=str(tmp_path)),
        TASK_ID,
        RESULT_ID,
        tool_name,
        content,
    )
    result = ToolResult(
        id=RESULT_ID,
        tool_call_id="tool_call_0123456789abcdef0123456789abcdef",
        ok=True,
        output={
            "persisted_result": True,
            "path": reference.path,
            "artifact_sha256": f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}",
            "artifact_size_bytes": len(content.encode("utf-8")),
            FULL_RESULT_REVIEW_MARKER: True,
        },
        runtime_review_id="review_0123456789abcdef0123456789abcdef",
        runtime_review_verdict="allow",
        runtime_review_completed=True,
    )

    assert reviewed_large_result_artifact_valid(
        result,
        data_dir=tmp_path,
        task_id=TASK_ID,
        tool_name=tool_name,
    )

    legacy = Path(reference.path).parent / f"{RESULT_ID}_{tool_name}.json"
    legacy.write_text(content, encoding="utf-8")
    legacy_result = result.model_copy(update={"output": {**result.output, "path": str(legacy)}}, deep=True)
    assert not reviewed_large_result_artifact_valid(
        legacy_result,
        data_dir=tmp_path,
        task_id=TASK_ID,
        tool_name=tool_name,
    )


def test_large_result_cleanup_removes_canonical_and_legacy_identity_paths(tmp_path: Path) -> None:
    tool_name = "tool.read"
    canonical = large_result_artifact_path(tmp_path, TASK_ID, RESULT_ID, tool_name)
    canonical.parent.mkdir(parents=True)
    canonical.write_text("canonical", encoding="utf-8")
    legacy = canonical.parent / f"{RESULT_ID}_{tool_name}.json"
    legacy.write_text("legacy", encoding="utf-8")
    unrelated = canonical.parent / f"{RESULT_ID}_tool.other.json"
    unrelated.write_text("keep", encoding="utf-8")

    assert discard_large_result_artifact(tmp_path, TASK_ID, RESULT_ID, tool_name)
    assert not canonical.exists()
    assert not legacy.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
