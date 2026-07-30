from __future__ import annotations

from app.tools import remote_tools


def test_remote_input_approval_binding_uses_constant_time_hmac_comparison(monkeypatch) -> None:
    comparisons: list[tuple[str, str]] = []

    monkeypatch.setattr(remote_tools, "args_binding_hmac", lambda *_args, **_kwargs: "args:expected")
    monkeypatch.setattr(
        remote_tools.hmac,
        "compare_digest",
        lambda left, right: comparisons.append((left, right)) or left == right,
    )
    approval = {
        "approval_type": "remote_input",
        "tool_name": "remote.click",
        "task_id": "task-1",
        "step_id": "step-1",
        "args_binding_hmac": "args:expected",
    }

    assert remote_tools._approval_matches_remote_input(approval, "remote.click", {"x": 1, "y": 2}) is True
    assert comparisons == [("args:expected", "args:expected")]
