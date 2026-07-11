from __future__ import annotations

import base64
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_activation import router as activation_router
from app.api.routes_activation_admin import (
    ADMIN_PASSWORD_HASH_ENV_VAR,
    ADMIN_SESSION_SECRET_ENV_VAR,
    hash_admin_password,
)
from app.api.routes_activation_admin import (
    router as admin_router,
)
from app.api.routes_commerce import router as commerce_router
from app.commerce.activation import (
    ACTIVATION_BASE_URL_ENV_VAR,
    ACTIVATION_KEY_PEPPER_ENV_VAR,
    ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF_ENV_VAR,
    ACTIVATION_SERVER_DEVICE_SECRET_ENV_VAR,
    ACTIVATION_SIGNING_PRIVATE_KEY_ENV_VAR,
    ActivationError,
    ActivationRefreshRequest,
    ActivationRequest,
    activate_license_with_server,
    activate_subscription_key,
    refresh_license_with_server,
    refresh_subscription_license,
    revoke_subscription_key,
    upsert_subscription_key,
)
from app.commerce.entitlements import Feature
from app.commerce.licensing import (
    LICENSE_PUBLIC_KEY_ENV_VAR,
    license_status,
    load_license,
    parse_license,
    sign_license,
)
from app.core.errors import register_error_handlers
from app.security.desktop_api import DESKTOP_API_TOKEN_HEADER
from app.security.middleware import register_security_middleware

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
VALID_NONCE = "nonce-redacted-123456"


class _Settings:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = str(data_dir)


def _future() -> datetime:
    return datetime.now(UTC) + timedelta(days=30)


