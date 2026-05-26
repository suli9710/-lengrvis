from __future__ import annotations

from app.policy.decision_cache import ToolDecisionCache
from app.policy.risk import RiskLevel, SafetyVerdict


def test_tool_decision_cache_reuses_equivalent_decisions():
    cache = ToolDecisionCache(max_entries=2, ttl_seconds=60, now_provider=lambda: 100.0)
    cache.put(
        "browser.navigate",
        {"url": "https://example.com", "dry_run": True},
        verdict=SafetyVerdict.ALLOW,
        risk_level=RiskLevel.R1_OPEN_ONLY,
        reasons=["open-only"],
        context={"cache_scope": "deterministic_fast_path"},
    )

    decision = cache.get(
        "browser.navigate",
        {"dry_run": True, "url": "https://example.com"},
        context={"cache_scope": "deterministic_fast_path"},
    )

    assert decision is not None
    assert decision.verdict == SafetyVerdict.ALLOW
    assert decision.risk_level == RiskLevel.R1_OPEN_ONLY


def test_tool_decision_cache_requires_fast_path_scope():
    cache = ToolDecisionCache(ttl_seconds=60, now_provider=lambda: 100.0)
    cache.put(
        "browser.navigate",
        {"url": "https://example.com", "dry_run": True},
        verdict=SafetyVerdict.ALLOW,
        risk_level=RiskLevel.R1_OPEN_ONLY,
        reasons=["ordinary allow"],
    )

    assert cache.get("browser.navigate", {"url": "https://example.com", "dry_run": True}) is None


def test_tool_decision_cache_does_not_cache_approved_or_live_write_args():
    cache = ToolDecisionCache(ttl_seconds=60, now_provider=lambda: 100.0)
    cache.put(
        "browser.click_element",
        {"url": "https://example.com", "selector": "#go", "dry_run": False, "approved": True, "approval_id": "a1"},
        verdict=SafetyVerdict.ALLOW,
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
    )

    assert cache.get("browser.click_element", {"url": "https://example.com", "selector": "#go", "dry_run": False}) is None


def test_tool_decision_cache_expires_entries():
    now = [100.0]
    cache = ToolDecisionCache(ttl_seconds=5, now_provider=lambda: now[0])
    cache.put(
        "browser.navigate",
        {"url": "https://example.com"},
        verdict=SafetyVerdict.ALLOW,
        risk_level=RiskLevel.R1_OPEN_ONLY,
        context={"cache_scope": "deterministic_fast_path"},
    )

    now[0] = 106.0

    assert cache.get("browser.navigate", {"url": "https://example.com"}, context={"cache_scope": "deterministic_fast_path"}) is None
