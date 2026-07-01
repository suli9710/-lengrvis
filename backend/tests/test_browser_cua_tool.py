from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.config import AppSettings
from app.llm.cua_provider import CUAProvider
from app.tools import browser_tools


@pytest.fixture(autouse=True)
def _reset_browser_runtime():
    browser_tools.reset_browser_activity_runtime()
    yield
    browser_tools.reset_browser_activity_runtime()


def _context() -> dict[str, Any]:
    settings = AppSettings(
        provider_name="mock",
        api_key="sk-test",
        mode="efficiency",
        allow_browser_network=True,
        allow_cloud_context=True,
    )
    return {"settings": settings, "allowed_directories": []}


class RecordingCUAProvider(CUAProvider):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__(settings, model="test-cua", source="test")
        self.calls: list[dict[str, Any]] = []

    async def run_step(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {
            "ok": True,
            "status": "completed",
            "provider": self.source,
            "model": self.model,
            "response_id": "resp_test",
            "output": [],
        }


def test_cua_run_rejects_payload_acknowledged_safety_checks(monkeypatch: pytest.MonkeyPatch):
    calls = 0

    async def resolve(*args: Any, **kwargs: Any):  # noqa: ARG001
        nonlocal calls
        calls += 1
        return RecordingCUAProvider(_context()["settings"])

    monkeypatch.setattr(browser_tools, "resolve_cua_provider", resolve)

    result = asyncio.run(
        browser_tools.cua_run_async(
            {
                "instruction": "Click the safe demo button.",
                "dry_run": False,
                "approved": True,
                "approval_id": "approval_test",
                "acknowledged_safety_checks": [{"id": "check_1"}],
            },
            _context(),
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "cannot be acknowledged" in result["error"]
    assert calls == 0


def test_cua_run_rejects_non_browser_environment(monkeypatch: pytest.MonkeyPatch):
    calls = 0

    async def resolve(*args: Any, **kwargs: Any):  # noqa: ARG001
        nonlocal calls
        calls += 1
        return RecordingCUAProvider(_context()["settings"])

    monkeypatch.setattr(browser_tools, "resolve_cua_provider", resolve)

    result = asyncio.run(
        browser_tools.cua_run_async(
            {
                "instruction": "Click the safe demo button.",
                "dry_run": False,
                "approved": True,
                "approval_id": "approval_test",
                "environment": "windows",
            },
            _context(),
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "browser CUA environment" in result["error"]
    assert calls == 0


def test_cua_run_rejects_previous_response_id_before_provider(monkeypatch: pytest.MonkeyPatch):
    calls = 0

    async def resolve(*args: Any, **kwargs: Any):  # noqa: ARG001
        nonlocal calls
        calls += 1
        return RecordingCUAProvider(_context()["settings"])

    monkeypatch.setattr(browser_tools, "resolve_cua_provider", resolve)

    result = asyncio.run(
        browser_tools.cua_run_async(
            {
                "instruction": "Click the safe demo button.",
                "dry_run": False,
                "approved": True,
                "approval_id": "approval_test",
                "previous_response_id": "resp_other_task",
            },
            _context(),
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "previous_response_id" in result["error"]
    assert calls == 0


def test_cua_run_rejects_provider_mode_before_provider(monkeypatch: pytest.MonkeyPatch):
    calls = 0

    async def resolve(*args: Any, **kwargs: Any):  # noqa: ARG001
        nonlocal calls
        calls += 1
        return RecordingCUAProvider(_context()["settings"])

    monkeypatch.setattr(browser_tools, "resolve_cua_provider", resolve)

    result = asyncio.run(
        browser_tools.cua_run_async(
            {
                "instruction": "Click the safe demo button.",
                "dry_run": False,
                "approved": True,
                "approval_id": "approval_test",
                "provider_mode": "openai",
            },
            _context(),
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "provider_mode" in result["error"]
    assert calls == 0


@pytest.mark.parametrize(
    "extra_args, error_fragment",
    [
        ({"acknowledged_safety_checks": [{"id": "check_1"}]}, "cannot be acknowledged"),
        ({"previous_response_id": "resp_other_task"}, "previous_response_id"),
        ({"provider_mode": "openai"}, "provider_mode"),
        ({"environment": "windows"}, "browser CUA environment"),
        ({"screenshot": "file:///C:/Users/Suli/Desktop/private-screen.png"}, "inline data:image"),
    ],
)
def test_cua_run_rejects_runtime_args_before_dry_run_preview(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: dict[str, Any],
    error_fragment: str,
):
    calls = 0

    async def resolve(*args: Any, **kwargs: Any):  # noqa: ARG001
        nonlocal calls
        calls += 1
        return RecordingCUAProvider(_context()["settings"])

    monkeypatch.setattr(browser_tools, "resolve_cua_provider", resolve)

    result = asyncio.run(
        browser_tools.cua_run_async(
            {
                "instruction": "Click the safe demo button.",
                "dry_run": True,
                **extra_args,
            },
            _context(),
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "dry_run" not in result
    assert error_fragment in result["error"]
    assert calls == 0


def test_cua_run_forces_provider_call_to_browser_environment(monkeypatch: pytest.MonkeyPatch):
    provider = RecordingCUAProvider(_context()["settings"])
    modes: list[str] = []

    async def resolve(*args: Any, **kwargs: Any):  # noqa: ARG001
        modes.append(str(kwargs.get("mode")))
        return provider

    monkeypatch.setattr(browser_tools, "resolve_cua_provider", resolve)

    result = asyncio.run(
        browser_tools.cua_run_async(
            {
                "instruction": "Click the safe demo button.",
                "dry_run": False,
                "approved": True,
                "approval_id": "approval_test",
                "environment": "BROWSER",
            },
            _context(),
        )
    )

    assert result["ok"] is True
    assert modes == ["auto"]
    assert provider.calls == [
        {
            "instruction": "Click the safe demo button.",
            "screenshot": None,
            "previous_response_id": None,
            "acknowledged_safety_checks": None,
            "environment": "browser",
        }
    ]


def test_cua_run_sync_times_out_slow_provider(monkeypatch: pytest.MonkeyPatch):
    class SlowCUAProvider(CUAProvider):
        def __init__(self, settings: AppSettings) -> None:
            super().__init__(settings, model="test-cua", source="test")

        async def run_step(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
            await asyncio.sleep(1.0)
            return {"ok": True, "status": "completed"}

    async def resolve(*args: Any, **kwargs: Any):  # noqa: ARG001
        return SlowCUAProvider(_context()["settings"])

    monkeypatch.setattr(browser_tools, "resolve_cua_provider", resolve)
    monkeypatch.setattr(browser_tools, "DEFAULT_CUA_RUN_TIMEOUT_SECONDS", 0.01)

    started = time.monotonic()
    result = browser_tools.cua_run(
        {
            "instruction": "Click the safe demo button.",
            "dry_run": False,
            "approved": True,
            "approval_id": "approval_test",
        },
        _context(),
    )

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert "timed out" in result["error"]
    assert time.monotonic() - started < 0.5


def test_cua_run_sync_timeout_returns_from_running_event_loop(monkeypatch: pytest.MonkeyPatch):
    class SlowCUAProvider(CUAProvider):
        def __init__(self, settings: AppSettings) -> None:
            super().__init__(settings, model="test-cua", source="test")

        async def run_step(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
            await asyncio.sleep(1.0)
            return {"ok": True, "status": "completed"}

    async def resolve(*args: Any, **kwargs: Any):  # noqa: ARG001
        return SlowCUAProvider(_context()["settings"])

    monkeypatch.setattr(browser_tools, "resolve_cua_provider", resolve)
    monkeypatch.setattr(browser_tools, "DEFAULT_CUA_RUN_TIMEOUT_SECONDS", 0.01)

    async def invoke() -> dict[str, Any]:
        return browser_tools.cua_run(
            {
                "instruction": "Click the safe demo button.",
                "dry_run": False,
                "approved": True,
                "approval_id": "approval_test",
            },
            _context(),
        )

    started = time.monotonic()
    result = asyncio.run(invoke())

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert "timed out" in result["error"]
    assert time.monotonic() - started < 0.5


def test_cua_run_rejects_unsafe_screenshot_before_activity_record(monkeypatch: pytest.MonkeyPatch):
    async def resolve(*args: Any, **kwargs: Any):  # noqa: ARG001
        return CUAProvider(_context()["settings"], client_factory=object)

    monkeypatch.setattr(browser_tools, "resolve_cua_provider", resolve)

    result = asyncio.run(
        browser_tools.cua_run_async(
            {
                "instruction": "Click the safe demo button.",
                "dry_run": False,
                "approved": True,
                "approval_id": "approval_test",
                "screenshot": "file:///C:/Users/Suli/Desktop/private-screen.png",
            },
            _context(),
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "inline data:image" in result["error"]
    assert "activity" not in result
