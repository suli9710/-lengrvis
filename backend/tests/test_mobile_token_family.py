from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core import db
from app.main import app
from app.security.mobile_identity import create_mobile_session, rotate_mobile_refresh_token
from app.security.mobile_jwt import (
    MOBILE_ACCESS_TOKEN_MAX_TTL_SECONDS,
    MOBILE_ACCESS_TOKEN_MIN_TTL_SECONDS,
    TOKEN_SCOPE,
    decode_mobile_token,
    issue_mobile_token,
)
from app.services import mobile_pairing_service
from app.services.mobile_pairing_access import (
    _mobile_approval_approve_denial_reason,
    _mobile_approval_reject_denial_reason,
    _mobile_approval_requires_step_up,
)


def _new_session(device_name: str = "Token Family Phone"):
    device_id = mobile_pairing_service.new_device_id()
    mobile_pairing_service._upsert_mobile_device(device_id=device_id, device_name=device_name)
    return device_id, create_mobile_session(device_id=device_id, device_name=device_name, scopes=[TOKEN_SCOPE])


def test_mobile_session_uses_short_access_token_and_normalized_refresh_family(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db(force=True)

    device_id, session = _new_session()
    claims = decode_mobile_token(session.token)
    issued_at = datetime.fromtimestamp(float(claims["iat"]), UTC)
    expires_at = datetime.fromtimestamp(float(claims["exp"]), UTC)

    assert MOBILE_ACCESS_TOKEN_MIN_TTL_SECONDS <= (expires_at - issued_at).total_seconds()
    assert (expires_at - issued_at).total_seconds() <= MOBILE_ACCESS_TOKEN_MAX_TTL_SECONDS
    assert claims["device_id"] == device_id
    assert claims["family_id"] == session.token_family_id
    assert claims["credential_id"] == session.device_credential_id
    assert session.refresh_token not in session.token

    with db.connect() as conn:
        credential = conn.execute(
            "SELECT * FROM device_credentials WHERE id = ?",
            (session.device_credential_id,),
        ).fetchone()
        family = conn.execute("SELECT * FROM token_families WHERE id = ?", (session.token_family_id,)).fetchone()
        refresh = conn.execute(
            "SELECT * FROM mobile_refresh_tokens WHERE family_id = ?",
            (session.token_family_id,),
        ).fetchone()

    assert credential["device_id"] == device_id
    assert credential["status"] == "active"
    assert family["status"] == "active"
    assert family["current_generation"] == 0
    assert refresh["status"] == "active"
    assert refresh["secret_hash"] not in session.refresh_token
    assert session.refresh_token not in json.dumps(dict(refresh), ensure_ascii=False)


def test_refresh_rotation_detects_reuse_and_revokes_the_family(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db(force=True)
    device_id, first = _new_session()

    second, _device = rotate_mobile_refresh_token(first.refresh_token, scopes=[TOKEN_SCOPE])

    assert second.refresh_token != first.refresh_token
    assert second.token_family_id == first.token_family_id
    assert decode_mobile_token(second.token)["device_id"] == device_id

    with pytest.raises(HTTPException, match="reuse detected") as reused:
        rotate_mobile_refresh_token(first.refresh_token, scopes=[TOKEN_SCOPE])
    assert reused.value.status_code == 401

    with db.connect() as conn:
        family = conn.execute("SELECT * FROM token_families WHERE id = ?", (first.token_family_id,)).fetchone()
        statuses = {
            row["status"]
            for row in conn.execute(
                "SELECT status FROM mobile_refresh_tokens WHERE family_id = ?",
                (first.token_family_id,),
            ).fetchall()
        }
    assert family["status"] == "compromised"
    assert family["reuse_detected_at"]
    assert statuses == {"revoked"}
    assert db.fetch_one("mobile_devices", device_id)["token_epoch"] == 1

    with pytest.raises(HTTPException, match="session has been revoked"):
        decode_mobile_token(second.token)
    with pytest.raises(HTTPException, match="revoked"):
        rotate_mobile_refresh_token(second.refresh_token, scopes=[TOKEN_SCOPE])


def test_refresh_endpoint_rotates_without_a_bearer_access_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db(force=True)
    _device_id, session = _new_session()
    client = TestClient(app)

    response = client.post(
        "/api/mobile/session/refresh",
        json={"refresh_token": session.refresh_token},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "Bearer"  # noqa: S105 - protocol token type, not a secret.
    assert body["refresh_token"] != session.refresh_token
    assert body["token_family_id"] == session.token_family_id
    assert body["device_credential_id"] == session.device_credential_id
    assert decode_mobile_token(body["token"])["device_id"] == body["device_id"]

    reused = client.post(
        "/api/mobile/session/refresh",
        json={"refresh_token": session.refresh_token},
    )
    assert reused.status_code == 401
    assert "reuse detected" in str(reused.json()["detail"]).lower()


def test_device_revocation_closes_only_that_devices_refresh_families(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db(force=True)
    first_device, first = _new_session("First Phone")
    second_device, second = _new_session("Second Phone")

    mobile_pairing_service.revoke_mobile_device_sessions(first_device)

    with pytest.raises(HTTPException, match="revoked"):
        rotate_mobile_refresh_token(first.refresh_token, scopes=[TOKEN_SCOPE])
    rotated_second, _device = rotate_mobile_refresh_token(second.refresh_token, scopes=[TOKEN_SCOPE])
    assert decode_mobile_token(rotated_second.token)["device_id"] == second_device

    mobile_pairing_service.revoke_mobile_device(second_device)
    with db.connect() as conn:
        credential_status = conn.execute(
            "SELECT status FROM device_credentials WHERE id = ?",
            (second.device_credential_id,),
        ).fetchone()["status"]
    assert credential_status == "revoked"


def test_access_token_rechecks_family_and_credential_status(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db(force=True)
    _device_id, family_revoked = _new_session("Family Revocation Phone")
    _other_device_id, credential_revoked = _new_session("Credential Revocation Phone")

    with db.connect() as conn:
        conn.execute(
            "UPDATE token_families SET status = 'revoked' WHERE id = ?",
            (family_revoked.token_family_id,),
        )
        conn.execute(
            "UPDATE device_credentials SET status = 'revoked' WHERE id = ?",
            (credential_revoked.device_credential_id,),
        )

    with pytest.raises(HTTPException, match="family has been revoked"):
        decode_mobile_token(family_revoked.token)
    with pytest.raises(HTTPException, match="credential has been revoked"):
        decode_mobile_token(credential_revoked.token)


def test_high_impact_mobile_approval_requires_fresh_biometric_step_up(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db(force=True)
    device_id = mobile_pairing_service.new_device_id()
    mobile_pairing_service._upsert_mobile_device(device_id=device_id, device_name="Approval Phone")
    approval = {
        "approval_type": "tool_call",
        "tool_name": "browser.submit_form",
        "tool_effects": ["submit", "external_post"],
        "allowed_device_ids": [device_id],
        "required_mobile_scopes": [TOKEN_SCOPE],
    }
    ordinary_claims = decode_mobile_token(issue_mobile_token(device_id=device_id, device_name="Approval Phone"))
    stepped_up_claims = decode_mobile_token(
        issue_mobile_token(
            device_id=device_id,
            device_name="Approval Phone",
            step_up_method="biometric",
        )
    )

    assert _mobile_approval_approve_denial_reason(approval, ordinary_claims) == (
        "High-impact mobile approval requires a fresh biometric step-up."
    )
    assert _mobile_approval_approve_denial_reason(approval, stepped_up_claims) == (
        "High-impact mobile approval requires a fresh biometric step-up."
    )
    assert _mobile_approval_approve_denial_reason(approval, None) == ""
    assert _mobile_approval_reject_denial_reason(approval, ordinary_claims) == ""


@pytest.mark.parametrize(
    "approval",
    [
        {
            "tool_name": "browser.act",
            "model_action": {"args": {"action": {"kind": "click", "selector": "button[type=submit]"}}},
        },
        {
            "tool_name": "browser.cua_run",
            "diff_preview": {"actions": [{"action": "send_message", "url": "https://mail.example.test"}]},
        },
        {
            "tool_name": "browser.act",
            "runtime_fields": {"proposed_action": {"kind": "purchase"}},
        },
        {
            "tool_name": "browser.cua_run",
            "engineering_boundary": {
                "model_action": {"args": {"target_url": "https://shop.example.test/checkout"}}
            },
        },
        {
            "tool_name": "browser.cua_run",
            "model_action": {"args": {"instruction": "Click checkout and submit the order"}},
        },
    ],
)
def test_browser_action_parameters_require_mobile_step_up(approval: dict) -> None:
    assert _mobile_approval_requires_step_up(approval) is True


@pytest.mark.parametrize(
    "approval",
    [
        {"risk_level": "R3_DESTRUCTIVE_OR_SYSTEM", "tool_effects": ["delete"]},
        {"engineering_boundary": {"tool": {"risk_level": "R3_DESTRUCTIVE_OR_SYSTEM"}}},
        {"engineering_boundary": {"tool": {"destructive": True}}},
        {"tool_effects": ["execute_subprocess"]},
        {"tool_effects": ["credential_write"]},
        {"permission_mode": "danger-full-access"},
    ],
)
def test_r3_execute_credential_and_permission_approvals_require_mobile_step_up(approval: dict) -> None:
    assert _mobile_approval_requires_step_up(approval) is True


@pytest.mark.parametrize(
    "approval",
    [
        {
            "tool_name": "browser.act",
            "model_action": {
                "args": {
                    "action": {"kind": "scroll", "selector": "main", "url": "https://docs.example.test/guide"}
                }
            },
        },
        {
            "tool_name": "browser.cua_run",
            "diff_preview": {
                "actions": [{"action": "read", "selector": "article"}],
                "instruction": "Scroll and read the article",
            },
        },
        {
            "tool_name": "document.edit_docx",
            "model_action": {"args": {"selector": "button[type=submit]"}},
        },
    ],
)
def test_read_only_or_non_browser_parameters_do_not_require_mobile_step_up(approval: dict) -> None:
    assert _mobile_approval_requires_step_up(approval) is False
