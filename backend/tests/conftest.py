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

