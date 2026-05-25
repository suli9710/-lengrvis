"""Tests for P1-2 browser write operations (click / fill / submit)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import AppSettings
from app.core import db
from app.tools import browser_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


def _context(*, mode: str = "privacy", allow_browser_network: bool = True, allow_cloud_context: bool = False) -> dict:
    return {
        "settings": AppSettings(
            provider_name="mock",
            mode=mode,
            allow_browser_network=allow_browser_network,
            allow_cloud_context=allow_cloud_context,
        ),
        "allowed_directories": [],
    }


def test_click_blocked_in_privacy_mode():
    context = _context(mode="privacy")
    result = browser_tools.click_element(
        {"url": "https://example.com", "selector": "button", "dry_run": True},
        context,
    )
    assert result["ok"] is False
    assert "privacy" in result["error"].lower()


def test_click_dry_run_in_efficiency_mode_returns_preview():
    context = _context(mode="efficiency")
    result = browser_tools.click_element(
        {"url": "https://example.com", "selector": "#submit", "dry_run": True},
        context,
    )
    assert result["ok"] is True
    assert result.get("dry_run") is True
    assert any(item["action"] == "click" for item in result["diff_preview"])


def test_click_blocks_sensitive_selector():
    context = _context(mode="efficiency")
    result = browser_tools.click_element(
        {"url": "https://example.com", "selector": "#password", "dry_run": True},
        context,
    )
    assert result["ok"] is False
    assert "sensitive" in result["error"].lower()


def test_fill_form_blocks_password_field():
    context = _context(mode="efficiency")
    result = browser_tools.fill_form(
        {
            "url": "https://example.com/login",
            "fields": {"password": "secret"},
            "dry_run": True,
        },
        context,
    )
    assert result["ok"] is False


def test_fill_form_redacts_values_in_dry_run():
    context = _context(mode="efficiency")
    result = browser_tools.fill_form(
        {
            "url": "https://example.com",
            "fields": {"#name": "Alice", "#email": "a@b.com"},
            "dry_run": True,
        },
        context,
    )
    assert result["ok"] is True
    values = {item["field_name"]: item["value"] for item in result["diff_preview"]}
    assert all(value == "***" for value in values.values())


def test_submit_form_requires_efficiency_mode():
    context = _context(mode="hybrid", allow_cloud_context=False)
    result = browser_tools.submit_form(
        {"url": "https://example.com", "selector": "form", "dry_run": True},
        context,
    )
    assert result["ok"] is False


def test_submit_form_dry_run_in_efficiency_mode():
    context = _context(mode="efficiency")
    result = browser_tools.submit_form(
        {"url": "https://example.com", "selector": "form#login", "dry_run": True},
        context,
    )
    assert result["ok"] is True
    assert any(item["action"] == "submit" for item in result["diff_preview"])


def test_hybrid_with_cloud_context_unlocks_writes():
    context = _context(mode="hybrid", allow_cloud_context=True)
    result = browser_tools.click_element(
        {"url": "https://example.com", "selector": "#go", "dry_run": True},
        context,
    )
    assert result["ok"] is True
