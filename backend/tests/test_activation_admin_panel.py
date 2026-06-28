from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_activation import router as activation_router
from app.api.routes_activation_admin import (
    ADMIN_PASSWORD_HASH_ENV_VAR,
    ADMIN_SESSION_SECRET_ENV_VAR,
    hash_admin_password,
    verify_admin_password,
)
from app.api.routes_activation_admin import (
    router as admin_router,
)
from app.commerce.activation import (
    ACTIVATION_KEY_PEPPER_ENV_VAR,
    ACTIVATION_SIGNING_PRIVATE_KEY_ENV_VAR,
    ActivationRequest,
    activate_subscription_key,
)
from app.commerce.licensing import LICENSE_PUBLIC_KEY_ENV_VAR, parse_license
from app.core.errors import register_error_handlers

PRIVATE_KEY_BYTES = bytes(range(1, 33))
PRIVATE_KEY = base64.urlsafe_b64encode(PRIVATE_KEY_BYTES).rstrip(b"=").decode("ascii")
_PRIVATE = Ed25519PrivateKey.from_private_bytes(PRIVATE_KEY_BYTES)
PUBLIC_KEY = (
    base64.urlsafe_b64encode(
        _PRIVATE.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    .rstrip(b"=")
    .decode("ascii")
)


def _future(days: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(activation_router, prefix="/api")
    app.include_router(admin_router)
    return app


def _configure(monkeypatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "activation.sqlite"
    monkeypatch.setenv(ACTIVATION_KEY_PEPPER_ENV_VAR, "pepper-redacted")
    monkeypatch.setenv(ACTIVATION_SIGNING_PRIVATE_KEY_ENV_VAR, PRIVATE_KEY)
    monkeypatch.setenv(LICENSE_PUBLIC_KEY_ENV_VAR, PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_DB", str(db_path))
    monkeypatch.setenv(ADMIN_SESSION_SECRET_ENV_VAR, "admin-session-redacted")
    monkeypatch.setenv(
        ADMIN_PASSWORD_HASH_ENV_VAR,
        hash_admin_password("correct-password", salt=b"0123456789abcdef", iterations=1000),
    )
    return db_path


def _login(client: TestClient) -> str:
    response = client.post("/api/admin/login", json={"password": "correct-password"})
    assert response.status_code == 200
    csrf = client.cookies.get("lengrvis_admin_csrf")
    assert csrf
    return csrf


def _create_key(client: TestClient, csrf: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "plan": "pro",
        "subscription_id": "sub_admin_001",
        "status": "active",
        "subject": "customer-redacted",
        "seats": 1,
        "max_devices": 1,
        "expires_at": _future(),
        "renews_at": _future(20),
        "order_ref": "order-redacted",
    }
    payload.update(overrides)
    response = client.post(
        "/api/admin/subscriptions",
        headers={"x-lengrvis-admin-csrf": csrf},
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_admin_password_hash_round_trips() -> None:
    encoded = hash_admin_password("secret", salt=b"0123456789abcdef", iterations=1000)

    assert verify_admin_password("secret", encoded)
    assert not verify_admin_password("wrong", encoded)
    assert "secret" not in encoded


def test_admin_requires_login_and_csrf(monkeypatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path)
    client = TestClient(_app())

    assert client.get("/admin").status_code == 200
    assert client.get("/api/admin/subscriptions").status_code == 401
    assert client.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    csrf = _login(client)

    assert (
        client.post(
            "/api/admin/subscriptions",
            json={
                "plan": "pro",
                "subscription_id": "sub_missing_csrf",
                "status": "active",
                "expires_at": _future(),
            },
        ).status_code
        == 403
    )
    assert csrf


def test_admin_login_rate_limits_failed_attempts(monkeypatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_MAX", "0")
    client = TestClient(_app())

    for _ in range(10):
        assert client.post("/api/admin/login", json={"password": "wrong"}).status_code == 401

    blocked = client.post("/api/admin/login", json={"password": "wrong"})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "admin_rate_limited"


def test_admin_mutation_rate_limits_state_changing_endpoints(monkeypatch, tmp_path: Path) -> None:
    import app.api.routes_activation_admin as admin_routes

    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(admin_routes, "_ADMIN_MUTATION_RATE_LIMIT_MAX", 2)
    client = TestClient(_app())
    csrf = _login(client)

    assert _create_key(client, csrf, subscription_id="sub_rate_001")["record"]["subscription_id"] == "sub_rate_001"
    assert _create_key(client, csrf, subscription_id="sub_rate_002")["record"]["subscription_id"] == "sub_rate_002"

    blocked = client.post(
        "/api/admin/subscriptions",
        headers={"x-lengrvis-admin-csrf": csrf},
        json={
            "plan": "pro",
            "subscription_id": "sub_rate_003",
            "status": "active",
            "expires_at": _future(),
        },
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "admin_rate_limited"

    assert client.get("/api/admin/subscriptions").status_code == 200


def test_admin_create_list_renew_revoke_and_unbind(monkeypatch, tmp_path: Path) -> None:
    db_path = _configure(monkeypatch, tmp_path)
    client = TestClient(_app())
    csrf = _login(client)

    created = _create_key(client, csrf)
    activation_key = created["activation_key"]
    key_hash = created["record"]["key_hash"]
    assert activation_key.startswith("lgrv_")
    assert created["record"]["plan"] == "pro"

    listed = client.get("/api/admin/subscriptions").json()["items"]
    assert len(listed) == 1
    assert "activation_key" not in str(listed)
    assert listed[0]["key_hash"] == key_hash

    activated = activate_subscription_key(
        ActivationRequest(
            activation_key=activation_key,
            device_id="dev_admin_one",
            device_fingerprint="fp_admin_one",
            device_profile={"os": "windows", "arch": "x64", "device_name": "raw-device"},
            app_version="desktop",
        ),
        db_path=db_path,
    )
    listed = client.get("/api/admin/subscriptions").json()["items"]
    assert listed[0]["device_count"] == 1
    assert listed[0]["devices"][0]["license_id"] == activated.license_id
    assert "dev_admin_one" not in listed[0]["devices"][0]["device_label"]
    assert listed[0]["devices"][0]["device_fingerprint_label"].startswith("fp_admin")
    assert listed[0]["devices"][0]["device_profile"]["os"] == "windows"
    assert "device_name" not in listed[0]["devices"][0]["device_profile"]

    renew = client.post(
        f"/api/admin/subscriptions/{key_hash}/renew",
        headers={"x-lengrvis-admin-csrf": csrf},
        json={
            "status": "active",
            "expires_at": _future(60),
            "renews_at": _future(50),
            "cancel_at_period_end": False,
            "seats": 2,
            "max_devices": 2,
        },
    )
    assert renew.status_code == 200
    assert renew.json()["record"]["max_devices"] == 2

    revoke = client.post(
        f"/api/admin/subscriptions/{key_hash}/revoke",
        headers={"x-lengrvis-admin-csrf": csrf},
    )
    assert revoke.status_code == 200
    assert revoke.json()["record"]["status"] == "revoked"
    assert revoke.json()["record"]["revocation_manifest_required"] is True
    assert revoke.json()["record"]["revoked_license_ids"] == [activated.license_id]

    unbind = client.delete(
        f"/api/admin/devices/{activated.license_id}",
        headers={"x-lengrvis-admin-csrf": csrf},
    )
    assert unbind.status_code == 200
    listed = client.get("/api/admin/subscriptions").json()["items"]
    assert listed[0]["device_count"] == 0


def test_admin_create_generates_subscription_id_when_blank(monkeypatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path)
    client = TestClient(_app())
    csrf = _login(client)

    created = _create_key(
        client,
        csrf,
        plan="max",
        subscription_id="",
        expires_at=_future(),
    )

    subscription_id = created["record"]["subscription_id"]
    assert subscription_id.startswith("sub_max_")
    assert len(subscription_id.split("_")) == 4

    listed = client.get("/api/admin/subscriptions").json()["items"]
    assert listed[0]["subscription_id"] == subscription_id
    assert created["activation_key"] not in str(listed)


def test_admin_can_issue_free_pro_max_keys(monkeypatch, tmp_path: Path) -> None:
    db_path = _configure(monkeypatch, tmp_path)
    client = TestClient(_app())
    csrf = _login(client)

    for plan in ("free", "pro", "max"):
        created = _create_key(
            client,
            csrf,
            plan=plan,
            subscription_id=f"sub_{plan}_001",
            expires_at=_future(),
        )
        result = activate_subscription_key(
            ActivationRequest(
                created["activation_key"],
                f"dev_{plan}_one",
                device_fingerprint=f"fp_{plan}_one",
            ),
            db_path=db_path,
        )
        license_ = parse_license(result.license_token, PUBLIC_KEY)

        assert result.plan.value == plan
        assert license_.plan.value == plan


def test_admin_page_uses_expiry_presets_and_renew_panel() -> None:
    client = TestClient(_app())

    html = client.get("/admin").text

    assert 'id="planSegment"' in html
    assert "Free" in html
    assert "Pro" in html
    assert "Max" in html
    assert 'id="expiresPreset"' in html
    assert "7 天试用" in html
    assert "30 天月付" in html
    assert 'id="searchBox"' in html
    assert 'id="planFilter"' in html
    assert 'id="statusFilter"' in html
    assert "详情与操作" in html
    assert "输入 撤销" in html
    assert "下载交接文本" in html
    assert "我已把授权码交接到安全位置" in html
    assert 'id="renewPanel"' in html
    assert "从当前到期日或今天较晚者起算" in html
    assert "expires_at: null, renews_at: null" in html
    assert "新的到期时间 ISO 时间戳" not in html
