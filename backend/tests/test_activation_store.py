from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.commerce import activation
from app.commerce.activation_store import ActivationStore


def test_activation_store_owns_subscription_admin_lifecycle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_ACTIVATION_KEY_PEPPER", "store-test-pepper")
    store = ActivationStore(tmp_path / "activation.sqlite")

    created = store.upsert_subscription_key(
        activation_key="secret-activation-key",
        plan="pro",
        subscription_id="sub_store_001",
        status="active",
        subject="customer-redacted",
        max_devices=2,
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )
    listed = store.list_subscription_keys()

    assert created["subscription_id"] == "sub_store_001"
    assert listed[0]["key_hash"] == created["key_hash"]
    assert listed[0]["devices"] == []
    assert "secret-activation-key" not in store.path.read_bytes().decode("utf-8", errors="ignore")

    revoked = store.revoke_subscription_key(
        key_hash=created["key_hash"],
        now=datetime(2026, 1, 3, tzinfo=UTC),
    )
    removed = store.delete_subscription_key(key_hash=created["key_hash"])

    assert revoked["status"] == "revoked"
    assert revoked["revocation_manifest_required"] is False
    assert removed["removed"] is True
    assert store.list_subscription_keys() == []


def test_legacy_activation_entrypoints_keep_private_monkeypatch_seams(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "patched.sqlite"
    fixed_now = datetime(2026, 2, 3, 4, 5, tzinfo=UTC)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_KEY_PEPPER", "legacy-test-pepper")
    monkeypatch.setattr(activation, "_activation_db_path", lambda path=None: db_path)
    monkeypatch.setattr(activation, "_utc_now", lambda: fixed_now)

    created = activation.upsert_subscription_key(
        activation_key="legacy-entrypoint-key",
        plan="max",
        subscription_id="sub_legacy_001",
        status="active",
    )
    listed = activation.list_subscription_keys()

    assert db_path.exists()
    assert created["key_hash"] == listed[0]["key_hash"]
    assert listed[0]["created_at"] == "2026-02-03T04:05:00Z"


def test_legacy_pro_rows_migrate_to_plus_without_silent_upgrade(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    monkeypatch.setenv("LENGRVIS_ACTIVATION_KEY_PEPPER", "legacy-catalog-pepper")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE subscription_keys (
                key_hash TEXT PRIMARY KEY,
                plan TEXT NOT NULL,
                subscription_id TEXT NOT NULL,
                status TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                seats INTEGER NOT NULL DEFAULT 1,
                max_devices INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                renews_at TEXT,
                cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                order_ref TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO subscription_keys (
                key_hash, plan, subscription_id, status, created_at, updated_at
            ) VALUES (?, 'pro', 'sub_legacy_pro', 'active', ?, ?)
            """,
            ("a" * 64, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )

    listed = ActivationStore(db_path).list_subscription_keys()

    assert listed[0]["plan"] == "plus"
    with sqlite3.connect(db_path) as conn:
        catalog = conn.execute("SELECT plan_catalog FROM subscription_keys").fetchone()[0]
    assert catalog == "free-pro-max-v1"
