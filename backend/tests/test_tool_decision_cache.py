from __future__ import annotations

from app.policy.decision_cache import (
    _INTERNAL_CACHE_SCOPE_MARKER,
    ToolDecisionCache,
)
from app.policy.risk import RiskLevel, SafetyVerdict

# Mirror the production deterministic fast-path contract. After the P1-1
# hardening the cache only trusts a process-level random marker injected by
# policy_engine._fast_path_tool_call; a caller-supplied scope string is
# rejected to prevent cache poisoning. Tests must use the same marker.
_FAST_PATH_CONTEXT = {"_internal_cache_scope": _INTERNAL_CACHE_SCOPE_MARKER}


def test_tool_decision_cache_reuses_equivalent_decisions():
    cache = ToolDecisionCache(max_entries=2, ttl_seconds=60, now_provider=lambda: 100.0)
    cache.put(
        "browser.navigate",
        {"url": "https://example.com", "dry_run": True},
        verdict=SafetyVerdict.ALLOW,
        risk_level=RiskLevel.R1_OPEN_ONLY,
        reasons=["open-only"],
        context=_FAST_PATH_CONTEXT,
    )

    decision = cache.get(
        "browser.navigate",
        {"dry_run": True, "url": "https://example.com"},
        context=_FAST_PATH_CONTEXT,
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
        context=_FAST_PATH_CONTEXT,
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
        context=_FAST_PATH_CONTEXT,
    )

    now[0] = 106.0

    assert cache.get("browser.navigate", {"url": "https://example.com"}, context=_FAST_PATH_CONTEXT) is None
