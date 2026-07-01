# ruff: noqa: S108 - tests intentionally use illustrative temp paths.

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.main import create_app
from app.policy.permissions import (
    PermissionPolicy,
    PermissionRule,
    PermissionStore,
    PermissionTimeWindow,
    _window_datetime,
    evaluate_permission_policy,
)
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


@pytest.fixture(autouse=True)
def _entitle_policy_management(monkeypatch: pytest.MonkeyPatch) -> None:
    """Policy management (PUT/upsert/delete permission-policy) is a Max-tier
    entitlement (Feature.POLICY_MANAGEMENT). Grant Max so the route round-trip
    tests exercise the gated endpoints instead of being rejected at 402."""
    monkeypatch.setenv("LENGRVIS_PLAN", "max")


def weekend_delete_rule() -> PermissionRule:
    return PermissionRule(
        id="weekend_delete",
        name="Weekend delete block",
        effect="deny",
        tools=["file.trash"],
        path_patterns=["*"],
        time_windows=[PermissionTimeWindow(days=[5, 6], start="00:00", end="23:59")],
        reason="Weekend file deletion is blocked.",
    )


def test_permission_policy_denies_when_allow_list_has_no_match():
    allow_rule = PermissionRule(
        id="allow_browser_read",
        name="Allow browser read",
        effect="allow",
        tools=["browser.read"],
        path_patterns=["*"],
        reason="Allow browser read only.",
    )
    policy = PermissionPolicy(rules=[allow_rule])

    decision = evaluate_permission_policy(
        policy,
        tool_name="file.read_text",
        args={"path": "/tmp/example.txt"},
    )

    assert decision.allowed is False
    assert decision.matched is False
    assert "default deny" in decision.reason.lower()


def test_permission_policy_allows_unmatched_when_only_deny_rules_exist():
    policy = PermissionPolicy(rules=[weekend_delete_rule()])
    weekday = datetime.fromisoformat("2026-05-28T12:00:00+00:00")

    decision = evaluate_permission_policy(
        policy,
        tool_name="file.read_text",
        args={"path": "/tmp/example.txt"},
        now=weekday,
    )

    assert decision.allowed is True
    assert decision.matched is False
    assert "default allow" in decision.reason.lower()


def test_permission_policy_allows_when_no_rules_configured():
    decision = evaluate_permission_policy(
        PermissionPolicy(rules=[]),
        tool_name="file.read_text",
        args={"path": "/tmp/example.txt"},
    )

    assert decision.allowed is True
    assert decision.matched is False
    assert "default allow" in decision.reason.lower()


def test_permission_policy_builtin_baseline_denies_high_risk_when_no_rules_configured():
    decision = evaluate_permission_policy(
        PermissionPolicy(rules=[]),
        tool_name="file.trash",
        args={"path": "/tmp/example.txt"},
    )

    assert decision.allowed is False
    assert decision.rule_id == "builtin_high_risk_baseline"
    assert "explicit allow" in decision.reason.lower()


@pytest.mark.parametrize(
    "tool_name",
    [
        "file.write_text",
        "file.copy",
        "file.move",
        "remote.click",
        "remote.type_text",
        "ui_automation.type_text",
        "ui_automation.hotkey",
        "app.launch",
        "workflow.run",
    ],
)
def test_permission_policy_builtin_baseline_denies_write_and_control_tools_without_rules(tool_name: str):
    decision = evaluate_permission_policy(
        PermissionPolicy(rules=[]),
        tool_name=tool_name,
        args={"path": "/tmp/example.txt", "dry_run": False},
    )

    assert decision.allowed is False
    assert decision.rule_id == "builtin_high_risk_baseline"
    assert "explicit allow" in decision.reason.lower()


def test_permission_policy_builtin_baseline_denies_high_risk_when_only_deny_rules_exist():
    policy = PermissionPolicy(rules=[weekend_delete_rule()])
    weekday = datetime.fromisoformat("2026-05-28T12:00:00+00:00")

    decision = evaluate_permission_policy(
        policy,
        tool_name="mcp.filesystem.write_file",
        args={"path": "/tmp/example.txt"},
        now=weekday,
    )

    assert decision.allowed is False
    assert decision.rule_id == "builtin_high_risk_baseline"


