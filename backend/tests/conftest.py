"""Shared pytest helpers for implementation-contract tests.

These tests are intentionally written against the public surfaces the backend is
expected to expose. If a surface is not implemented yet, the individual test
skips with a precise module/API name instead of failing the whole suite.
"""

from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_DATA = PROJECT_ROOT / "test_data"
SLOW_TEST_NODEID_SUFFIXES = {
    "test_lengrvis_code_runner.py::test_cancel_terminates_fake_lengrvis_code_process",
    "test_runs_api.py::test_cancelled_run_is_not_overwritten_by_finishing_engine_turn",
    "test_runs_api.py::test_developer_cancel_terminates_fake_lengrvis_and_publishes_diagnostics",
    "test_runs_api.py::test_paused_run_is_not_overwritten_by_finishing_engine_turn",
    "test_skill_loader.py::test_handler_timeout_returns_inline_error",
    "test_start_app_script.py::test_start_app_recent_log_summary_redacts_secrets",
    "test_task_pool.py::test_pool_shutdown_drains_running_tasks",
}
SLOW_TEST_NODEID_SUBSTRINGS = {
    "test_lan_transport_security.py::test_system_diagnostics_reports_default_http_lan_readiness",
    "test_lan_transport_security.py::test_system_diagnostics_reports_tls_enabled_with_missing_files",
    "test_privacy_mode_offline_eval.py::test_natural_language_system_check_chat_delegates_to_read_only_diagnostics",
    "test_privacy_mode_offline_eval.py::test_natural_language_system_check_runs_auto_routes_to_read_only_diagnostics",
    "test_privacy_mode_offline_eval.py::test_privacy_mode_offline_system_check_completes_with_local_diagnostics",
    "test_runs_api.py::test_run_api_system_diagnostics_stays_os_local_only",
    "test_system_diagnostics.py::test_system_diagnostics_include_anonymous_product_funnel",
    "test_system_diagnostics.py::test_system_diagnostics_include_lan_tls_readiness",
    "test_system_diagnostics.py::test_system_diagnostics_include_local_product_metrics",
    "test_windows_core_capabilities.py::test_public_api_routes_expose_windows_core",
    "test_windows_core_capabilities.py::test_system_diagnostics_startup_and_settings_dry_run",
}


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    return TEST_DATA


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "notes").mkdir()
    (root / "notes" / "safe.txt").write_text("project notes\n", encoding="utf-8")
    return root


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        normalized = item.nodeid.replace("\\", "/")
        if any(normalized.endswith(suffix) for suffix in SLOW_TEST_NODEID_SUFFIXES) or any(
            substring in normalized for substring in SLOW_TEST_NODEID_SUBSTRINGS
        ):
            item.add_marker("slow")


@pytest.fixture(autouse=True)
def desktop_api_token_optional_for_testclient(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "1")
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", "1")


@pytest.fixture(autouse=True)
def isolate_local_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep contract tests independent of the developer's real config.yaml/.env.

    AppSettings.from_sources() auto-discovers config.yaml/.env from the repo
    root, so a developer's live cloud configuration would otherwise leak into
    contract assertions. Point discovery at nonexistent paths; tests that need
    a specific config file set LENGRVIS_CONFIG_FILE/LENGRVIS_ENV_FILE themselves.
    """
    missing = tmp_path / "_no_runtime_config"
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(missing / "config.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(missing / ".env"))


@pytest.fixture(autouse=True)
def reset_settings_cache() -> Iterable[None]:
    """The settings TTL cache must never leak state across tests."""
    from app.llm.registry import invalidate_settings_cache

    invalidate_settings_cache()
    yield
    invalidate_settings_cache()


@pytest.fixture(autouse=True)
def reset_pairing_rate_limit_state() -> Iterable[None]:
    """Pairing failure counters are process-global and must not leak across tests."""
    from app.services import mobile_pairing_service

    with mobile_pairing_service._PAIR_CONFIRM_FAILURES_LOCK:
        mobile_pairing_service._PAIR_CONFIRM_FAILURES.clear()
    yield
    with mobile_pairing_service._PAIR_CONFIRM_FAILURES_LOCK:
        mobile_pairing_service._PAIR_CONFIRM_FAILURES.clear()


@pytest.fixture(autouse=True)
def reset_audit_chain_head_cache() -> Iterable[None]:
    """Audit sequence allocation uses an in-memory head keyed by db path."""
    from app.core import db

    db.reset_audit_caches()
    yield
    db.reset_audit_caches()


def import_first(module_names: Iterable[str]) -> Any:
    """Import the first available module from a list of expected locations."""

    attempted: list[str] = []
    for name in module_names:
        attempted.append(name)
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
    pytest.skip(f"Expected module not implemented yet. Tried: {', '.join(attempted)}")


def require_attr(module: Any, attr_names: Iterable[str]) -> Any:
    """Return the first implemented attribute from a list of accepted names."""

    for name in attr_names:
        if hasattr(module, name):
            return getattr(module, name)
    pytest.skip(
        f"{module.__name__} is present but none of these APIs exist: "
        f"{', '.join(attr_names)}"
    )


def call_with_supported_kwargs(func: Callable[..., Any], **kwargs: Any) -> Any:
    """Call a function with only the keyword arguments it declares.

    Some implementations use names like ``root`` while others prefer
    ``workspace_root``. Tests pass the broad contract and this helper adapts to
    explicit signatures while preserving failures for real runtime errors.
    """

    signature = inspect.signature(func)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return func(**kwargs)

    accepted = {
        name: value for name, value in kwargs.items() if name in signature.parameters
    }
    return func(**accepted)


def load_json_fixture(relative_path: str) -> Any:
    path = TEST_DATA / relative_path
    return json.loads(path.read_text(encoding="utf-8"))
