from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest
from scripts import run_windows_agent_acceptance as acceptance
from scripts.windows_agent_acceptance_fixtures import native_windows_hosts

from app.config import AppSettings
from app.core.schemas import Plan, PlanStep
from app.llm.mock_provider import MockProvider

HOSTS = [{"process_id": 123}, {"process_id": 234}]


def transfer_plan(hosts=HOSTS, *, task_id="task"):
    source, destination = [
        {"process_id": host["process_id"], "name": "Fixture editor", "control_type": "Edit"} for host in hosts
    ]
    return Plan(
        task_id=task_id,
        goal="Copy and verify fixture text",
        steps=[
            PlanStep(
                id="read",
                tool_name="ui_automation.get_property",
                agent_name="ComputerAgent",
                description="Read source",
                args={**source, "prop": "text"},
            ),
            PlanStep(
                id="write",
                tool_name="ui_automation.type_text",
                agent_name="ComputerAgent",
                description="Write destination",
                args=destination,
                depends_on=["read"],
                input_bindings={"text": {"step_id": "read", "path": ["value"]}},
            ),
            PlanStep(
                id="verify",
                tool_name="ui_automation.get_property",
                agent_name="ComputerAgent",
                description="Verify destination",
                args={**destination, "prop": "text"},
                depends_on=["read", "write"],
                input_bindings={"expected_value": {"step_id": "read", "path": ["value"]}},
            ),
        ],
    )


def test_acceptance_validates_the_complete_source_write_readback_chain():
    registry = acceptance.fixture_registry(HOSTS, "source")
    assert acceptance.validate_transfer_plan(transfer_plan(), registry, HOSTS) == ("read", "write", "verify")


def test_acceptance_sample_exercises_multiline_unicode_without_resembling_credentials():
    from app.policy.sensitive_values import looks_sensitive_value

    source = acceptance.fixture_source_text()
    assert "\r\n" in source and "😀" in source
    assert not looks_sensitive_value(source)


@pytest.mark.parametrize(
    "mutation",
    [
        "other_pid",
        "source_write",
        "literal_write",
        "source_verification",
        "no_write_dependency",
        "unsafe_tool",
        "bound_selector",
        "cycle",
    ],
)
def test_acceptance_rejects_plans_that_do_not_prove_the_requested_transfer(mutation):
    plan = transfer_plan()
    read, write, verify = plan.steps
    if mutation == "other_pid":
        read.args["process_id"] = 345
    elif mutation == "source_write":
        write.args["process_id"] = HOSTS[0]["process_id"]
    elif mutation == "literal_write":
        write.input_bindings = {}
        write.args["text"] = "guessed content"
    elif mutation == "source_verification":
        verify.args["process_id"] = HOSTS[0]["process_id"]
    elif mutation == "no_write_dependency":
        verify.depends_on = ["read"]
    elif mutation == "unsafe_tool":
        write.tool_name = "system.run_command"
    elif mutation == "bound_selector":
        write.input_bindings["process_id"] = write.input_bindings["text"]
    else:
        read.depends_on = ["verify"]
    with pytest.raises(ValueError):
        acceptance.validate_transfer_plan(plan, acceptance.fixture_registry(HOSTS, "source"), HOSTS)


@pytest.mark.parametrize("change", [{"process_id": 999}, {"text": "unapproved content"}])
def test_acceptance_runtime_rechecks_scope_after_planning(change):
    tool = acceptance.fixture_registry(HOSTS, "source").get("ui_automation.type_text")
    with pytest.raises(ValueError):
        tool.execute({"process_id": 234, "name": "Fixture editor", "text": "source", **change}, {})


@pytest.mark.parametrize("wrapped", [False, True])
def test_acceptance_refuses_resolved_mock_provider(monkeypatch, wrapped):
    provider = MockProvider()
    if wrapped:
        provider = SimpleNamespace(provider=SimpleNamespace(provider=provider))
    monkeypatch.setattr(acceptance.PlannerAgent, "_provider_for_settings", lambda *a: provider)
    planner = acceptance.ScopedPlanner(AppSettings(), acceptance.fixture_registry(HOSTS, "source"), HOSTS, None)
    with pytest.raises(ValueError, match="real planner"):
        planner._provider_for_settings(AppSettings())


def test_acceptance_mock_config_never_launches_native_windows(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(acceptance.sys, "platform", "win32")
    monkeypatch.setattr(
        acceptance, "get_effective_settings", lambda: AppSettings(provider_name="mock", api_key="private-key-fixture")
    )
    monkeypatch.setattr(acceptance, "native_windows_hosts", lambda *a, **k: pytest.fail("Must not launch fixture"))
    path = tmp_path / "report.json"
    assert acceptance.main(["--report", str(path)]) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["model_plan_received"] is False
    assert report["failed_check"] == "Windows acceptance refuses MockProvider"
    assert "private-key-fixture" not in capsys.readouterr().out + path.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native UIA acceptance")
@pytest.mark.asyncio
@pytest.mark.parametrize("long_text", [False, True])
async def test_acceptance_drives_the_normal_task_entry_and_native_approval_flow(monkeypatch, tmp_path, long_text):
    pytest.importorskip("comtypes.client")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_TASK_RECORDING_ENABLED", "false")
    acceptance.db.init_db()
    with native_windows_hosts(tmp_path / "windows") as hosts:
        orchestrator = acceptance.OrchestratorAgent()
        source = "原生任务入口\r\ncopy-exactly ✓"
        if long_text:
            # Ordinary prose exceeds the result budget without resembling a
            # credential (a single long alphanumeric token is intentionally denied).
            source += "Copy this paragraph intact. 跨应用原生文本。\r\n" * 700
        orchestrator.registry = acceptance.fixture_registry(hosts, source)

        async def known_plan(task_id, *args, **kwargs):
            return transfer_plan(hosts, task_id=task_id)

        # This is a harness contract test, not evidence of real LLM planning.
        monkeypatch.setattr(orchestrator.planner, "create_plan", known_plan)
        result = await acceptance.exercise_transfer(orchestrator, hosts, source)

    assert result["task_phase"] == "completed"
    assert result["approval_consumed"] is True
    assert result["destination_verified"] is True
    assert len(result["steps"]) == 3