def test_permission_policy_explicit_allow_can_override_builtin_baseline():
    allow_rule = PermissionRule(
        id="allow_trash",
        name="Allow trash",
        effect="allow",
        tools=["file.trash"],
        path_patterns=["*"],
        reason="Trash is allowed for this environment.",
    )

    decision = evaluate_permission_policy(
        PermissionPolicy(rules=[allow_rule]),
        tool_name="file.trash",
        args={"path": "/tmp/example.txt"},
    )

    assert decision.allowed is True
    assert decision.rule_id == "allow_trash"


def test_permission_policy_denies_matching_weekend_delete():
    policy = PermissionPolicy(rules=[weekend_delete_rule()])
    saturday = datetime.fromisoformat("2026-05-30T12:00:00+00:00")

    decision = evaluate_permission_policy(
        policy,
        tool_name="file.trash",
        args={"path": "/tmp/example.txt"},
        now=saturday,
    )

    assert decision.allowed is False
    assert decision.rule_id == "weekend_delete"


def test_permission_store_persists_policy_to_sqlite():
    store = PermissionStore()
    store.save_policy(PermissionPolicy(rules=[weekend_delete_rule()]))

    loaded = store.get_policy()

    assert loaded.rules[0].id == "weekend_delete"
    assert loaded.rules[0].time_windows
    assert loaded.rules[0].time_windows[0].days == [5, 6]


def test_permission_store_corrupt_policy_parse_fallback_is_narrow(monkeypatch: pytest.MonkeyPatch):
    store = PermissionStore()
    store.save_policy(PermissionPolicy(rules=[weekend_delete_rule()]))
    corrupt_payload = "{not-json"
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE permission_policies SET data = ?, updated_at = ? WHERE id = ?",
            (corrupt_payload, "now", store.policy_id),
        )
        db.store_sensitive_record_integrity("permission_policies", store.policy_id, corrupt_payload, conn=conn)

    assert store.get_policy().rules == []
    updated = store.add_rule(weekend_delete_rule())
    assert [rule.id for rule in updated.rules] == ["weekend_delete"]

    def fail_model_validate(cls, value):  # noqa: ANN001
        raise RuntimeError("policy parser bug")

    monkeypatch.setattr(PermissionPolicy, "model_validate", classmethod(fail_model_validate))
    valid_payload = "{}"
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE permission_policies SET data = ?, updated_at = ? WHERE id = ?",
            (valid_payload, "now", store.policy_id),
        )
        db.store_sensitive_record_integrity("permission_policies", store.policy_id, valid_payload, conn=conn)

    with pytest.raises(RuntimeError, match="policy parser bug"):
        store.get_policy()


def test_permission_window_datetime_invalid_timezone_falls_back() -> None:
    now = datetime(2026, 1, 1, 12, 0)
    window = PermissionTimeWindow(start="09:00", end="17:00", timezone="Not/A_Real_Zone")

    assert _window_datetime(window, now) is now