def _configure_server(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    monkeypatch.setenv(ACTIVATION_KEY_PEPPER_ENV_VAR, "pepper-redacted")
    monkeypatch.setenv(ACTIVATION_SERVER_DEVICE_SECRET_ENV_VAR, "server-device-secret-redacted")
    monkeypatch.setenv(ACTIVATION_SIGNING_PRIVATE_KEY_ENV_VAR, PRIVATE_KEY)
    monkeypatch.setenv(LICENSE_PUBLIC_KEY_ENV_VAR, PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_DB", str(db_path))


def test_activation_server_issues_signed_pro_license(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    upsert_subscription_key(
        activation_key="key-valid",
        plan="pro",
        subscription_id="sub_001",
        status="active",
        subject="subject-redacted",
        max_devices=2,
        expires_at=_future(),
        db_path=db_path,
    )

    result = activate_subscription_key(
        ActivationRequest(
            activation_key="key-valid",
            device_id="dev_redacted_001",
            device_fingerprint="fp_valid",
            device_profile={"os": "windows", "arch": "x64", "hostname": "raw-device-name"},
            app_version="desktop",
            nonce=VALID_NONCE,
        ),
        db_path=db_path,
    )

    license_ = parse_license(result.license_token, PUBLIC_KEY)
    assert result.plan.value == "pro"
    assert license_.plan.value == "pro"
    assert license_.plan_catalog == "free-plus-pro-v1"
    assert license_.subscription_id == "sub_001"
    assert license_.subscription_status == "active"
    assert license_.device_id == "dev_redacted_001"
    assert license_.device_fingerprint == "fp_valid"
    assert "key-valid" not in result.license_token


def test_activation_repeat_for_same_device_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    upsert_subscription_key(
        activation_key="key-repeat",
        plan="pro",
        subscription_id="sub_repeat",
        status="trialing",
        expires_at=_future(),
        db_path=db_path,
    )
    request = ActivationRequest(
        activation_key="key-repeat", device_id="dev_same", device_fingerprint="fp_repeat", nonce=VALID_NONCE
    )

    first = activate_subscription_key(request, db_path=db_path)
    second = activate_subscription_key(request, db_path=db_path)

    assert second.reused_device is True
    assert second.license_id == first.license_id


def test_activation_server_rejects_weak_device_proof_when_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    monkeypatch.setenv(ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF_ENV_VAR, "true")
    upsert_subscription_key(
        activation_key="key-weak-proof",
        plan="pro",
        subscription_id="sub_weak_proof",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )

    with pytest.raises(ActivationError) as excinfo:
        activate_subscription_key(
            ActivationRequest(
                activation_key="key-weak-proof",
                device_id="dev_weak_proof",
                nonce=VALID_NONCE,
                device_fingerprint="fp_weak_proof",
                device_profile={"binding_strength": "install_only", "secret_storage": "plaintext"},
            ),
            db_path=db_path,
        )

    assert excinfo.value.code == "activation_device_proof_weak"


def test_activation_server_signs_strong_device_binding_claim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    monkeypatch.setenv(ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF_ENV_VAR, "true")
    upsert_subscription_key(
        activation_key="key-strong-proof",
        plan="pro",
        subscription_id="sub_strong_proof",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )

    result = activate_subscription_key(
        ActivationRequest(
            activation_key="key-strong-proof",
            device_id="dev_strong_proof",
            nonce=VALID_NONCE,
            device_fingerprint="fp_strong_proof",
            device_profile={
                "fingerprint": "fp_strong_proof",
                "binding_strength": "strong",
                "secret_storage": "dpapi",
                "hardware_signal_count": 1,
                "install_hash": "install_hash_redacted",
                "machine_id_hash": "machine_hash_redacted",
                "signals": ["machine_id_hash"],
            },
        ),
        db_path=db_path,
    )

    license_ = parse_license(result.license_token, PUBLIC_KEY)
    assert license_.payload["activation"]["device_binding"] == {
        "strength": "strong",
        "secret_storage": "dpapi",
        "hardware_signal_count": 1,
        "fingerprint": "fp_strong_proof",
    }


def test_activation_server_rejects_strong_device_profile_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    monkeypatch.setenv(ACTIVATION_REQUIRE_STRONG_DEVICE_PROOF_ENV_VAR, "true")
    upsert_subscription_key(
        activation_key="key-profile-mismatch",
        plan="pro",
        subscription_id="sub_profile_mismatch",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )

    with pytest.raises(ActivationError) as excinfo:
        activate_subscription_key(
            ActivationRequest(
                activation_key="key-profile-mismatch",
                device_id="dev_profile_mismatch",
                nonce=VALID_NONCE,
                device_fingerprint="fp_actual",
                device_profile={
                    "fingerprint": "fp_other",
                    "binding_strength": "strong",
                    "secret_storage": "dpapi",
                    "hardware_signal_count": 1,
                    "install_hash": "install_hash_redacted",
                    "machine_id_hash": "machine_hash_redacted",
                    "signals": ["machine_id_hash"],
                },
            ),
            db_path=db_path,
        )

    assert excinfo.value.code == "activation_device_profile_mismatch"


def test_activation_server_requires_separate_device_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    monkeypatch.delenv(ACTIVATION_SERVER_DEVICE_SECRET_ENV_VAR, raising=False)
    upsert_subscription_key(
        activation_key="key-no-device-secret",
        plan="pro",
        subscription_id="sub_no_device_secret",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )

    with pytest.raises(ActivationError) as excinfo:
        activate_subscription_key(
            ActivationRequest(
                activation_key="key-no-device-secret",
                device_id="dev_no_device_secret",
                nonce=VALID_NONCE,
                device_fingerprint="fp_no_device_secret",
            ),
            db_path=db_path,
        )

    assert excinfo.value.code == "activation_server_unconfigured"


def test_activation_device_limit_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    upsert_subscription_key(
        activation_key="key-device-limit",
        plan="pro",
        subscription_id="sub_limit",
        status="active",
        max_devices=1,
        expires_at=_future(),
        db_path=db_path,
    )

    activate_subscription_key(
        ActivationRequest("key-device-limit", "dev_one", nonce=VALID_NONCE, device_fingerprint="fp_one"),
        db_path=db_path,
    )
    with pytest.raises(ActivationError) as excinfo:
        activate_subscription_key(
            ActivationRequest("key-device-limit", "dev_two", nonce=VALID_NONCE, device_fingerprint="fp_two"),
            db_path=db_path,
        )

    assert excinfo.value.code == "activation_device_limit"
    assert excinfo.value.status_code == 409


def test_activation_device_fingerprint_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    upsert_subscription_key(
        activation_key="key-fingerprint",
        plan="pro",
        subscription_id="sub_fingerprint",
        status="active",
        max_devices=1,
        expires_at=_future(),
        db_path=db_path,
    )

    first = activate_subscription_key(
        ActivationRequest(
            activation_key="key-fingerprint",
            device_id="dev_same_install",
            nonce=VALID_NONCE,
            device_fingerprint="fp_one",
            device_profile={"os": "windows", "arch": "x64"},
        ),
        db_path=db_path,
    )
    with pytest.raises(ActivationError) as excinfo:
        activate_subscription_key(
            ActivationRequest(
                activation_key="key-fingerprint",
                device_id="dev_same_install",
                nonce=VALID_NONCE,
                device_fingerprint="fp_other",
                device_profile={"os": "linux", "arch": "x64"},
            ),
            db_path=db_path,
        )

    assert excinfo.value.code == "activation_device_fingerprint_mismatch"
    reinstalled = activate_subscription_key(
        ActivationRequest(
            activation_key="key-fingerprint",
            device_id="dev_reinstalled",
            nonce=VALID_NONCE,
            device_fingerprint="fp_one",
            device_profile={"os": "windows", "arch": "x64"},
        ),
        db_path=db_path,
    )
    assert reinstalled.reused_device is True
    assert reinstalled.license_id == first.license_id
    assert parse_license(reinstalled.license_token, PUBLIC_KEY).device_id == "dev_reinstalled"
    assert first.license_id


def test_activation_rejects_inactive_subscription(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    upsert_subscription_key(
        activation_key="key-past-due",
        plan="max",
        subscription_id="sub_due",
        status="past_due",
        expires_at=_future(),
        db_path=db_path,
    )

    with pytest.raises(ActivationError) as excinfo:
        activate_subscription_key(
            ActivationRequest("key-past-due", "dev_one", nonce=VALID_NONCE, device_fingerprint="fp_due"),
            db_path=db_path,
        )

    assert excinfo.value.code == "subscription_past_due"


def test_activation_route_returns_license_without_echoing_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_MAX", "0")
    upsert_subscription_key(
        activation_key="key-route",
        plan="pro",
        subscription_id="sub_route",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(activation_router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/v1/activations",
        json={
            "activation_key": "key-route",
            "device_id": "dev_route",
            "device_fingerprint": "fp_route",
            "app_version": "desktop",
            "nonce": "nonce-route-redacted-123456",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == "pro"
    assert body["subscription_id"] == "sub_route"
    assert "key-route" not in str(body)


def test_activation_route_records_safe_audit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import app.api.routes_activation as activation_routes

    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    upsert_subscription_key(
        activation_key="key-route-audit",
        plan="pro",
        subscription_id="sub_route_audit",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )
    audit_events: list[tuple[str, dict[str, Any] | None, str]] = []
    monkeypatch.setattr(
        activation_routes,
        "record_activation_audit",
        lambda event_type, *, result=None, code="", client_ref="": audit_events.append(
            (
                event_type,
                {
                    "license_id": result.license_id,
                    "subscription_id": result.subscription_id,
                    "plan": result.plan.value,
                }
                if result is not None
                else None,
                client_ref,
            )
        ),
    )
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(activation_router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/v1/activations",
        json={
            "activation_key": "key-route-audit",
            "device_id": "dev_route_audit",
            "device_fingerprint": "fp_route_audit",
            "app_version": "desktop",
            "nonce": "nonce-route-audit-redacted-123456",
        },
    )

    assert response.status_code == 200, response.text
    assert audit_events[0][0] == "activation.license.issued"
    assert audit_events[0][1]["subscription_id"] == "sub_route_audit"
    assert audit_events[0][2].startswith("client_")
    assert "key-route-audit" not in str(audit_events)


def test_activation_route_requires_nonce(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    upsert_subscription_key(
        activation_key="key-route-no-nonce",
        plan="pro",
        subscription_id="sub_route_no_nonce",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(activation_router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/v1/activations",
        json={
            "activation_key": "key-route-no-nonce",
            "device_id": "dev_route_no_nonce",
            "device_fingerprint": "fp_route_no_nonce",
            "app_version": "desktop",
        },
    )

    assert response.status_code == 422


def test_activation_route_is_not_desktop_token_guarded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_MAX", "0")
    upsert_subscription_key(
        activation_key="key-public",
        plan="pro",
        subscription_id="sub_public",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )
    app = FastAPI()
    register_security_middleware(app)
    register_error_handlers(app)
    app.include_router(activation_router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/v1/activations",
        json={
            "activation_key": "key-public",
            "device_id": "dev_public",
            "device_fingerprint": "fp_public",
            "app_version": "desktop",
            "nonce": "nonce-public-redacted-123456",
        },
    )

    assert response.status_code == 200


def test_full_backend_does_not_expose_activation_issuance_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.main import create_app as create_full_backend_app

    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "full-backend-data"))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-token-redacted")
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", "0")
    upsert_subscription_key(
        activation_key="key-full-backend",
        plan="pro",
        subscription_id="sub_full_backend",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )
    client = TestClient(create_full_backend_app())

    response = client.post(
        "/api/v1/activations",
        headers={DESKTOP_API_TOKEN_HEADER: "desktop-token-redacted"},
        json={
            "activation_key": "key-full-backend",
            "device_id": "dev_full_backend",
            "app_version": "desktop",
            "nonce": "nonce-full-backend-redacted-123456",
        },
    )

    assert response.status_code == 404


def test_activation_only_app_exposes_activation_issuance_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from activation_main import create_app as create_activation_app

    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_MAX", "0")
    upsert_subscription_key(
        activation_key="key-activation-only",
        plan="pro",
        subscription_id="sub_activation_only",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )
    client = TestClient(create_activation_app())

    response = client.post(
        "/api/v1/activations",
        json={
            "activation_key": "key-activation-only",
            "device_id": "dev_activation_only",
            "device_fingerprint": "fp_activation_only",
            "app_version": "desktop",
            "nonce": "nonce-activation-only-redacted-123456",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == "pro"
    assert body["subscription_id"] == "sub_activation_only"
    assert "key-activation-only" not in str(body)


def test_activation_refresh_route_issues_new_signed_license(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_MAX", "0")
    upsert_subscription_key(
        activation_key="key-refresh-route",
        plan="pro",
        subscription_id="sub_refresh_route",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )
    activated = activate_subscription_key(
        ActivationRequest(
            activation_key="key-refresh-route",
            device_id="dev_refresh_route",
            nonce=VALID_NONCE,
            device_fingerprint="fp_refresh_route",
            app_version="desktop-old",
        ),
        db_path=db_path,
    )
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(activation_router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/v1/licenses/refresh",
        json={
            "license_token": activated.license_token,
            "device_id": "dev_refresh_route",
            "device_fingerprint": "fp_refresh_route",
            "app_version": "desktop-new",
            "nonce": "nonce-refresh-route-redacted-123456",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    refreshed = parse_license(body["license_token"], PUBLIC_KEY)
    assert body["license_id"] == activated.license_id
    assert body["subscription_id"] == "sub_refresh_route"
    assert refreshed.license_id == activated.license_id
    assert refreshed.subscription_status == "active"
    assert refreshed.payload["activation"]["source"] == "activation_server"


def test_activation_refresh_rejects_revoked_subscription(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    _configure_server(monkeypatch, db_path)
    created = upsert_subscription_key(
        activation_key="key-refresh-revoked",
        plan="pro",
        subscription_id="sub_refresh_revoked",
        status="active",
        expires_at=_future(),
        db_path=db_path,
    )
    activated = activate_subscription_key(
        ActivationRequest(
            activation_key="key-refresh-revoked",
            device_id="dev_refresh_revoked",
            nonce=VALID_NONCE,
            device_fingerprint="fp_refresh_revoked",
        ),
        db_path=db_path,
    )
    revoke_subscription_key(key_hash=created["key_hash"], db_path=db_path)

    with pytest.raises(ActivationError) as excinfo:
        refresh_subscription_license(
            ActivationRefreshRequest(
                license_token=activated.license_token,
                device_id="dev_refresh_revoked",
                nonce=VALID_NONCE,
                device_fingerprint="fp_refresh_revoked",
            ),
            db_path=db_path,
        )

    assert excinfo.value.code == "subscription_revoked"
    assert excinfo.value.status_code == 402


def test_subscription_activation_lifecycle_is_closed_across_admin_activation_and_desktop_routes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "activation.sqlite"
    desktop_data_dir = tmp_path / "desktop-data"
    _configure_server(monkeypatch, db_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(desktop_data_dir))
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")
    monkeypatch.setenv("LENGRVIS_ACTIVATION_RATE_LIMIT_MAX", "0")
    monkeypatch.setenv("LENGRVIS_ACTIVATION_BASE_URL", "https://activation.example")
    monkeypatch.setenv("LENGRVIS_SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS", "1")
    monkeypatch.delenv("LENGRVIS_COMMERCIAL_RELEASE", raising=False)
    monkeypatch.delenv("LENGRVIS_LICENSE_KEY", raising=False)
    monkeypatch.setenv(ADMIN_SESSION_SECRET_ENV_VAR, "admin-session-redacted")
    monkeypatch.setenv(
        ADMIN_PASSWORD_HASH_ENV_VAR,
        hash_admin_password("correct-password", salt=b"0123456789abcdef", iterations=1000),
    )

    activation_admin_app = FastAPI()
    register_error_handlers(activation_admin_app)
    activation_admin_app.include_router(activation_router, prefix="/api")
    activation_admin_app.include_router(admin_router)
    activation_admin_client = TestClient(activation_admin_app)

    commerce_app = FastAPI()
    register_error_handlers(commerce_app)
    commerce_app.include_router(commerce_router, prefix="/api")
    commerce_client = TestClient(commerce_app)

    class _ActivationHttpClient:
        def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
            path = urlparse(url).path
            return activation_admin_client.post(path, json=json)

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.commerce.activation.httpx.Client", lambda timeout: _ActivationHttpClient())

    login = activation_admin_client.post("/api/admin/login", json={"password": "correct-password"})
    assert login.status_code == 200
    csrf = activation_admin_client.cookies.get("lengrvis_admin_csrf")
    assert csrf
    admin_headers = {"x-lengrvis-admin-csrf": csrf}

    create = activation_admin_client.post(
        "/api/admin/subscriptions",
        headers=admin_headers,
        json={
            "plan": "pro",
            "subscription_id": "sub_lifecycle_closed",
            "status": "active",
            "subject": "lifecycle-customer-redacted",
            "seats": 2,
            "max_devices": 2,
            "expires_at": _future().isoformat(),
            "renews_at": _future().isoformat(),
            "order_ref": "order-lifecycle-redacted",
        },
    )
    assert create.status_code == 200, create.text
    activation_key = create.json()["activation_key"]
    key_hash = create.json()["record"]["key_hash"]

    activated = commerce_client.post(
        "/api/commerce/license/activate",
        json={"activation_key": activation_key, "app_version": "desktop"},
    )
    assert activated.status_code == 200, activated.text
    activated_body = activated.json()
    assert activated_body["active"] is True
    assert activated_body["plan"] == "pro"
    assert activated_body["subscription_id"] == "sub_lifecycle_closed"
    assert activated_body["subscription_status"] == "active"
    assert activated_body["subscription_confirmation_fresh"] is True
    assert "license.key" in {path.name for path in desktop_data_dir.iterdir()}

    plan = commerce_client.get("/api/commerce/plan")
    assert plan.status_code == 200
    plan_body = plan.json()
    assert plan_body["plan"] == "pro"
    assert plan_body["features"][Feature.POLICY_MANAGEMENT.value] is True

    renewed_until = datetime.now(UTC) + timedelta(days=90)
    renew = activation_admin_client.post(
        f"/api/admin/subscriptions/{key_hash}/renew",
        headers=admin_headers,
        json={
            "status": "active",
            "expires_at": renewed_until.isoformat(),
            "renews_at": renewed_until.isoformat(),
            "cancel_at_period_end": False,
            "seats": 3,
            "max_devices": 3,
        },
    )
    assert renew.status_code == 200, renew.text
    time.sleep(1.1)

    refreshed_status = commerce_client.get("/api/commerce/license")
    assert refreshed_status.status_code == 200, refreshed_status.text
    refreshed_body = refreshed_status.json()
    assert refreshed_body["active"] is True
    assert refreshed_body["plan"] == "pro"
    assert refreshed_body["seats"] == 3
    assert refreshed_body["subscription_confirmation_fresh"] is True
    assert refreshed_body["license_id"] == activated_body["license_id"]

    revoke = activation_admin_client.post(f"/api/admin/subscriptions/{key_hash}/revoke", headers=admin_headers)
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["record"]["revocation_manifest_required"] is True
    time.sleep(1.1)

    revoked_status = commerce_client.get("/api/commerce/license")
    assert revoked_status.status_code == 200, revoked_status.text
    revoked_body = revoked_status.json()
    assert revoked_body["active"] is False
    assert revoked_body["state"] == "subscription_confirmation_failed"
    assert revoked_body["error_code"] == "subscription_revoked"

    delete_before_unbind = activation_admin_client.delete(
        f"/api/admin/subscriptions/{key_hash}",
        headers=admin_headers,
    )
    assert delete_before_unbind.status_code == 409
    assert delete_before_unbind.json()["error"]["code"] == "subscription_delete_has_devices"

    unbind = activation_admin_client.delete(
        f"/api/admin/devices/{activated_body['license_id']}",
        headers=admin_headers,
    )
    assert unbind.status_code == 200
    delete_after_unbind = activation_admin_client.delete(
        f"/api/admin/subscriptions/{key_hash}",
        headers=admin_headers,
    )
    assert delete_after_unbind.status_code == 200
    assert activation_admin_client.get("/api/admin/subscriptions").json()["items"] == []


def test_client_activation_verifies_and_persists_license(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ACTIVATION_BASE_URL_ENV_VAR, "https://activation.example")
    monkeypatch.setenv(LICENSE_PUBLIC_KEY_ENV_VAR, PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")
    seen: dict[str, Any] = {}

    class _Client:
        def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
            seen["url"] = url
            seen["json"] = json
            token = sign_license(
                {
                    "schema": 1,
                    "license_id": "lic_client",
                    "plan": "pro",
                    "subject": "subject-redacted",
                    "subscription_id": "sub_client",
                    "subscription_status": "active",
                    "issued_at": datetime.now(UTC).isoformat(),
                    "expires_at": _future().isoformat(),
                    "activation": {
                        "source": "activation_server",
                        "nonce_sha256": sha256(str(json["nonce"]).encode("utf-8")).hexdigest(),
                    },
                },
                PRIVATE_KEY,
            )
            return httpx.Response(200, json={"license_token": token}, request=httpx.Request("POST", url))

        def close(self) -> None:
            seen["closed"] = True

    license_ = activate_license_with_server(
        "key-client",
        _Settings(tmp_path),
        app_version="desktop",
        client=_Client(),
    )

    assert license_.license_id == "lic_client"
    persisted = (tmp_path / "license.key").read_text(encoding="utf-8").strip()
    assert parse_license(persisted, PUBLIC_KEY).license_id == "lic_client"
    assert (
        parse_license(persisted, PUBLIC_KEY).payload["activation"]["nonce_sha256"]
        == sha256(str(seen["json"]["nonce"]).encode("utf-8")).hexdigest()
    )
    assert seen["url"] == "https://activation.example/api/v1/activations"
    assert seen["json"]["activation_key"] == "key-client"
    assert seen["json"]["device_id"].startswith("dev_")
    assert seen["json"]["device_fingerprint"].startswith("fp_")
    assert seen["json"]["device_profile"]["fingerprint"] == seen["json"]["device_fingerprint"]
    assert "device_name" not in seen["json"]["device_profile"]


def test_client_activation_rejects_replayed_nonce_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ACTIVATION_BASE_URL_ENV_VAR, "https://activation.example")
    monkeypatch.setenv(LICENSE_PUBLIC_KEY_ENV_VAR, PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    class _Client:
        def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
            replayed_token = sign_license(
                {
                    "schema": 1,
                    "license_id": "lic_replayed",
                    "plan": "pro",
                    "subject": "subject-redacted",
                    "subscription_id": "sub_replayed",
                    "subscription_status": "active",
                    "issued_at": datetime.now(UTC).isoformat(),
                    "expires_at": _future().isoformat(),
                    "activation": {
                        "source": "activation_server",
                        "nonce_sha256": sha256(b"old-request-nonce").hexdigest(),
                    },
                },
                PRIVATE_KEY,
            )
            return httpx.Response(200, json={"license_token": replayed_token}, request=httpx.Request("POST", url))

        def close(self) -> None:
            pass

    with pytest.raises(ActivationError) as excinfo:
        activate_license_with_server("key-client", _Settings(tmp_path), client=_Client())

    assert excinfo.value.code == "activation_nonce_mismatch"
    assert not (tmp_path / "license.key").exists()


def test_client_refresh_without_persist_rejects_different_device_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ACTIVATION_BASE_URL_ENV_VAR, "https://activation.example")
    monkeypatch.setenv(LICENSE_PUBLIC_KEY_ENV_VAR, PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")
    seen: dict[str, Any] = {}

    class _Client:
        def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
            seen["url"] = url
            seen["json"] = json
            refreshed_token = sign_license(
                {
                    "schema": 1,
                    "license_id": "lic_refresh_fp_mismatch",
                    "plan": "pro",
                    "subject": "subject-redacted",
                    "subscription_id": "sub_refresh_fp_mismatch",
                    "subscription_status": "active",
                    "device_id": json["device_id"],
                    "device_fingerprint": "fp_other_machine",
                    "issued_at": datetime.now(UTC).isoformat(),
                    "expires_at": _future().isoformat(),
                    "activation": {
                        "source": "activation_server",
                        "nonce_sha256": sha256(str(json["nonce"]).encode("utf-8")).hexdigest(),
                    },
                },
                PRIVATE_KEY,
            )
            return httpx.Response(200, json={"license_token": refreshed_token}, request=httpx.Request("POST", url))

        def close(self) -> None:
            seen["closed"] = True

    with pytest.raises(ActivationError) as excinfo:
        refresh_license_with_server(
            "old-token-redacted",
            _Settings(tmp_path),
            client=_Client(),
            persist=False,
        )

    assert excinfo.value.code == "license_device_fingerprint_mismatch"
    assert seen["url"] == "https://activation.example/api/v1/licenses/refresh"
    assert seen["json"]["device_fingerprint"].startswith("fp_")
    assert seen["json"]["device_fingerprint"] != "fp_other_machine"
    assert not (tmp_path / "license.key").exists()


def test_client_refresh_without_persist_rejects_replayed_nonce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(ACTIVATION_BASE_URL_ENV_VAR, "https://activation.example")
    monkeypatch.setenv(LICENSE_PUBLIC_KEY_ENV_VAR, PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    class _Client:
        def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
            refreshed_token = sign_license(
                {
                    "schema": 1,
                    "license_id": "lic_refresh_replay",
                    "plan": "pro",
                    "subject": "subject-redacted",
                    "subscription_id": "sub_refresh_replay",
                    "subscription_status": "active",
                    "device_id": json["device_id"],
                    "device_fingerprint": json["device_fingerprint"],
                    "issued_at": datetime.now(UTC).isoformat(),
                    "expires_at": _future().isoformat(),
                    "activation": {
                        "source": "activation_server",
                        "nonce_sha256": sha256(b"old-refresh-nonce").hexdigest(),
                    },
                },
                PRIVATE_KEY,
            )
            return httpx.Response(200, json={"license_token": refreshed_token}, request=httpx.Request("POST", url))

        def close(self) -> None:
            pass

    with pytest.raises(ActivationError) as excinfo:
        refresh_license_with_server(
            "old-token-redacted",
            _Settings(tmp_path),
            client=_Client(),
            persist=False,
        )

    assert excinfo.value.code == "activation_nonce_mismatch"
    assert not (tmp_path / "license.key").exists()


def test_subscription_license_stale_refresh_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issued_at = datetime.now(UTC) - timedelta(days=3)
    monkeypatch.setenv(ACTIVATION_BASE_URL_ENV_VAR, "https://activation.example")
    monkeypatch.setenv(LICENSE_PUBLIC_KEY_ENV_VAR, PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS", "60")
    token = sign_license(
        {
            "schema": 1,
            "license_id": "lic_stale",
            "plan": "pro",
            "subject": "subject-redacted",
            "subscription_id": "sub_stale",
            "subscription_status": "active",
            "issued_at": issued_at.isoformat(),
            "expires_at": _future().isoformat(),
            "activation": {"source": "activation_server"},
        },
        PRIVATE_KEY,
    )
    (tmp_path / "license.key").write_text(token, encoding="utf-8")

    class _Client:
        def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
            return httpx.Response(
                402,
                json={"error": {"code": "subscription_revoked", "message": "Subscription is not active."}},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("app.commerce.activation.httpx.Client", lambda timeout: _Client())

    assert load_license(_Settings(tmp_path)) is None
    status = license_status(_Settings(tmp_path))
    assert status["state"] == "subscription_confirmation_failed"
    assert status["active"] is False
    assert status["error_code"] == "subscription_revoked"


def test_subscription_license_stale_refresh_success_persists_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    issued_at = datetime.now(UTC) - timedelta(days=3)
    refreshed_issued_at = datetime.now(UTC)
    monkeypatch.setenv(ACTIVATION_BASE_URL_ENV_VAR, "https://activation.example")
    monkeypatch.setenv(LICENSE_PUBLIC_KEY_ENV_VAR, PUBLIC_KEY)
    monkeypatch.setenv("LENGRVIS_SUBSCRIPTION_LICENSE_REFRESH_TTL_SECONDS", "60")
    token = sign_license(
        {
            "schema": 1,
            "license_id": "lic_refresh_success",
            "plan": "pro",
            "subject": "subject-redacted",
            "subscription_id": "sub_refresh_success",
            "subscription_status": "active",
            "issued_at": issued_at.isoformat(),
            "expires_at": _future().isoformat(),
            "activation": {"source": "activation_server"},
        },
        PRIVATE_KEY,
    )
    (tmp_path / "license.key").write_text(token, encoding="utf-8")
    seen: dict[str, Any] = {}

    class _Client:
        def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
            seen["url"] = url
            seen["json"] = json
            refreshed_token = sign_license(
                {
                    "schema": 1,
                    "license_id": "lic_refresh_success",
                    "plan": "pro",
                    "subject": "subject-redacted",
                    "subscription_id": "sub_refresh_success",
                    "subscription_status": "active",
                    "issued_at": refreshed_issued_at.isoformat(),
                    "expires_at": _future().isoformat(),
                    "activation": {
                        "source": "activation_server",
                        "nonce_sha256": sha256(str(json["nonce"]).encode("utf-8")).hexdigest(),
                    },
                },
                PRIVATE_KEY,
            )
            return httpx.Response(
                200,
                json={"license_token": refreshed_token},
                request=httpx.Request("POST", url),
            )

        def close(self) -> None:
            seen["closed"] = True

    monkeypatch.setattr("app.commerce.activation.httpx.Client", lambda timeout: _Client())

    license_ = load_license(_Settings(tmp_path))

    assert license_ is not None
    assert license_.license_id == "lic_refresh_success"
    persisted = (tmp_path / "license.key").read_text(encoding="utf-8").strip()
    assert (
        parse_license(persisted, PUBLIC_KEY).payload["activation"]["nonce_sha256"]
        == sha256(str(seen["json"]["nonce"]).encode("utf-8")).hexdigest()
    )
    assert seen["url"] == "https://activation.example/api/v1/licenses/refresh"
    assert seen["json"]["license_token"] == token
    assert seen["json"]["device_id"].startswith("dev_")
    status = license_status(_Settings(tmp_path))
    assert status["state"] == "active"
    assert status["subscription_confirmation_fresh"] is True


def test_client_activation_rejects_http_non_localhost(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ACTIVATION_BASE_URL_ENV_VAR, "http://activation.example")
    monkeypatch.setenv("LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS", "1")

    with pytest.raises(ActivationError) as excinfo:
        activate_license_with_server("key-client", _Settings(tmp_path))

    assert excinfo.value.code == "activation_https_required"
    assert not (tmp_path / "license.key").exists()
