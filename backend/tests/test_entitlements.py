"""Tests for commercialization plan entitlements (商业化 feature gating)."""

from __future__ import annotations

import pytest

from app.commerce.entitlements import (
    EntitlementError,
    Feature,
    Plan,
    active_plan,
    has_feature,
    is_high_risk,
    normalize_plan,
    require_feature,
    required_plan,
)


def test_normalize_plan_accepts_aliases_and_defaults_to_free():
    assert normalize_plan("free") is Plan.FREE
    assert normalize_plan("") is Plan.FREE
    assert normalize_plan(None) is Plan.FREE
    assert normalize_plan("  PRO ") is Plan.PRO
    assert normalize_plan("professional") is Plan.PRO
    assert normalize_plan("max") is Plan.MAX
    assert normalize_plan("team") is Plan.MAX
    assert normalize_plan("team-self-hosted") is Plan.MAX
    assert normalize_plan("enterprise") is Plan.MAX
    assert normalize_plan("nonsense") is Plan.FREE
    assert normalize_plan(Plan.MAX) is Plan.MAX


def test_has_feature_matrix():
    # Free tier
    assert has_feature(Plan.FREE, Feature.BASIC_TASKS)
    assert has_feature(Plan.FREE, Feature.LOCAL_READ_ONLY)
    assert not has_feature(Plan.FREE, Feature.REMOTE_CONTROL)
    assert not has_feature(Plan.FREE, Feature.DOCUMENT_AI)
    assert not has_feature(Plan.FREE, Feature.AUDIT_EXPORT)
    # Pro tier inherits Free and unlocks cloud capabilities incl. remote control
    assert has_feature(Plan.PRO, Feature.BASIC_TASKS)
    assert has_feature(Plan.PRO, Feature.DOCUMENT_AI)
    assert has_feature(Plan.PRO, Feature.REMOTE_CONTROL)
    assert not has_feature(Plan.PRO, Feature.AUDIT_EXPORT)
    assert not has_feature(Plan.PRO, Feature.PRIVATE_DEPLOYMENT)
    # Max tier inherits everything
    assert has_feature(Plan.MAX, Feature.REMOTE_CONTROL)
    assert has_feature(Plan.MAX, Feature.AUDIT_EXPORT)
    assert has_feature(Plan.MAX, Feature.PRIVATE_DEPLOYMENT)


def test_has_feature_accepts_plan_strings():
    assert has_feature("pro", Feature.REMOTE_CONTROL)
    assert not has_feature("free", Feature.REMOTE_CONTROL)


def test_required_plan_and_high_risk_flags():
    assert required_plan(Feature.REMOTE_CONTROL) is Plan.PRO
    assert required_plan(Feature.AUDIT_EXPORT) is Plan.MAX
    assert is_high_risk(Feature.REMOTE_CONTROL)
    assert not is_high_risk(Feature.DOCUMENT_AI)


def test_require_feature_raises_for_unentitled_plan():
    with pytest.raises(EntitlementError) as excinfo:
        require_feature(Plan.FREE, Feature.REMOTE_CONTROL)
    error = excinfo.value
    assert error.status_code == 402
    assert error.required_plan is Plan.PRO
    assert error.current_plan is Plan.FREE
    assert error.feature is Feature.REMOTE_CONTROL


def test_require_feature_returns_plan_for_entitled():
    assert require_feature(Plan.PRO, Feature.REMOTE_CONTROL) is Plan.PRO
    assert require_feature("team", Feature.AUDIT_EXPORT) is Plan.MAX


def test_active_plan_reads_environment(monkeypatch):
    monkeypatch.delenv("LENGRVIS_PLAN", raising=False)
    assert active_plan() is Plan.FREE
    monkeypatch.setenv("LENGRVIS_PLAN", "pro")
    assert active_plan() is Plan.PRO
    monkeypatch.setenv("LENGRVIS_PLAN", "team-self-hosted")
    assert active_plan() is Plan.MAX


def test_active_plan_prefers_explicit_settings_attribute(monkeypatch):
    monkeypatch.setenv("LENGRVIS_PLAN", "free")

    class _Settings:
        plan = "team"

    assert active_plan(_Settings()) is Plan.MAX


def test_remote_desktop_gated_by_plan_in_effective_settings(monkeypatch):
    from app.llm import registry

    monkeypatch.setenv("LENGRVIS_REMOTE_DESKTOP_ENABLED", "true")

    monkeypatch.setenv("LENGRVIS_PLAN", "free")
    registry.invalidate_settings_cache()
    free_settings = registry.get_effective_settings()
    assert free_settings.remote_desktop_enabled is False

    monkeypatch.setenv("LENGRVIS_PLAN", "pro")
    registry.invalidate_settings_cache()
    pro_settings = registry.get_effective_settings()
    assert pro_settings.remote_desktop_enabled is True


def test_commercial_release_cannot_unlock_paid_plan_with_environment_only(monkeypatch):
    from app.llm import registry

    monkeypatch.setenv("LENGRVIS_COMMERCIAL_RELEASE", "true")
    monkeypatch.setenv("LENGRVIS_PLAN", "max")
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    registry.invalidate_settings_cache()

    settings = registry.get_effective_settings()

    assert settings.plan == "free"
