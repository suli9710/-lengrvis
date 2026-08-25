from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from app.automation.intent_capsule import (
    IntentCapsuleError,
    issue_intent_capsule,
    revoke_intent_capsule,
    verify_intent_capsule,
)


def _issue():
    return issue_intent_capsule(
        task_id="task-1",
        user_goal="把表格数据填入已认证网页",
        plan_revision=3,
        allowed_tools=["browser.*", "document.extract"],
        resource_scope=["https://example.test/*", "D:/work/*.csv"],
        data_egress_scope=["provider:planning", "origin:example.test"],
        policy_version="policy-v2",
        ttl_seconds=600,
    )


def test_intent_capsule_binds_goal_plan_tool_resource_and_egress() -> None:
    issued = _issue()

    verified = verify_intent_capsule(
        issued.token,
        task_id="task-1",
        user_goal="把表格数据填入已认证网页",
        plan_revision=3,
        policy_version="policy-v2",
        tool_name="browser.act",
        resource="https://example.test/form/1",
        data_egress="origin:example.test",
    )

    assert verified.id == issued.capsule.id
    assert verified.nonce


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"user_goal": "改为发送给所有人"}, "goal digest"),
        ({"plan_revision": 4}, "plan revision"),
        ({"policy_version": "policy-v3"}, "policy version"),
        ({"tool_name": "external.email.send"}, "tool is outside"),
        ({"resource": "https://other.test/form"}, "resource is outside"),
        ({"data_egress": "origin:other.test"}, "data egress is outside"),
    ],
)
def test_intent_capsule_rejects_scope_drift(override: dict[str, object], message: str) -> None:
    issued = _issue()
    args: dict[str, object] = {
        "task_id": "task-1",
        "user_goal": "把表格数据填入已认证网页",
        "plan_revision": 3,
        "policy_version": "policy-v2",
        "tool_name": "browser.act",
        "resource": "https://example.test/form/1",
        "data_egress": "origin:example.test",
    }
    args.update(override)

    with pytest.raises(IntentCapsuleError, match=message):
        verify_intent_capsule(issued.token, **args)  # type: ignore[arg-type]


def test_intent_capsule_rejects_tamper_expiry_and_revocation() -> None:
    issued = _issue()
    tampered = f"{issued.token[:-1]}{'A' if issued.token[-1] != 'A' else 'B'}"
    common = {
        "task_id": "task-1",
        "user_goal": "把表格数据填入已认证网页",
        "plan_revision": 3,
        "policy_version": "policy-v2",
    }

    with pytest.raises(IntentCapsuleError, match="signature"):
        verify_intent_capsule(tampered, **common)

    with pytest.raises(IntentCapsuleError, match="expired"):
        verify_intent_capsule(
            issued.token,
            **common,
            now=datetime.now(UTC) + timedelta(hours=2),
        )

    issued = _issue()
    revoke_intent_capsule(issued.capsule.id)
    with pytest.raises(IntentCapsuleError, match="stored intent capsule status is revoked"):
        verify_intent_capsule(issued.token, **common, now=datetime.now(UTC) + timedelta(hours=2))
    assert revoke_intent_capsule(issued.capsule.id).status == "revoked"  # type: ignore[union-attr]


def test_intent_capsule_rejects_equivalent_noncanonical_signature_encoding() -> None:
    issued = _issue()
    payload_segment, signature_segment = issued.token.split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    canonical_index = alphabet.index(signature_segment[-1])
    assert canonical_index % 4 == 0
    noncanonical_signature = f"{signature_segment[:-1]}{alphabet[canonical_index + 1]}"
    assert base64.urlsafe_b64decode(f"{signature_segment}=") == base64.urlsafe_b64decode(f"{noncanonical_signature}=")

    with pytest.raises(IntentCapsuleError, match="signature"):
        verify_intent_capsule(
            f"{payload_segment}.{noncanonical_signature}",
            task_id="task-1",
            user_goal="把表格数据填入已认证网页",
            plan_revision=3,
            policy_version="policy-v2",
        )


@pytest.mark.parametrize("ttl_seconds", [59, 3601])
def test_intent_capsule_rejects_out_of_range_ttl(ttl_seconds: int) -> None:
    with pytest.raises(ValueError, match="TTL must be between"):
        issue_intent_capsule(
            task_id="task-ttl",
            user_goal="核对文件",
            plan_revision=1,
            allowed_tools=["document.extract"],
            resource_scope=[],
            data_egress_scope=[],
            policy_version="policy-v2",
            ttl_seconds=ttl_seconds,
        )


def test_intent_capsule_rejects_blank_allowed_tool_scope() -> None:
    with pytest.raises(ValueError, match="at least one allowed tool"):
        issue_intent_capsule(
            task_id="task-tools",
            user_goal="核对文件",
            plan_revision=1,
            allowed_tools=["  "],
            resource_scope=[],
            data_egress_scope=[],
            policy_version="policy-v2",
        )
