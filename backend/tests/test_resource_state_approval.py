from types import SimpleNamespace

import pytest

from app.orchestration.resource_state import StaleResourceStateError, validate_write_preconditions
from app.perception.ui_automation_identity import UI_AUTOMATION_IDENTITY_VERSION


def _tool(name="test.remote_write", trust_tier="builtin"):
    return SimpleNamespace(name=name, effects=["write"], trust_tier=trust_tier)


@pytest.mark.parametrize("current", [[], [{"kind": "remote_document", "revision": 2}]])
def test_non_file_resource_must_still_match_the_approved_state(current):
    with pytest.raises(StaleResourceStateError):
        validate_write_preconditions(
            tool=_tool(),
            args={},
            context={},
            current_state=current,
            expected_approval_state=[{"kind": "remote_document", "revision": 1}],
        )


@pytest.mark.parametrize(
    ("name", "trust_tier", "kind", "version"),
    [
        ("test.remote_write", "builtin", "ui_automation_element", UI_AUTOMATION_IDENTITY_VERSION),
        ("ui_automation.type_text", "external", "ui_automation_element", UI_AUTOMATION_IDENTITY_VERSION),
        ("ui_automation.type_text", "builtin", "remote_document", UI_AUTOMATION_IDENTITY_VERSION),
        ("ui_automation.type_text", "builtin", "ui_automation_element", "legacy"),
    ],
)
def test_only_builtin_semantic_uia_state_can_defer_to_the_native_adapter(name, trust_tier, kind, version):
    with pytest.raises(StaleResourceStateError):
        validate_write_preconditions(
            tool=_tool(name, trust_tier),
            args={},
            context={},
            current_state=[],
            expected_approval_state=[{"kind": kind, "identity_version": version}],
        )


@pytest.mark.parametrize("name", ["ui_automation.click", "ui_automation.type_text", "ui_automation.hotkey"])
def test_builtin_semantic_uia_state_uses_native_identity_revalidation(name):
    validate_write_preconditions(
        tool=_tool(name),
        args={},
        context={},
        current_state=[],
        expected_approval_state=[{"kind": "ui_automation_element", "identity_version": UI_AUTOMATION_IDENTITY_VERSION}],
    )


def test_unchanged_non_file_resource_state_is_accepted():
    state = [{"kind": "remote_document", "revision": 1}]
    validate_write_preconditions(
        tool=_tool(),
        args={},
        context={},
        current_state=state,
        expected_approval_state=state,
    )


def test_matching_file_does_not_hide_a_changed_non_file_resource(tmp_path):
    file_state = {"path": str(tmp_path / "output.txt"), "exists": False}
    with pytest.raises(StaleResourceStateError):
        validate_write_preconditions(
            tool=_tool(),
            args={},
            context={},
            current_state=[file_state, {"kind": "remote_document", "revision": 2}],
            expected_approval_state=[file_state, {"kind": "remote_document", "revision": 1}],
        )
