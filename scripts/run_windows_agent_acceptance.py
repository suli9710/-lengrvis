"""Real planner -> native cross-app transfer, using only owned test windows.

Run from a source checkout. Reads the configured provider credential, isolates
all task data, and never substitutes MockProvider for a failed real request.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.agents.orchestrator_agent import OrchestratorAgent  # noqa: E402
from app.agents.planner_agent import PlannerAgent  # noqa: E402
from app.core import db  # noqa: E402
from app.core.schemas import Approval, ApprovalStatus, Plan, StepStatus, TaskStatus  # noqa: E402
from app.llm.mock_provider import MockProvider  # noqa: E402
from app.llm.registry import get_effective_settings, invalidate_settings_cache  # noqa: E402
from app.orchestration.execution_stage import ExecutionStage  # noqa: E402
from app.orchestration.workflow_plan import expand_workflow_steps  # noqa: E402
from app.perception import ui_automation as uia  # noqa: E402
from app.perception import ui_automation_com as com  # noqa: E402
from app.perception.ui_automation_elements import coerce_selector  # noqa: E402
from app.policy.model_boundary import model_control_arg_error  # noqa: E402
from app.services.mobile_pairing_service import approve_approval  # noqa: E402
from app.tools import ui_automation_contracts as contracts  # noqa: E402
from app.tools import ui_automation_tools, workflow_tools  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402
from scripts.windows_agent_acceptance_fixtures import native_windows_hosts  # noqa: E402

PRIMITIVES = frozenset(
    {
        "ui_automation.get_property",
        "ui_automation.inspect_selector",
        "ui_automation.find_element",
        "ui_automation.type_text",
    }
)


class AcceptanceCheckFailed(ValueError):
    """A static, credential-free explanation of a failed acceptance check."""


def fixture_source_text() -> str:
    # A short per-run identifier makes the unknown source distinguishable
    # without resembling a credential that the input policy must reject.
    return "Windows 跨应用验收\r\n" + secrets.token_hex(4) + "：请完整保留这段普通文本和 emoji 😀。"


def check_fixture_scope(tool_name, args, hosts):
    if tool_name not in PRIMITIVES:
        raise AcceptanceCheckFailed("Acceptance plan contains an unsupported tool")
    selector = coerce_selector(args)
    allowed_pids = {host["process_id"] for host in hosts}
    if selector.process_id not in allowed_pids or selector.name != "Fixture editor":
        raise AcceptanceCheckFailed("Acceptance selector must identify an owned fixture editor")
    if (
        tool_name == "ui_automation.type_text"
        and selector.process_id != hosts[1]["process_id"]
    ):
        raise AcceptanceCheckFailed("Acceptance writes are restricted to the destination fixture")
    if tool_name == "ui_automation.get_property" and args.get("prop") != "text":
        raise AcceptanceCheckFailed("Acceptance reads must use the text property")


def validate_transfer_plan(plan: Plan, registry: ToolRegistry, hosts):
    expand_workflow_steps(plan, registry, [tool.name for tool in registry.list()])
    if not 3 <= len(plan.steps) <= 16 or len({step.id for step in plan.steps}) != len(
        plan.steps
    ):
        raise AcceptanceCheckFailed("Acceptance plan must contain a bounded, unique set of steps")
    steps = {step.id: step for step in plan.steps}
    for step in plan.steps:
        if model_control_arg_error(step.args) or set(step.input_bindings) - {
            "text",
            "expected_value",
        }:
            raise AcceptanceCheckFailed(
                "Acceptance plan cannot bind selectors or runtime controls"
            )
        check_fixture_scope(step.tool_name, step.args, hosts)
        arguments = {**step.args, **dict.fromkeys(step.input_bindings, "fixture value")}
        contracts.validate_arguments(step.tool_name.split(".", 1)[1], arguments, {})
        if any(dependency not in steps for dependency in step.depends_on):
            raise AcceptanceCheckFailed("Acceptance plan has a missing dependency")
    completed = set()
    while len(completed) < len(steps):
        ready = {
            step.id for step in plan.steps if set(step.depends_on) <= completed
        } - completed
        if not ready:
            raise AcceptanceCheckFailed("Acceptance plan has cyclic dependencies")
        completed.update(ready)
    writes = [
        step for step in plan.steps if step.tool_name == "ui_automation.type_text"
    ]
    if len(writes) != 1:
        raise AcceptanceCheckFailed("Acceptance requires exactly one destination write")
    write = writes[0]
    binding = write.input_bindings.get("text")
    source = steps.get(binding.step_id) if binding else None
    if (
        source is None
        or source.tool_name != "ui_automation.get_property"
        or binding.path != ["value"]
        or source.id not in write.depends_on
        or coerce_selector(source.args).process_id != hosts[0]["process_id"]
    ):
        raise AcceptanceCheckFailed("Acceptance write must bind the observed source text")
    verifications = [
        step
        for step in plan.steps
        if step.tool_name == "ui_automation.get_property"
        and coerce_selector(step.args).process_id == hosts[1]["process_id"]
        and step.input_bindings.get("expected_value") == binding
        and {source.id, write.id} <= set(step.depends_on)
    ]
    if len(verifications) != 1:
        raise AcceptanceCheckFailed(
            "Acceptance requires a destination readback bound to the source observation"
        )
    return source.id, write.id, verifications[0].id


def fixture_registry(hosts, source_text):
    catalog = ToolRegistry()
    ui_automation_tools.register(catalog)
    workflow_tools.register(catalog)
    registry = ToolRegistry()
    for tool in catalog.list():
        if tool.name not in PRIMITIVES | {"workflow.run"}:
            continue
        if tool.name in PRIMITIVES:
            original, name = tool.execute, tool.name

            def execute(args, context, *, original=original, name=name):
                check_fixture_scope(name, args, hosts)
                if (
                    name == "ui_automation.type_text"
                    and args.get("text") != source_text
                ):
                    raise AcceptanceCheckFailed(
                        "Acceptance write does not match the fixture source content"
                    )
                return original(args, context)

            tool.execute = execute
        registry.register(tool)
    return registry


class ScopedPlanner(PlannerAgent):
    def __init__(self, settings, registry, hosts, bus):
        super().__init__(bus)
        self.settings = settings
        self.registry = registry
        self.hosts = hosts
        self.provider_class = ""
        self.plan_received = False

    def _settings_for_mode(self, mode):
        return self.settings.model_copy(
            update={"mode": mode, "allow_mock_fallback": False}
        )

    def _provider_for_settings(self, settings):
        provider = super()._provider_for_settings(settings)
        resolved = provider
        seen = set()
        while True:
            if isinstance(resolved, MockProvider):
                raise AcceptanceCheckFailed("Windows acceptance requires a real planner provider")
            if id(resolved) in seen:
                raise AcceptanceCheckFailed(
                    "Windows acceptance cannot resolve a cyclic provider wrapper"
                )
            seen.add(id(resolved))
            nested = getattr(resolved, "provider", None)
            if nested is None:
                break
            resolved = nested
        self.provider_class = type(resolved).__name__
        return provider

    async def create_plan(self, *args, **kwargs):
        plan = await super().create_plan(*args, **kwargs)
        if not self.provider_class:
            raise AcceptanceCheckFailed("Windows acceptance requires a model-generated plan")
        self.plan_received = True
        validate_transfer_plan(plan, self.registry, self.hosts)
        return plan


async def exercise_transfer(orchestrator, hosts, source_text):
    adapter = uia.WindowsCOMUIAutomationTarget()

    def read(host):
        return uia._native_text(adapter._automation.ElementFromHandle(host["editor"]))

    def seed_source():
        adapter._type_text_sync(
            adapter._automation.ElementFromHandle(hosts[0]["editor"]), source_text
        )

    com.run_sync(seed_source)
    initial_destination = com.run_sync(read, hosts[1])
    goal = (
        "Copy the complete existing text from the source Windows editor into the destination editor and verify it. "
        f"Source selector: process_id={hosts[0]['process_id']}, name=Fixture editor, control_type=Edit. "
        f"Destination selector: process_id={hosts[1]['process_id']}, name=Fixture editor, control_type=Edit. "
        "The source content is unknown until read. Only these isolated test windows are in scope."
    )
    task = await orchestrator.handle_user_goal(goal, "efficiency")
    if (
        task.status != TaskStatus.EXECUTION
        or task.execution_stage != ExecutionStage.AWAITING_APPROVAL
    ):
        raise AcceptanceCheckFailed("Native transfer did not reach the required approval boundary")
    plan = orchestrator._latest_plan_for_task(task.id)
    source_id, write_id, verify_id = validate_transfer_plan(
        plan, orchestrator.registry, hosts
    )
    if com.run_sync(read, hosts[1]) != initial_destination:
        raise AcceptanceCheckFailed("Destination changed before approval")
    approvals = [
        Approval.model_validate(row)
        for row in db.fetch_many("approvals", "task_id = ?", (task.id,), limit=32)
    ]
    pending = [item for item in approvals if item.status == ApprovalStatus.PENDING]
    if (
        len(pending) != 1
        or pending[0].step_id != write_id
        or pending[0].tool_name != "ui_automation.type_text"
    ):
        raise AcceptanceCheckFailed("Native transfer produced an unexpected approval")
    approved = approve_approval(pending[0].id)
    task = await orchestrator.execute_approved_step(approved)
    plan = orchestrator._latest_plan_for_task(task.id)
    await orchestrator._process_steps(task, plan)
    steps = {step.id: step for step in plan.steps}
    consumed = bool(db.fetch_one("approvals", approved.id).get("consumed_at"))
    if (
        task.status != TaskStatus.COMPLETED
        or not consumed
        or any(
            steps[key].status != StepStatus.SUCCEEDED
            for key in (source_id, write_id, verify_id)
        )
        or com.run_sync(read, hosts[1]) != source_text
    ):
        raise AcceptanceCheckFailed(
            "Native transfer did not complete with verified destination content"
        )
    return {
        "task_phase": task.status.value,
        "approval_consumed": consumed,
        "steps": [
            {"tool": step.tool_name, "status": step.status.value} for step in plan.steps
        ],
        "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "destination_verified": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        choices=range(30, 601),
        default=120,
        metavar="30..600",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / ".tmp/qa-evidence/windows-agent-acceptance.json",
    )
    args = parser.parse_args(argv)
    report = {
        "schema": "windows-agent-acceptance/v1",
        "ok": False,
        "started_at": datetime.now(UTC).isoformat(),
    }
    started = time.monotonic()
    planner = None
    try:
        if sys.platform != "win32":
            raise RuntimeError("Windows native acceptance requires Windows")
        configured = get_effective_settings()
        if configured.provider_name == "mock":
            raise AcceptanceCheckFailed("Windows acceptance refuses MockProvider")
        updates = {
            "mode": "efficiency",
            "allow_mock_fallback": False,
            "llm_api_max_retries": 0,
            "timeout": 35,
        }
        if args.base_url:
            updates["base_url"] = args.base_url
        if args.model:
            updates["model"] = args.model
        configured = configured.model_copy(update=updates)
        report["model"] = configured.model
        # This process owns its entire environment and database. Credentials are
        # inherited in memory, never written into command arguments or reports.
        scratch = Path(tempfile.mkdtemp(prefix="lengrvis-windows-acceptance-"))
        configured = configured.model_copy(update={"data_dir": str(scratch / "data")})
        os.environ.update(
            {
                "LENGRVIS_TEST": "1",
                "LENGRVIS_DATA_DIR": str(scratch / "data"),
                "LENGRVIS_CONFIG_FILE": str(scratch / "absent.yaml"),
                "LENGRVIS_ENV_FILE": str(scratch / "absent.env"),
                "LENGRVIS_AUDIT_HMAC_SECRET": secrets.token_hex(32),
                "LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS": "1",
                "LENGRVIS_ALLOW_MOCK_FALLBACK": "false",
                "LENGRVIS_TASK_RECORDING_ENABLED": "false",
                "LENGRVIS_PROVIDER_NAME": configured.provider_name,
                "LENGRVIS_MODE": "efficiency",
                "LENGRVIS_BASE_URL": configured.base_url,
                "LENGRVIS_MODEL": configured.model,
                "LENGRVIS_API_KEY": configured.api_key,
            }
        )
        invalidate_settings_cache()
        db.init_db()
        source_text = fixture_source_text()
        with native_windows_hosts(
            scratch / "windows", lifetime_seconds=args.timeout_seconds + 30
        ) as hosts:
            orchestrator = OrchestratorAgent()
            orchestrator.registry = fixture_registry(hosts, source_text)
            planner = ScopedPlanner(
                configured, orchestrator.registry, hosts, orchestrator.bus
            )
            orchestrator.planner = planner
            result = asyncio.run(
                asyncio.wait_for(
                    exercise_transfer(orchestrator, hosts, source_text),
                    args.timeout_seconds,
                )
            )
            report.update(result)
            report["ok"] = True
    except Exception as exc:  # noqa: BLE001 - report only safe exception metadata, never provider response bodies.
        report["error_type"] = type(exc).__name__
        if isinstance(exc, AcceptanceCheckFailed):
            report["failed_check"] = str(exc)
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int):
            report["http_status"] = status
    finally:
        report["provider_class"] = planner.provider_class if planner else ""
        report["model_plan_received"] = bool(planner and planner.plan_received)
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=True))
        db.close_thread_connection()
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
