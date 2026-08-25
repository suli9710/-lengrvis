"""Task-local external-surface fixtures for the real-LLM eval harness.

The versioned benchmark measures planner and run-policy behavior. Browser page
content and paid document capability are therefore injected only for an
explicit benchmark task, restored immediately afterwards, and reported as
fixture capabilities. Product runtime policy, SSRF validation, licensing, and
approval gates are not changed.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse


def benchmark_capabilities(task: dict[str, Any]) -> dict[str, Any]:
    expected_tools = _expected_tools(task)
    capabilities: dict[str, Any] = {
        "browser_network": any(name.startswith("browser.") for name in expected_tools)
    }
    if isinstance(task.get("browser_fixture"), dict):
        capabilities["browser_text_fixture"] = True
        capabilities["browser_fixture_host_allowlist"] = True
    if any(name.startswith("document.") for name in expected_tools):
        capabilities["document_ai_entitlement_fixture"] = True
    return capabilities


def benchmark_environment(task: dict[str, Any]) -> dict[str, str | None]:
    if any(name.startswith("document.") for name in _expected_tools(task)):
        # Licensing intentionally ignores paid-plan env overrides outside test
        # mode. The isolated harness declares this fixture explicitly instead
        # of weakening the production entitlement resolver.
        return {"LENGRVIS_PLAN": "pro", "LENGRVIS_TEST": "1"}
    return {}


@contextmanager
def benchmark_runtime_scope(task: dict[str, Any]):
    fixture = task.get("browser_fixture")
    if not isinstance(fixture, dict):
        yield
        return

    from app.services import browser_activity_runtime
    from app.tools import browser_tools

    fixture_url = str(fixture.get("url") or "").strip()
    fixture_host = (urlparse(fixture_url).hostname or "").casefold().rstrip(".")
    if not fixture_url or not fixture_host:
        raise ValueError("browser_fixture requires an absolute URL")

    original_private_host_check = browser_activity_runtime._is_private_host
    original_runtime = browser_tools._BROWSER_ACTIVITY_RUNTIME

    def task_local_private_host_check(hostname: str) -> bool:
        normalized = str(hostname or "").casefold().rstrip(".")
        if normalized == fixture_host:
            return False
        return original_private_host_check(hostname)

    browser_activity_runtime._is_private_host = task_local_private_host_check
    browser_tools.reset_browser_activity_runtime(
        _BenchmarkBrowserAdapter(fixture_url=fixture_url, fixture=fixture)
    )
    try:
        yield
    finally:
        browser_activity_runtime._is_private_host = original_private_host_check
        browser_tools._BROWSER_ACTIVITY_RUNTIME = original_runtime


class _BenchmarkBrowserAdapter:
    def __init__(self, *, fixture_url: str, fixture: dict[str, Any]) -> None:
        self.fixture_url = fixture_url
        self.fixture = fixture

    def perform(
        self,
        _session: Any,
        action: dict[str, Any],
        _context: dict[str, Any],
    ) -> dict[str, Any]:
        kind = str(action.get("kind") or "").casefold()
        action_url = str(action.get("url") or self.fixture_url).strip()
        if kind not in {"open", "navigate", "observe"}:
            return {
                "ok": False,
                "error": "The benchmark browser fixture forbids live write execution.",
            }
        if _normalized_fixture_url(action_url) != _normalized_fixture_url(
            self.fixture_url
        ):
            return {
                "ok": False,
                "error": "The browser action is outside the task-local benchmark fixture.",
            }
        return {
            "ok": True,
            "url": self.fixture_url,
            "title": str(self.fixture.get("title") or "Benchmark page"),
            "text": str(self.fixture.get("text") or ""),
            "links": [],
            "adapter": "real_llm_eval_fixture",
        }


def _expected_tools(task: dict[str, Any]) -> list[str]:
    expect = task.get("expect")
    if not isinstance(expect, dict):
        return []
    raw_tools = expect.get("plan_tools") or expect.get("task_plan_tools") or []
    return [str(name) for name in raw_tools if isinstance(name, str)]


def _normalized_fixture_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    path = parsed.path or "/"
    return parsed._replace(
        scheme=parsed.scheme.casefold(),
        netloc=parsed.netloc.casefold(),
        path=path,
        fragment="",
    ).geturl()
