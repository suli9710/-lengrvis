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
    evaluate_permission_policy,
)
from app.policy.policy_engine import PolicyEngine
from app.policy.risk import RiskLevel, SafetyVerdict


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


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
