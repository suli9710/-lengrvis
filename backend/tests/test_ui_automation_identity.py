from __future__ import annotations

import hashlib
from typing import Any

import pytest

from app.perception import ui_automation_identity as identity
from app.perception.ui_automation_elements import UIAutomationElement
from app.policy import approval_binding
from app.policy.approval_binding import canonical_json


def _install_deterministic_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    def digest(value: Any, *, prefix: str = "hmac") -> str:
        encoded = canonical_json(value).encode("utf-8")
        return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"

    monkeypatch.setattr(identity, "hmac_digest", digest)


def _element(**updates: Any) -> UIAutomationElement:
    values: dict[str, Any] = {
        "name": "Alice confidential draft",
        "automation_id": "workspace-alice-send",
        "control_type": "Button",
        "class_name": "PrivateComposer",
        "process_id": 42,
        "properties": {
            "runtime_id": [7, 8, 9],
            "bounding_box": {"left": 10, "top": 20, "right": 30, "bottom": 40},
        },
    }
    values.update(updates)
    return UIAutomationElement(**values)


def test_semantic_resource_state_never_persists_selector_or_element_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_deterministic_digest(monkeypatch)

    state = identity.semantic_resource_state(
        selector={"automation_id": "workspace-alice-send", "name": "Alice confidential draft"},
        element=_element(),
        target_window={"identity_hmac": "ui-window:abc"},
    )

    serialized = str(state).casefold()
    assert state["identity_version"] == identity.UI_AUTOMATION_IDENTITY_VERSION
    assert state["selector_hmac"].startswith("ui-selector:")
    assert state["fingerprint"]["identity_hmac"].startswith("ui-element:")
    assert "alice" not in serialized
    assert "confidential" not in serialized
    assert "workspace-alice-send" not in serialized
    assert "privatecomposer" not in serialized


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("name", "Different name"),
        ("automation_id", "different-id"),
        ("control_type", "Edit"),
        ("class_name", "DifferentClass"),
        ("process_id", 84),
        ("runtime_id", [9, 8, 7]),
        ("bounding_box", {"left": 11, "top": 20, "right": 30, "bottom": 40}),
    ],
)
def test_element_fingerprint_binds_every_identity_field(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    changed: Any,
) -> None:
    _install_deterministic_digest(monkeypatch)
    original = _element()
    if field in {"runtime_id", "bounding_box"}:
        properties = dict(original.properties)
        properties[field] = changed
        modified = _element(properties=properties)
    else:
        modified = _element(**{field: changed})

    assert identity.element_action_fingerprint(original) != identity.element_action_fingerprint(modified)


def test_process_identity_normalizes_equivalent_windows_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_deterministic_digest(monkeypatch)

    left = identity.process_identity_fingerprint(
        process_id=42,
        created_at=1234.5,
        executable=r"C:\Apps\Mail\..\Mail\CLIENT.EXE",
    )
    right = identity.process_identity_fingerprint(
        process_id=42,
        created_at=1234.5,
        executable="c:/apps/mail/client.exe",
    )

    assert left == right
    assert left is not None
    assert "client.exe" not in str(left).casefold()


@pytest.mark.parametrize(
    ("process_id", "created_at", "executable"),
    [
        (0, 1.0, r"C:\app.exe"),
        (-1, 1.0, r"C:\app.exe"),
        (1, 1.0, ""),
        (1, "invalid", r"C:\app.exe"),
    ],
)
def test_process_identity_rejects_incomplete_facts(
    monkeypatch: pytest.MonkeyPatch,
    process_id: int,
    created_at: Any,
    executable: str,
) -> None:
    _install_deterministic_digest(monkeypatch)

    assert (
        identity.process_identity_fingerprint(
            process_id=process_id,
            created_at=created_at,
            executable=executable,
        )
        is None
    )


