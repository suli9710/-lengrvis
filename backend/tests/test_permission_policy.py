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

    response = client.post(
        "/api/settings/permission-policy/rules",
        json=weekend_delete_rule().model_dump(mode="json"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rules"][0]["id"] == "weekend_delete"

    fetched = client.get("/api/settings/permission-policy")
    assert fetched.status_code == 200
    assert fetched.json()["rules"][0]["tools"] == ["file.trash"]

    deleted = client.delete("/api/settings/permission-policy/rules/weekend_delete")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True
    assert deleted.json()["policy"]["rules"] == []


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
