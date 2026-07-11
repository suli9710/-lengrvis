from __future__ import annotations

from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import Approval
from app.main import app
from app.security.mobile_jwt import issue_mobile_token
from app.services import mobile_pairing_service


def _paired_device_token(device_id: str = "mobile_push_device") -> str:
    mobile_pairing_service._upsert_mobile_device(device_id=device_id, device_name="Push phone")
    return issue_mobile_token(device_id=device_id, device_name="Push phone")


def test_mobile_device_can_register_and_remove_its_push_subscription() -> None:
    db.init_db()
    token = _paired_device_token()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    unauthenticated = client.put(
        "/api/mobile/push-subscription",
        json={"provider": "expo", "token": "ExponentPushToken[pytest-device]"},
    )
    registered = client.put(
        "/api/mobile/push-subscription",
        headers=headers,
        json={"provider": "expo", "token": "ExponentPushToken[pytest-device]"},
    )

    assert unauthenticated.status_code == 401
    assert registered.status_code == 200
    assert registered.json() == {"status": "registered", "provider": "expo"}
    stored = db.fetch_one("mobile_devices", "mobile_push_device")
    assert stored is not None
    assert stored["push_subscription"]["token"] == "ExponentPushToken[pytest-device]"

    removed = client.delete("/api/mobile/push-subscription", headers=headers)

    assert removed.status_code == 200
    assert removed.json() == {"status": "unregistered"}
    assert "push_subscription" not in (db.fetch_one("mobile_devices", "mobile_push_device") or {})


def test_approval_push_contains_only_routing_metadata_and_targets_registered_devices() -> None:
    from app.services import mobile_push_service

    db.init_db()
    claims = {"device_id": "mobile_push_target", "scope": "mobile:approval"}
    mobile_pairing_service._upsert_mobile_device(device_id=claims["device_id"], device_name="Push phone")
    mobile_pairing_service.register_mobile_push_subscription(
        claims,
        provider="expo",
        push_token="ExponentPushToken[pytest-target]",  # noqa: S106 - deterministic fake push token.
    )
    client = _FakeExpoClient()
    approval = {
        "id": "approval_push_1",
        "task_id": "task_push_1",
        "approval_type": "tool_call",
        "message": "secret file body must never leave the computer",
        "diff_preview": {"secret": "must-not-leak"},
        "status": "pending",
    }

    result = mobile_push_service.send_approval_pushes(approval, client=client)

    assert result == {"attempted": 1, "sent": 1, "removed": 0}
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request["url"] == "https://exp.host/--/api/v2/push/send"
    assert request["timeout"] == 3.0
    assert request["json"] == [
        {
            "to": "ExponentPushToken[pytest-target]",
            "title": "Lengrvis 需要你审批",
            "body": "有任务等待审批，打开 App 查看详情。",
            "sound": "default",
            "priority": "high",
            "channelId": "approvals",
            "data": {"approvalId": "approval_push_1", "kind": "approval"},
        }
    ]
    assert "secret" not in str(request).lower()


def test_created_approval_enqueues_background_push_without_changing_event_payload(monkeypatch) -> None:
    from app.services import approval_event_service, mobile_push_service

    queued: list[Approval] = []
    monkeypatch.setattr(mobile_push_service, "enqueue_approval_push", queued.append)
    approval = Approval(
        id="approval_push_enqueue",
        task_id="task_push_enqueue",
        message="the push worker, not the event bus, owns notification redaction",
    )

    approval_event_service.publish_approval_created(approval)

    assert queued == [approval]


class _FakeExpoResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"data": [{"status": "ok", "id": "ticket-1"}]}


class _FakeExpoClient:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def post(self, url: str, **kwargs):
        self.requests.append({"url": url, **kwargs})
        return _FakeExpoResponse()


def test_revoking_device_sessions_removes_push_subscription() -> None:
    db.init_db()
    device_id = "mobile_push_revoked"
    claims = {"device_id": device_id, "scope": "mobile:approval"}
    mobile_pairing_service._upsert_mobile_device(device_id=device_id, device_name="Push phone")
    mobile_pairing_service.register_mobile_push_subscription(
        claims,
        provider="expo",
        push_token="ExponentPushToken[pytest-revoked]",  # noqa: S106 - deterministic fake push token.
    )

    mobile_pairing_service.revoke_mobile_device_sessions(device_id)

    assert "push_subscription" not in (db.fetch_one("mobile_devices", device_id) or {})