def test_settings_permission_policy_routes_round_trip_rule():
    client = TestClient(create_app())

    confirmation = client.post(
        "/api/settings/permission-policy/confirm-relaxation",
        json={"action": "upsert_rule", "rule": weekend_delete_rule().model_dump(mode="json")},
    )
    response = client.post(
        "/api/settings/permission-policy/rules",
        json=weekend_delete_rule().model_dump(mode="json"),
        params={"confirmation_nonce": confirmation.json()["nonce"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rules"][0]["id"] == "weekend_delete"

    fetched = client.get("/api/settings/permission-policy")
    assert fetched.status_code == 200
    assert fetched.json()["rules"][0]["tools"] == ["file.trash"]

    delete_confirmation = client.post(
        "/api/settings/permission-policy/confirm-relaxation",
        json={"action": "delete_rule", "rule_id": "weekend_delete"},
    )
    deleted = client.delete(
        "/api/settings/permission-policy/rules/weekend_delete",
        params={"confirmation_nonce": delete_confirmation.json()["nonce"]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert deleted.json()["policy"]["rules"] == []


def test_permission_allow_rule_requires_confirmation():
    client = TestClient(create_app())
    allow_rule = PermissionRule(
        id="allow_all_shell",
        name="Allow shell",
        effect="allow",
        tools=["shell.execute"],
        path_patterns=["*"],
        reason="Allow shell execution.",
    )

    blocked = client.post(
        "/api/settings/permission-policy/rules",
        json=allow_rule.model_dump(mode="json"),
    )
    confirmation = client.post(
        "/api/settings/permission-policy/confirm-relaxation",
        json={"action": "upsert_rule", "rule": allow_rule.model_dump(mode="json")},
    )
    allowed = client.post(
        "/api/settings/permission-policy/rules",
        json=allow_rule.model_dump(mode="json"),
        params={"confirmation_nonce": confirmation.json()["nonce"]},
    )
    second_allow_rule = PermissionRule(
        id="allow_all_browser",
        name="Allow browser",
        effect="allow",
        tools=["browser.*"],
        path_patterns=["*"],
        reason="Allow browser actions.",
    )
    reused_for_other_rule = client.post(
        "/api/settings/permission-policy/rules",
        json=second_allow_rule.model_dump(mode="json"),
        params={"confirmation_nonce": confirmation.json()["nonce"]},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "sensitive_confirmation_required"
    assert confirmation.status_code == 200
    assert confirmation.json()["required"] is True
    assert allowed.status_code == 200
    assert reused_for_other_rule.status_code == 409


def test_permission_deny_rule_delete_requires_confirmation():
    client = TestClient(create_app())
    PermissionStore().save_policy(PermissionPolicy(rules=[weekend_delete_rule()]))

    blocked = client.delete("/api/settings/permission-policy/rules/weekend_delete")
    confirmation = client.post(
        "/api/settings/permission-policy/confirm-relaxation",
        json={"action": "delete_rule", "rule_id": "weekend_delete"},
    )
    allowed = client.delete(
        "/api/settings/permission-policy/rules/weekend_delete",
        params={"confirmation_nonce": confirmation.json()["nonce"]},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "sensitive_confirmation_required"
    assert confirmation.status_code == 200
    assert confirmation.json()["required"] is True
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True


def test_permission_allow_rule_scope_expansion_requires_confirmation():
    client = TestClient(create_app())
    narrow_rule = PermissionRule(
        id="allow_browser_read",
        name="Allow browser read",
        effect="allow",
        tools=["browser.read"],
        path_patterns=["C:/Users/Suli/Documents/*"],
        time_windows=[PermissionTimeWindow(days=[0], start="09:00", end="10:00")],
        reason="Allow a narrow browser read window.",
    )
    PermissionStore().save_policy(PermissionPolicy(rules=[narrow_rule]))
    expanded_rule = narrow_rule.model_copy(
        update={
            "tools": ["browser.*"],
            "path_patterns": ["C:/Users/Suli/*"],
            "time_windows": [PermissionTimeWindow(days=[0, 1], start="08:00", end="11:00")],
        },
        deep=True,
    )

    blocked = client.post(
        "/api/settings/permission-policy/rules",
        json=expanded_rule.model_dump(mode="json"),
    )
    confirmation = client.post(
        "/api/settings/permission-policy/confirm-relaxation",
        json={"action": "upsert_rule", "rule": expanded_rule.model_dump(mode="json")},
    )
    allowed = client.post(
        "/api/settings/permission-policy/rules",
        json=expanded_rule.model_dump(mode="json"),
        params={"confirmation_nonce": confirmation.json()["nonce"]},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "sensitive_confirmation_required"
    assert confirmation.status_code == 200
    assert confirmation.json()["changes"][0]["kind"] == "allow_rule_scope_expanded"
    assert set(confirmation.json()["changes"][0]["fields"]) == {"tools", "path_patterns", "time_windows"}
    assert allowed.status_code == 200


def test_permission_deny_rule_scope_narrowing_requires_confirmation():
    client = TestClient(create_app())
    broad_deny = PermissionRule(
        id="deny_shell",
        name="Deny shell",
        effect="deny",
        tools=["shell.*"],
        path_patterns=["*"],
        time_windows=[PermissionTimeWindow(days=list(range(7)), start="00:00", end="23:59")],
        reason="Block shell broadly.",
    )
    PermissionStore().save_policy(PermissionPolicy(rules=[broad_deny]))
    narrowed_deny = broad_deny.model_copy(
        update={
            "tools": ["shell.readonly"],
            "path_patterns": ["C:/Users/Suli/Documents/*"],
            "time_windows": [PermissionTimeWindow(days=[0], start="09:00", end="10:00")],
        },
        deep=True,
    )

    blocked = client.post(
        "/api/settings/permission-policy/rules",
        json=narrowed_deny.model_dump(mode="json"),
    )
    confirmation = client.post(
        "/api/settings/permission-policy/confirm-relaxation",
        json={"action": "upsert_rule", "rule": narrowed_deny.model_dump(mode="json")},
    )
    allowed = client.post(
        "/api/settings/permission-policy/rules",
        json=narrowed_deny.model_dump(mode="json"),
        params={"confirmation_nonce": confirmation.json()["nonce"]},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "sensitive_confirmation_required"
    assert confirmation.status_code == 200
    assert confirmation.json()["changes"][0]["kind"] == "deny_rule_scope_narrowed"
    assert set(confirmation.json()["changes"][0]["fields"]) == {"tools", "path_patterns", "time_windows"}
    assert allowed.status_code == 200


def test_policy_engine_blocks_weekend_delete_from_persisted_permission_policy():
    PermissionStore().save_policy(PermissionPolicy(rules=[weekend_delete_rule()]))
    saturday = datetime.fromisoformat("2026-05-30T12:00:00+00:00")
    engine = PolicyEngine(now_provider=lambda: saturday)

    review = engine.review_tool_call(
        "task_permissions",
        "step_delete",
        "file.trash",
        {"path": "/tmp/example.txt", "dry_run": False},
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
    )

    assert review.verdict == SafetyVerdict.DENY
    assert review.risk_level == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
    assert "weekend_delete" in review.reasons[0]
    assert "Weekend file deletion is blocked" in review.reasons[0]


def test_policy_engine_fails_closed_when_permission_store_errors(monkeypatch: pytest.MonkeyPatch):
    engine = PolicyEngine()

    def broken_evaluate(**kwargs):  # noqa: ANN003, ANN202, ARG001
        raise RuntimeError("policy store unavailable")

    monkeypatch.setattr(engine.permission_store, "evaluate", broken_evaluate)

    review = engine.review_tool_call(
        "task_permissions",
        "step_read",
        "system.get_info",
        {},
        RiskLevel.R0_READ_ONLY,
    )

    assert review.verdict == SafetyVerdict.DENY
    assert "fail-closed" in review.reasons[0].lower()


def test_evaluate_user_permission_for_tool_matches_policy_engine():
    from app.policy.permissions import evaluate_user_permission_for_tool

    PermissionStore().save_policy(PermissionPolicy(rules=[weekend_delete_rule()]))
    saturday = datetime.fromisoformat("2026-05-30T12:00:00+00:00")
    engine = PolicyEngine(now_provider=lambda: saturday)
    args = {"path": "/tmp/example.txt", "dry_run": False}

    via_engine = engine._review_permission_policy("file.trash", args, {})
    via_helper = evaluate_user_permission_for_tool(
        tool_name="file.trash",
        args=args,
        context={},
        policy_engine=engine,
    )

    assert via_engine.allowed == via_helper.allowed
    assert via_helper.allowed is False
    assert "Weekend" in via_helper.reason
