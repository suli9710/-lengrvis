from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.acceleration import onnx_sessions
from app.core import db
from app.core.schemas import PlanStep, Task, ToolResult
from app.indexer.fts_index import FTSIndex
from app.llm.registry import invalidate_settings_cache
from app.orchestration.execution_engine import InMemoryRunStore, RunNotFoundError
from app.orchestration.execution_models import LargeResultRef, RunObservation, RunPhase, RunState
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.os_execution_engine import OSExecutionEngine
from app.policy.risk import RiskLevel


def test_db_connect_reuses_thread_connection_for_same_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.close_thread_connection()
    try:
        with db.connect() as first:
            with db.connect() as second:
                assert second is first
    finally:
        db.close_thread_connection()


def test_reset_init_db_cache_closes_reused_connection(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.close_thread_connection()
    try:
        with db.connect() as first:
            first.execute("SELECT 1")

        db.reset_init_db_cache()

        with pytest.raises(sqlite3.ProgrammingError):
            first.execute("SELECT 1")
        with db.connect() as second:
            assert second is not first
    finally:
        db.close_thread_connection()


def test_onnx_session_cache_evicts_least_recent(monkeypatch, tmp_path: Path) -> None:
    class FakeOrt:
        created: list[str] = []

        class InferenceSession:
            def __init__(self, model_path: str, providers: list[object]) -> None:
                FakeOrt.created.append(model_path)
                self.model_path = model_path
                self.providers = providers

    monkeypatch.setenv("LENGRVIS_ONNX_SESSION_CACHE_MAX_ENTRIES", "2")
    monkeypatch.setattr(onnx_sessions, "import_onnxruntime", lambda: FakeOrt)
    onnx_sessions.clear_session_cache()
    try:
        backends = [
            onnx_sessions.OnnxSessionBackend(
                kind="onnx",
                model_path=str(tmp_path / f"model-{index}.onnx"),
                execution_provider=onnx_sessions.CPU_PROVIDER,
                available_providers=[onnx_sessions.CPU_PROVIDER],
            )
            for index in range(3)
        ]

        first = onnx_sessions.create_inference_session(backends[0])
        second = onnx_sessions.create_inference_session(backends[1])
        assert onnx_sessions.create_inference_session(backends[0]) is first

        third = onnx_sessions.create_inference_session(backends[2])

        assert third is not second
        assert onnx_sessions.create_inference_session(backends[1]) is not second
        assert FakeOrt.created == [
            backends[0].model_path,
            backends[1].model_path,
            backends[2].model_path,
            backends[1].model_path,
        ]
    finally:
        onnx_sessions.clear_session_cache()


def test_onnx_provider_probe_and_import_failures_are_narrow(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import_onnxruntime = onnx_sessions.import_onnxruntime

    class RuntimeFailingOrt:
        @staticmethod
        def get_available_providers():
            raise RuntimeError("provider probe failed")

    monkeypatch.setattr(onnx_sessions, "import_onnxruntime", lambda: RuntimeFailingOrt)
    assert onnx_sessions.available_execution_providers() == []

    class BuggyOrt:
        @staticmethod
        def get_available_providers():
            raise AssertionError("provider probe bug")

    monkeypatch.setattr(onnx_sessions, "import_onnxruntime", lambda: BuggyOrt)
    with pytest.raises(AssertionError, match="provider probe bug"):
        onnx_sessions.available_execution_providers()

    calls: list[str] = []

    def import_with_fallback(module_name: str):
        calls.append(module_name)
        if module_name == "onnxruntime_windowsml":
            raise OSError("native runtime load failed")
        if module_name == "onnxruntime_directml":
            return object()
        raise AssertionError("unexpected module")

    monkeypatch.setattr(onnx_sessions.importlib, "import_module", import_with_fallback)
    monkeypatch.setattr(onnx_sessions, "import_onnxruntime", real_import_onnxruntime)
    assert onnx_sessions.import_onnxruntime() is not None
    assert calls[:2] == ["onnxruntime_windowsml", "onnxruntime_directml"]

    def import_bug(_module_name: str):
        raise AssertionError("import hook bug")

    monkeypatch.setattr(onnx_sessions.importlib, "import_module", import_bug)
    with pytest.raises(AssertionError, match="import hook bug"):
        onnx_sessions.import_onnxruntime()


def test_onnx_session_output_name_discovery_is_narrow() -> None:
    class RuntimeFailingSession:
        def get_outputs(self):
            raise RuntimeError("native output metadata unavailable")

    assert onnx_sessions.session_output_names(RuntimeFailingSession()) == []

    class BuggySession:
        def get_outputs(self):
            raise AssertionError("test double bug")

    with pytest.raises(AssertionError, match="test double bug"):
        onnx_sessions.session_output_names(BuggySession())


def test_onnx_session_input_name_discovery_is_narrow() -> None:
    class RuntimeFailingSession:
        def get_inputs(self):
            raise RuntimeError("native input metadata unavailable")

    with pytest.raises(onnx_sessions.OnnxAccelerationUnavailable, match="native input metadata unavailable"):
        onnx_sessions.session_input_names(RuntimeFailingSession())

    class BuggySession:
        def get_inputs(self):
            raise AssertionError("input metadata bug")

    with pytest.raises(AssertionError, match="input metadata bug"):
        onnx_sessions.session_input_names(BuggySession())


def test_in_memory_run_store_evicts_least_recent_run() -> None:
    now = 0.0
    store = InMemoryRunStore(max_runs=2, ttl_seconds=100, terminal_ttl_seconds=100, clock=lambda: now)

    store.put(RunState(run_id="run_a", engine="os", phase=RunPhase.RUNNING))
    store.put(RunState(run_id="run_b", engine="os", phase=RunPhase.RUNNING))
    store.get("run_a")
    store.put(RunState(run_id="run_c", engine="os", phase=RunPhase.RUNNING))

    assert store.get("run_a").run_id == "run_a"
    assert store.get("run_c").run_id == "run_c"
    with pytest.raises(RunNotFoundError):
        store.get("run_b")
    assert len(store) == 2


def test_in_memory_run_store_expires_terminal_runs_without_read_refresh() -> None:
    now = 0.0

    def clock() -> float:
        return now

    store = InMemoryRunStore(max_runs=10, ttl_seconds=100, terminal_ttl_seconds=5, clock=clock)
    store.put(RunState(run_id="run_done", engine="os", phase=RunPhase.COMPLETED))

    now = 4.0
    assert store.get("run_done").phase == RunPhase.COMPLETED
    now = 6.0
    with pytest.raises(RunNotFoundError):
        store.get("run_done")


def test_in_memory_run_store_trims_run_state_history() -> None:
    store = InMemoryRunStore(max_observations=3, max_large_result_refs=2)
    state = RunState(
        run_id="run_history",
        engine="os",
        phase=RunPhase.RUNNING,
        observations=[RunObservation(turn=index, source="test", message=str(index)) for index in range(5)],
        large_result_refs=[LargeResultRef(ref_id=f"ref_{index}", path=f"result-{index}.json") for index in range(4)],
    )

    stored = store.put(state)
    loaded = store.get("run_history")

    assert [item.turn for item in stored.observations] == [2, 3, 4]
    assert [item.turn for item in loaded.observations] == [2, 3, 4]
    assert [item.ref_id for item in loaded.large_result_refs] == ["ref_2", "ref_3"]


@pytest.mark.asyncio
async def test_os_engine_records_only_recent_run_observations() -> None:
    store = InMemoryRunStore(max_observations=3, max_large_result_refs=3)
    engine = OSExecutionEngine(store=store)
    state = RunState(
        run_id="osrun_history",
        engine="os",
        phase=RunPhase.RUNNING,
        observations=[RunObservation(turn=index, source="old", message=str(index)) for index in range(5)],
        large_result_refs=[LargeResultRef(ref_id=f"old_ref_{index}", path=f"old-{index}.json") for index in range(3)],
    )
    task = Task(user_goal="bounded observations", mode="efficiency")
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="test.bound",
        description="Record bounded observation",
        args={},
        risk_level=RiskLevel.R0_READ_ONLY,
    )
    result = ToolResult(
        tool_call_id="call_bound",
        ok=True,
        output={
            "persisted_result": True,
            "path": "new-result.json",
            "original_size": 42,
            "preview": "new",
        },
        observation="new bounded observation",
    )

    updated = await engine._record_step_results(
        state,
        task,
        {},
        None,
        turn=9,
        step_outcomes=[(step, StepExecutionOutcome("succeeded", result))],
        observations_by_step={},
    )

    assert [item.turn for item in updated.observations] == [3, 4, 9]
    assert [item.ref_id for item in updated.large_result_refs] == ["old_ref_1", "old_ref_2", result.id]


def test_index_rebuild_limit_preserves_existing_index(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    db.close_thread_connection()
    db.reset_init_db_cache()
    invalidate_settings_cache()
    db.init_db()

    def embedder(texts: list[str]) -> list[list[float]]:
        return [[1.0] for _text in texts]

    (workspace / "kept.txt").write_text("keep this indexed document", encoding="utf-8")
    first = FTSIndex(embedder=embedder).rebuild([str(workspace)])
    assert first["files_indexed"] == 1

    (workspace / "too-many.txt").write_text("this file exceeds the configured rebuild cap", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_INDEX_REBUILD_MAX_FILES", "1")
    invalidate_settings_cache()
    second = FTSIndex(embedder=embedder).rebuild([str(workspace)])

    assert second["aborted"] is True
    assert "file limit" in second["abort_reason"]
    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM indexed_files ORDER BY name").fetchall()
    assert [row["name"] for row in rows] == ["kept.txt"]

    db.close_thread_connection()


def test_index_rebuild_subset_preserves_other_scopes(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    db.close_thread_connection()
    db.reset_init_db_cache()
    invalidate_settings_cache()
    db.init_db()

    def embedder(texts: list[str]) -> list[list[float]]:
        return [[1.0] for _text in texts]

    old_first = first_workspace / "old-first.txt"
    kept_second = second_workspace / "kept-second.txt"
    old_first.write_text("old first scoped document", encoding="utf-8")
    kept_second.write_text("second scoped document should stay indexed", encoding="utf-8")

    initial = FTSIndex(embedder=embedder).rebuild([str(first_workspace), str(second_workspace)])
    assert initial["files_indexed"] == 2

    old_first.unlink()
    new_first = first_workspace / "new-first.txt"
    new_first.write_text("new first scoped document", encoding="utf-8")
    subset = FTSIndex(embedder=embedder).rebuild([str(first_workspace)])

    assert subset["files_indexed"] == 1
    with db.connect() as conn:
        rows = conn.execute("SELECT normalized_path FROM indexed_files ORDER BY normalized_path").fetchall()
    indexed_paths = [row["normalized_path"] for row in rows]
    assert str(old_first.resolve()) not in indexed_paths
    assert str(new_first.resolve()) in indexed_paths
    assert str(kept_second.resolve()) in indexed_paths

    db.close_thread_connection()
