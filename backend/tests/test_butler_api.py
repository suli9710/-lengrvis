from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from native_confirmation_helpers import signed_native_confirmation_headers

from app.api.routes_butler import router
from app.core import butler_store as store
from app.core.butler_models import AutonomyScope, ButlerMatter, MatterStatus


def test_scope_creation_requires_single_use_native_confirmation_bound_to_its_resources(monkeypatch, tmp_path):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    monkeypatch.setenv(
        "LENGRVIS_NATIVE_CONFIRMATION_PUBLIC_KEY", base64.urlsafe_b64encode(public).rstrip(b"=").decode()
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    body = {"label": "工作目录", "directories": [str(tmp_path)], "allowed_actions": ["observe", "file_modify"]}
    with TestClient(app) as client:
        assert client.post("/api/butler/scopes", json=body).status_code == 403
        challenge = client.post("/api/butler/scopes/native-confirmation-challenge", json=body)
        assert challenge.status_code == 200, challenge.text
        headers = signed_native_confirmation_headers(challenge.json(), key)
        changed = client.post("/api/butler/scopes", json={**body, "allowed_actions": ["observe"]}, headers=headers)
        assert changed.status_code == 403
        accepted = client.post("/api/butler/scopes", json=body, headers=headers)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["authorization"]["desktop_native_confirmed"] is True
        assert client.post("/api/butler/scopes", json=body, headers=headers).status_code == 403
        scope_id = accepted.json()["id"]
        matter = store.save(ButlerMatter(goal="继续整理", source="event", scope_versions={scope_id: 1}))
        edit = {**body, "id": scope_id, "version": 1, "allowed_actions": ["observe"]}
        challenge = client.post("/api/butler/scopes/native-confirmation-challenge", json=edit)
        updated = client.post(
            "/api/butler/scopes", json=edit, headers=signed_native_confirmation_headers(challenge.json(), key)
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == 2
        assert store.get(ButlerMatter, matter.id).status == MatterStatus.PAUSED
        assert client.post("/api/butler/scopes/native-confirmation-challenge", json=edit).status_code == 409
    assert len(store.list_records(AutonomyScope)) == 1


def test_invalid_scope_and_message_contracts_do_not_reach_execution():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        response = client.post(
            "/api/butler/scopes/native-confirmation-challenge", json={"label": "bad", "allowed_actions": ["payment"]}
        )
        assert response.status_code == 422
        assert client.post("/api/butler/messages", json={"message": "hi", "mode": "unrestricted"}).status_code == 422
