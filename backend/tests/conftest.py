"""Shared pytest helpers for implementation-contract tests.

These tests are written against public surfaces the backend is expected to keep
exposing. Missing modules or APIs are regressions, so the helpers fail closed
instead of silently skipping coverage.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

# Secure local secret storage (app.security.local_secret) fails closed when no
# DPAPI/keyring backend is available. The autouse fixtures below set
# LENGRVIS_TEST=1, and pytest sets PYTEST_CURRENT_TEST, but only at test *setup*
# time -- neither is present during collection. Importing modules that touch
# local-secret storage at import time therefore raises RuntimeError and aborts
# collection on runners without a usable keyring backend. Opt into the
# sanctioned test/dev fallback at import time. Tests asserting the fail-closed
# behavior delete this var via monkeypatch, so this only provides a default.
os.environ.setdefault("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")
os.environ.setdefault("LENGRVIS_NATIVE_CONFIRMATION_SECRET", "test-native-confirmation-secret")


# test_start_app_script.py exercises Windows-only PowerShell launch behavior and
# only passes on Windows runners. Skip collecting it on non-Windows platforms.
collect_ignore_glob: list[str] = []
if os.name != "nt":
    collect_ignore_glob.append("test_start_app_script.py")


# Remote desktop control (手机远控) is a Plus+ entitlement. The commercialization
# gate app.llm.registry._enforce_plan_entitlements() (applied inside
# get_effective_settings()) force-disables remote_desktop_enabled on the default
# FREE plan, and test_entitlements.py asserts that FREE -> disabled behavior.
# Test helpers in these modules enable remote desktop without selecting an
# entitled plan, so without this fixture the flag is silently reverted to False
# and the remote-input grant/JWT paths raise 403 "Remote desktop is disabled".
# Running these modules on a Pro plan keeps the enabled flag honored; it does NOT
# auto-enable remote desktop, so disabled-by-default assertions still hold.
REMOTE_DESKTOP_ENTITLED_TEST_MODULES = {
    "test_remote_desktop",
    "test_mobile_pairing",
    "test_lan_api_guard",
    "test_guardian_backend",
    "test_llm_settings_api",
}


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
CROSS_PLATFORM_CI_SKIP_NODEID_SUBSTRINGS = {
    # This flow intentionally exercises the host OS trash/recycle-bin adapter.
    # Windows CI remains the authoritative gate; Linux/macOS runners do not
    # provide the same user-session trash semantics and have historically failed
    # here for environment reasons unrelated to the backend contract. Approval
    # resume flows fake send2trash and remain covered cross-platform.
    "test_rollback.py::test_rollback_trash_created_file_sends_to_recycle_bin",
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
    cross_platform_skip = pytest.mark.skip(
        reason="Windows host OS integration is covered by the windows-latest CI gate."
    )
    skip_windows_host_integrations = os.name != "nt" and os.environ.get("LENGRVIS_CROSS_PLATFORM_CI") == "1"
    for item in items:
        normalized = item.nodeid.replace("\\", "/")
        if any(normalized.endswith(suffix) for suffix in SLOW_TEST_NODEID_SUFFIXES) or any(
            substring in normalized for substring in SLOW_TEST_NODEID_SUBSTRINGS
        ):
            item.add_marker("slow")
        if skip_windows_host_integrations and any(
            substring in normalized for substring in CROSS_PLATFORM_CI_SKIP_NODEID_SUBSTRINGS
        ):
            item.add_marker(cross_platform_skip)
        if not item.get_closest_marker("requires_desktop_api_token"):
            item.add_marker(pytest.mark.desktop_api_token_optional)


@pytest.fixture(autouse=True)
def desktop_api_token_optional_for_testclient(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_TEST", "1")
    if request.node.get_closest_marker("desktop_api_token_optional"):
        monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", "1")


@pytest.fixture(autouse=True)
def entitle_remote_desktop_plan_for_remote_modules(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run remote-desktop modules on a Pro plan so the entitlement gate keeps
    remote_desktop_enabled honored (see REMOTE_DESKTOP_ENTITLED_TEST_MODULES).

    This only selects an entitled plan; it never enables remote desktop itself,
    so tests asserting the disabled-by-default behavior are unaffected.
    """
    module = getattr(request, "module", None)
    module_name = (getattr(module, "__name__", "") or "").rsplit(".", 1)[-1]
    if module_name in REMOTE_DESKTOP_ENTITLED_TEST_MODULES:
        monkeypatch.setenv("LENGRVIS_PLAN", "pro")


@pytest.fixture(autouse=True)
def isolate_local_runtime_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep contract tests independent of the developer's real config.yaml/.env.

    AppSettings.from_sources() auto-discovers config.yaml/.env from the repo
    root, so a developer's live cloud configuration would otherwise leak into
    contract assertions. Point discovery and local state at temporary paths;
    tests that need a specific config or data directory set the corresponding
    LENGRVIS_* variable themselves.
    """
    missing = tmp_path / "_no_runtime_config"
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(missing / "config.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(missing / ".env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "pytest-audit-hmac-secret")
    for key in (
        "LENGRVIS_LAN_TLS_ENABLED",
        "LENGRVIS_LAN_TLS_AUTO",
        "LENGRVIS_LAN_TLS_CERT_FILE",
        "LENGRVIS_LAN_TLS_KEY_FILE",
        "LENGRVIS_LAN_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


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
    """DB handles and audit state must never leak across test cases."""
    from app.core import db

    db.reset_connection_state()
    db.set_startup_sensitive_integrity_status({"ok": True, "checked": 0, "failures": []})
    yield
    db.set_startup_sensitive_integrity_status({"ok": True, "checked": 0, "failures": []})
    db.reset_connection_state()


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
    pytest.fail(f"Expected module is missing. Tried: {', '.join(attempted)}")


def require_attr(module: Any, attr_names: Iterable[str]) -> Any:
    """Return the first implemented attribute from a list of accepted names."""

    for name in attr_names:
        if hasattr(module, name):
            return getattr(module, name)
    pytest.fail(f"{module.__name__} is present but none of these APIs exist: {', '.join(attr_names)}")


def call_with_supported_kwargs(func: Callable[..., Any], **kwargs: Any) -> Any:
    """Call a function with only the keyword arguments it declares.

    Some implementations use names like ``root`` while others prefer
    ``workspace_root``. Tests pass the broad contract and this helper adapts to
    explicit signatures while preserving failures for real runtime errors.
    """

    signature = inspect.signature(func)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return func(**kwargs)

    accepted = {name: value for name, value in kwargs.items() if name in signature.parameters}
    return func(**accepted)


def load_json_fixture(relative_path: str) -> Any:
    path = TEST_DATA / relative_path
    return json.loads(path.read_text(encoding="utf-8"))