def test_window_fingerprint_hides_parent_labels_and_binds_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_deterministic_digest(monkeypatch)
    window = _element(control_type="Window")
    parent_chain = [
        {"name": "Alice confidential", "automation_id": "workspace-alice"},
        {"name": "Mail", "automation_id": "mail-root"},
    ]

    forward = identity.window_action_fingerprint(
        window,
        parent_chain=parent_chain,
        process_identity={"executable_hmac": "exec", "instance_hmac": "instance"},
        native_window_handle=9001,
    )
    reversed_chain = identity.window_action_fingerprint(
        window,
        parent_chain=list(reversed(parent_chain)),
        process_identity={"executable_hmac": "exec", "instance_hmac": "instance"},
        native_window_handle=9001,
    )

    assert forward["parent_chain_hmac"] != reversed_chain["parent_chain_hmac"]
    assert forward["parent_chain_depth"] == 2
    assert "alice" not in str(forward).casefold()
    assert "workspace-alice" not in str(forward).casefold()


def test_v2_identity_golden_fingerprints_are_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        approval_binding,
        "approval_secret",
        lambda: "ui-identity-golden-secret",
    )
    element = UIAutomationElement(
        name="Payroll — alice@example.test",
        automation_id="workspace-alice",
        control_type="Button",
        class_name="PayrollButton",
        process_id=42,
        properties={
            "runtime_id": [7, 8, 9],
            "bounding_box": {"left": 10, "top": 20, "right": 30, "bottom": 40},
        },
    )
    process_identity = identity.process_identity_fingerprint(
        process_id=42,
        created_at=1234.5,
        executable=r"C:\Users\Alice\Private Workspace\mail.exe",
    )
    assert process_identity is not None
    parent_chain = [
        {
            "runtime_id": [10, 20, 30],
            "native_window_handle": 9001,
            "name": "Inbox - alice@example.test",
            "automation_id": "mail-window",
            "control_type": "Window",
            "class_name": "MailWindow",
            "process_id": 42,
        }
    ]
    window = UIAutomationElement(
        name="Inbox - alice@example.test",
        automation_id="mail-window",
        control_type="Window",
        class_name="MailWindow",
        process_id=42,
        properties={"runtime_id": [10, 20, 30]},
    )
    target_window = identity.window_action_fingerprint(
        window,
        parent_chain=parent_chain,
        process_identity=process_identity,
        native_window_handle=9001,
    )

    state = identity.semantic_resource_state(
        selector={
            "automation_id": "workspace-alice",
            "name": "Payroll — alice@example.test",
        },
        element=element,
        target_window=target_window,
    )

    assert state == {
        "kind": "ui_automation_element",
        "identity_version": "ui-automation-identity/v2",
        "selector_hmac": ("ui-selector:93c2c144d533e5d0ee15ec4944dccb6ea64eb7ef14cd28acc601bdacc4b5d9dd"),
        "fingerprint": {
            "identity_hmac": ("ui-element:1ec8e71844b3954d5de94b4c4d6e876d7c471dad5f41501a45a4a2a57ec1fd49")
        },
        "target_window": {
            "runtime_id": [10, 20, 30],
            "native_window_handle": 9001,
            "process_id": 42,
            "executable_hmac": (
                "ui-process-executable:69c26f7102e9ed2e2f20172c760f5935f3039ee198b5c93c3aaada14bd18c43c"
            ),
            "instance_hmac": ("ui-process-instance:eb0fb09f3c06d33d37c1bad2381c82dd608b854b157fab8ba8040e6996a91694"),
            "parent_chain_depth": 1,
            "parent_chain_hmac": ("ui-parent-chain:5df2417b1226dcc471390dc9fdc4f7565e29f99bccc5e9184a76695559089c04"),
            "identity_hmac": ("ui-window:b282d842b2f0f30271dd4645f3270be39f52d2df25c2f1189981d3bccde1b020"),
        },
    }


def test_legacy_or_malformed_approved_identity_fails_closed() -> None:
    legacy_state = {
        "kind": identity.UI_AUTOMATION_RESOURCE_KIND,
        "target_window": {"identity_hmac": "ui-window:legacy"},
    }
    malformed_current = {
        "kind": identity.UI_AUTOMATION_RESOURCE_KIND,
        "identity_version": identity.UI_AUTOMATION_IDENTITY_VERSION,
        "target_window": "invalid",
    }

    assert identity.expected_approved_window_fingerprint([legacy_state]) is None
    assert identity.expected_approved_window_fingerprint([malformed_current]) is None
    assert identity.has_approved_semantic_resource_state([legacy_state]) is True
    assert identity.has_approved_semantic_resource_state([malformed_current]) is True
