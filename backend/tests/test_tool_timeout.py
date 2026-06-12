"""Verify synchronous tool execution respects configured timeouts."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import db
from app.orchestration.tool_runtime import ToolRuntime
from app.policy.risk import RiskLevel
from app.tools.registry import register_all_tools
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_MODE", "efficiency")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    db.init_db()
    register_all_tools()
    yield


def _slow_execute(args, context):  # noqa: ANN001, ANN202, ARG001
    time.sleep(2)
    return {"ok": True}


def test_slow_tool_times_out_with_configured_limit():
    tool = ToolDefinition(
        name="test.slow_timeout",
        description="slow tool for timeout test",
        input_schema={},
        output_schema={},
        risk_level=RiskLevel.R0_READ_ONLY,
        agent_owner="FileAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=_slow_execute,
    )
    context = {"settings": SimpleNamespace(tool_timeout_seconds=0.1)}
    runtime = ToolRuntime(orchestrator=None)

    output = asyncio.run(runtime._execute_tool_body(tool, {}, context, threaded=True))

    assert output.get("error")
    assert "timed out" in str(output["error"]).casefold()
    assert "test.slow_timeout" in str(output["error"])
