"""Best-effort background approval notifications for paired mobile devices.

Push messages intentionally contain only an approval id. Approval text,
previews, paths, task content, provider tokens, and project identifiers remain
on the local computer.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

import httpx

from app.core import db
from app.core.audit import record
from app.core.schemas import Approval
from app.security.mobile_jwt import TOKEN_SCOPE
from app.services import mobile_pairing_service

EXPO_PUSH_SEND_URL = "https://exp.host/--/api/v2/push/send"
PUSH_TIMEOUT_SECONDS = 3.0
_PUSH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lengrvis-mobile-push")


class _PushHttpClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...


def enqueue_approval_push(approval: Approval | dict[str, Any]) -> None:
    """Queue delivery without blocking approval creation or WebSocket fanout."""

    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    try:
        _PUSH_EXECUTOR.submit(send_approval_pushes, payload)
    except RuntimeError:
        # Interpreter shutdown or executor teardown must not break approval flow.
        return


def send_approval_pushes(
    approval: Approval | dict[str, Any],
    *,
    client: _PushHttpClient | None = None,
) -> dict[str, int]:
    payload = approval.model_dump(mode="json") if isinstance(approval, Approval) else dict(approval)
    approval_id = str(payload.get("id") or "").strip()
    if not approval_id:
        return {"attempted": 0, "sent": 0, "removed": 0}

    targets = _eligible_push_targets(payload)
    if not targets:
        return {"attempted": 0, "sent": 0, "removed": 0}

    messages = [_approval_push_message(target["token"], approval_id) for target in targets]
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    access_token = os.environ.get("LENGRVIS_EXPO_PUSH_ACCESS_TOKEN", "").strip()
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    http_client = client or httpx
    try:
        response = http_client.post(
            EXPO_PUSH_SEND_URL,
            json=messages,
            headers=headers,
            timeout=PUSH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        tickets = _push_tickets(response.json(), len(targets))
    except (httpx.HTTPError, OSError, TypeError, ValueError):
        _record_delivery_result(attempted=len(targets), sent=0, removed=0)
        return {"attempted": len(targets), "sent": 0, "removed": 0}

    sent = 0
    removed = 0
    for target, ticket in zip(targets, tickets, strict=False):
        if str(ticket.get("status") or "").lower() == "ok":
            sent += 1
            continue
        details = ticket.get("details") if isinstance(ticket.get("details"), dict) else {}
        if str(details.get("error") or ticket.get("message") or "") == "DeviceNotRegistered":
            if mobile_pairing_service._remove_mobile_push_subscription(
                target["device_id"],
                expected_token=target["token"],
            ):
                removed += 1
    _record_delivery_result(attempted=len(targets), sent=sent, removed=removed)
    return {"attempted": len(targets), "sent": sent, "removed": removed}


def _eligible_push_targets(approval: dict[str, Any]) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for device in db.fetch_many("mobile_devices", limit=100):
        device_id = str(device.get("device_id") or device.get("id") or "").strip()
        if str(device.get("status") or "active").lower() != "active":
            continue
        subscription = device.get("push_subscription")
        if not device_id or not isinstance(subscription, dict):
            continue
        provider = str(subscription.get("provider") or "").strip().lower()
        push_token = str(subscription.get("token") or "").strip()
        if provider != "expo" or not mobile_pairing_service._valid_expo_push_token(push_token):
            continue
        claims = {"device_id": device_id, "scope": TOKEN_SCOPE}
        if not mobile_pairing_service.mobile_claims_can_access_approval(approval, claims):
            continue
        targets.append({"device_id": device_id, "token": push_token})
    return targets


def _approval_push_message(push_token: str, approval_id: str) -> dict[str, Any]:
    return {
        "to": push_token,
        "title": "Lengrvis 需要你审批",
        "body": "有任务等待审批，打开 App 查看详情。",
        "sound": "default",
        "priority": "high",
        "channelId": "approvals",
        "data": {"approvalId": approval_id, "kind": "approval"},
    }


def _push_tickets(value: Any, expected: int) -> list[dict[str, Any]]:
    root = value if isinstance(value, dict) else {}
    raw = root.get("data")
    if isinstance(raw, dict):
        raw = [raw]
    tickets = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if len(tickets) < expected:
        tickets.extend({"status": "error"} for _ in range(expected - len(tickets)))
    return tickets[:expected]


def _record_delivery_result(*, attempted: int, sent: int, removed: int) -> None:
    record(
        "mobile.push.delivery",
        "MobilePushService",
        {"attempted": attempted, "sent": sent, "removed": removed},
    )
