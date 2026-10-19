from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from native_confirmation_helpers import signed_native_confirmation_headers
from test_privacy_erase import ERASE_ENDPOINT, _erase_headers, _public_key_b64, _seed_user_data, _setup_env

from app.core import db
from app.core.errors import AppError
from app.core.local_data_lifecycle import local_data_operation
from app.core.privacy_erase_state import load_erase_state
from app.core.schemas import Task
from app.main import create_app
from app.security.native_confirmation import NATIVE_CONFIRMATION_PUBLIC_KEY_ENV
from app.services import privacy_erase_service

BODY = {"confirm": "erase-local-data", "include_settings": False}


def _prepare(client: TestClient) -> dict:
    response = client.post(f"{ERASE_ENDPOINT}/prepare", json=BODY, headers=_erase_headers())
    assert response.status_code == 200, response.text
    return response.json()


def _reserved_request(client: TestClient, state: dict, endpoint: str = "", **values):
    return client.post(
        f"{ERASE_ENDPOINT}{endpoint}",
        json={**BODY, "operation_id": state["operation_id"], **values},
        headers={"x-lengrvis-erase-reservation-token": state["reservation_token"]},
    )


@pytest.mark.parametrize("expired", [False, True])
def test_prepare_rejects_busy_or_expired_proof_before_any_deletion(monkeypatch, tmp_path, expired):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    client = TestClient(create_app())
    if expired:
        from app.security import native_confirmation

        key = Ed25519PrivateKey.generate()
        monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, _public_key_b64(key))
        challenge = client.post(f"{ERASE_ENDPOINT}/native-confirmation-challenge", json=BODY).json()
        monkeypatch.setattr(native_confirmation.time, "time", lambda: challenge["expires_at_epoch"] + 1)
        response = client.post(
            f"{ERASE_ENDPOINT}/prepare", json=BODY, headers=signed_native_confirmation_headers(challenge, key)
        )
        assert response.status_code == 403
    else:
        with local_data_operation():
            response = client.post(f"{ERASE_ENDPOINT}/prepare", json=BODY, headers=_erase_headers())
        assert response.status_code == 409
    assert load_erase_state() is None
    assert db.fetch_many("tasks")
    assert list((tmp_path / "diagnostic-packages").glob("*.json"))


def test_reservation_excludes_new_writes_and_deferred_work_until_both_phases_complete(monkeypatch, tmp_path):
    from app.core.local_data_lifecycle import data_generation

    _setup_env(monkeypatch, tmp_path)
    generation = data_generation()
    app = create_app()

    @app.post("/api/erase-reservation-test-write")
    def write():
        db.upsert_model("tasks", Task(user_goal="must not be written"))
        return {"ok": True}

    client = TestClient(app)
    state = _prepare(client)
    response = client.post("/api/erase-reservation-test-write")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "local_data_erasing"
    with local_data_operation(expected_generation=generation) as current:
        assert not current
    with pytest.raises(AppError, match="清除尚未完成"), local_data_operation():
        pass
    incomplete = _reserved_request(client, state, "/complete", desktop_complete=True)
    assert incomplete.json()["progress"]["backend"] == "pending"
    assert client.post("/api/erase-reservation-test-write").status_code == 409
    assert _reserved_request(client, state).status_code == 200
    assert _reserved_request(client, state, "/complete", desktop_complete=True).json()["ok"] is True
    assert client.post("/api/erase-reservation-test-write").status_code == 200


def test_partial_erase_survives_restart_and_retries_same_operation(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    client = TestClient(create_app())
    state = _prepare(client)
    _reserved_request(client, state, "/complete", desktop_complete=True)
    original = privacy_erase_service._erase_managed_files

    def fail(_path):
        raise AppError("local_data_cleanup_incomplete", "synthetic cleanup failure", 500)

    monkeypatch.setattr(privacy_erase_service, "_erase_managed_files", fail)
    assert _reserved_request(client, state).status_code == 500
    progress = load_erase_state()
    assert progress["desktop"] == "complete"
    assert progress["backend"] == "incomplete"
    assert progress["database"] == "complete"
    assert progress["deleted"]["rows_by_table"]["tasks"] == 1
    assert db.fetch_many("tasks") == []
    assert list((tmp_path / "diagnostic-packages").glob("*.json"))
    # No in-memory reservation cache is needed: a new app sees the journal.
    restarted = TestClient(create_app())
    with pytest.raises(AppError), local_data_operation():
        pass
    resumed = _prepare(restarted)
    assert resumed["operation_id"] == state["operation_id"]
    assert resumed["desktop"] == "complete"
    assert _reserved_request(restarted, state).status_code == 403  # rotated capability
    monkeypatch.setattr(privacy_erase_service, "_erase_managed_files", original)
    restored = _reserved_request(restarted, resumed)
    assert restored.status_code == 200
    assert restored.json()["deleted"]["rows_by_table"]["tasks"] == 1
    # A lost successful backend reply may be repeated without new deletion.
    assert _reserved_request(restarted, resumed).json()["backend"] == "complete"
    assert _reserved_request(restarted, resumed, "/complete", desktop_complete=True).json()["ok"]
    assert not list((tmp_path / "diagnostic-packages").glob("*.json"))


def test_reservation_requires_native_confirmation_and_keeps_scope_bound(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    client = TestClient(create_app())
    assert client.post(f"{ERASE_ENDPOINT}/prepare", json=BODY).status_code == 403
    state = _prepare(client)
    assert client.post(ERASE_ENDPOINT, json={**BODY, "operation_id": state["operation_id"]}).status_code == 403
    response = client.post(
        f"{ERASE_ENDPOINT}/prepare", json={**BODY, "include_settings": True}, headers=_erase_headers()
    )
    assert response.status_code == 409
    assert load_erase_state()["operation_id"] == state["operation_id"]
