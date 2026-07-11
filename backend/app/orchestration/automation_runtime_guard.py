from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit, urlunsplit

from app.automation.intent_capsule import IntentCapsuleError, issue_intent_capsule, verify_intent_capsule
from app.automation.models import BudgetConsumeRequest, RunBudgetLimits
from app.automation.run_budget import consume_run_budget_events, create_run_budget
from app.core import db
from app.core.audit import record
from app.core.content_provenance import stable_content_hash
from app.core.schemas import PlanStep, Task
from app.orchestration.runtime_context import TaskRuntimeContext
from app.orchestration.tool_runtime_paths import candidate_authorized_paths
from app.policy.approval_binding import permission_policy_version
from app.policy.permissions import PermissionStore
from app.tools.schemas import ToolDefinition

_RUNTIME_CONTROL_ARG_KEYS = {
    "approval_id",
    "approved",
    "auto_approved",
    "dry_run",
    "policy_decision",
}
_WRITE_EFFECTS = {
    "browser_write",
    "click",
    "control",
    "copy",
    "create",
    "delete",
    "drag",
    "external_post",
    "input",
    "keyboard",
    "modify",
    "move",
    "rename",
    "send",
    "trash",
    "type",
    "upload",
    "write",
}
_EXTERNAL_SEND_EFFECTS = {"external_post", "send", "submit", "upload"}
_UI_INPUT_EFFECTS = {"browser_write", "click", "control", "drag", "input", "keyboard", "type"}
_SUBPROCESS_EFFECTS = {"execute_process", "execute_subprocess", "execute_test", "process", "subprocess"}
_RECIPIENT_KEYS = {"attendee", "attendees", "bcc", "cc", "recipient", "recipients", "to"}
_URL_KEYS = {
    "base_url",
    "destination_url",
    "endpoint",
    "href",
    "origin",
    "source_url",
    "target_url",
    "url",
    "webhook_url",
}
_FILE_RESOURCE_KINDS = {"directory", "document", "file", "image", "repository", "workspace"}
_MAX_BOUND_TARGETS = 1000


@dataclass(frozen=True, slots=True)
class AutomationExecutionAuthorization:
    run_id: str
    capsule_id: str
    action_fingerprint: str
    soft_exceeded: bool
    budget_version: int


class AutomationExecutionDenied(RuntimeError):
    def __init__(self, reason: str, *, run_id: str, hard_stop: bool) -> None:
        self.reason = reason
        self.run_id = run_id
        self.hard_stop = hard_stop
        super().__init__(reason)


def authorize_automation_execution(
    *,
    task: Task,
    step: PlanStep,
    tool: ToolDefinition,
    runtime: TaskRuntimeContext,
    args: dict[str, Any],
    threaded_tools: bool = False,
) -> AutomationExecutionAuthorization | None:
    """Validate and reserve an action under a server-issued intent and run budget."""
    context = runtime.tool_context()
    if "automation_run_id" not in context:
        plan_revision = _current_plan_revision(task.id, context)
        if plan_revision <= 0:
            return None
        try:
            resources = _bound_resources(tool, args, context)
            recipients = _bound_recipients(tool, args, context)
            domains = _bound_domains(args, context, recipients)
            egress_targets = _bound_egress(context, domains, recipients)
        except IntentCapsuleError as exc:
            _deny(
                task=task,
                run_id=f"core:{task.id}",
                tool=tool,
                reason=str(exc),
                event_type="automation_runtime.intent_denied",
                hard_stop=True,
            )
        run_id = f"core:{task.id}"
        policy_version = permission_policy_version(PermissionStore().updated_at())
        issued = issue_intent_capsule(
            task_id=task.id,
            user_goal=task.user_goal,
            plan_revision=plan_revision,
            allowed_tools=[tool.name],
            resource_scope=resources,
            data_egress_scope=egress_targets,
            policy_version=policy_version,
        )
        create_run_budget(run_id, limits=RunBudgetLimits(max_subprocesses=10))
        runtime.extra_context.update(
            {
                "automation_run_id": run_id,
                "automation_intent_capsule_token": issued.token,
                "automation_plan_revision": plan_revision,
                "automation_policy_version": policy_version,
                "core_runtime_authorization": True,
            }
        )
        context = runtime.tool_context()

    run_id = str(context.get("automation_run_id") or "").strip()
    if not run_id:
        _deny(
            task=task,
            run_id="",
            tool=tool,
            reason="automation_run_id is empty",
            event_type="automation_runtime.intent_denied",
            hard_stop=True,
        )

    token = str(context.get("automation_intent_capsule_token") or context.get("intent_capsule_token") or "").strip()
    declared_policy_version = str(
        context.get("automation_policy_version") or context.get("policy_version") or ""
    ).strip()
    policy_version = permission_policy_version(PermissionStore().updated_at())
    if declared_policy_version and declared_policy_version != policy_version:
        _deny(
            task=task,
            run_id=run_id,
            tool=tool,
            reason="automation runtime policy version is stale",
            event_type="automation_runtime.intent_denied",
            hard_stop=True,
        )
    plan_revision = _positive_int(
        context.get("automation_plan_revision", context.get("plan_revision")),
        default=0,
    )
    missing = [
        name
        for name, value in (
            ("intent capsule token", token),
            ("plan revision", plan_revision),
        )
        if not value
    ]
    if missing:
        _deny(
            task=task,
            run_id=run_id,
            tool=tool,
            reason=f"automation runtime is missing {', '.join(missing)}",
            event_type="automation_runtime.intent_denied",
            hard_stop=True,
        )

    try:
        resources = _bound_resources(tool, args, context)
        recipients = _bound_recipients(tool, args, context)
        domains = _bound_domains(args, context, recipients)
        egress_targets = _bound_egress(context, domains, recipients)
    except IntentCapsuleError as exc:
        _deny(
            task=task,
            run_id=run_id,
            tool=tool,
            reason=str(exc),
            event_type="automation_runtime.intent_denied",
            hard_stop=True,
        )
    external_send = _is_external_send(tool, args)

    try:
        capsule = verify_intent_capsule(
            token,
            task_id=task.id,
            user_goal=task.user_goal,
            plan_revision=plan_revision,
            policy_version=policy_version,
            tool_name=tool.name,
        )
        for resource in resources:
            verify_intent_capsule(
                token,
                task_id=task.id,
                user_goal=task.user_goal,
                plan_revision=plan_revision,
                policy_version=policy_version,
                tool_name=tool.name,
                resource=resource,
            )
        if (tool.external_network or external_send) and not egress_targets:
            raise IntentCapsuleError("external destination scope cannot be determined")
        for egress in egress_targets:
            verify_intent_capsule(
                token,
                task_id=task.id,
                user_goal=task.user_goal,
                plan_revision=plan_revision,
                policy_version=policy_version,
                tool_name=tool.name,
                data_egress=egress,
            )
    except IntentCapsuleError as exc:
        _deny(
            task=task,
            run_id=run_id,
            tool=tool,
            reason=str(exc),
            event_type="automation_runtime.intent_denied",
            hard_stop=True,
            details={
                "resource_count": len(resources),
                "egress_count": len(egress_targets),
            },
        )

    action_fingerprint = _action_fingerprint(
        tool,
        args,
        resources=resources,
        egress_targets=egress_targets,
        recipients=recipients,
        domains=domains,
    )
    events = _budget_events(
        tool=tool,
        step=step,
        args=args,
        context=context,
        action_fingerprint=action_fingerprint,
        threaded_tools=threaded_tools,
    )
    try:
        budget = consume_run_budget_events(
            run_id,
            events,
            recipients=recipients,
            domains=domains,
        )
    except KeyError:
        _deny(
            task=task,
            run_id=run_id,
            tool=tool,
            reason="automation run budget is missing",
            event_type="automation_runtime.budget_denied",
            hard_stop=True,
        )
    if not budget.allowed:
        hard_stop = budget.hard_exceeded
        _deny(
            task=task,
            run_id=run_id,
            tool=tool,
            reason=budget.reason
            or ("automation run budget is exhausted" if hard_stop else "automation run budget requires user review"),
            event_type=(
                "automation_runtime.budget_hard_stopped"
                if hard_stop
                else "automation_runtime.budget_paused"
            ),
            hard_stop=hard_stop,
            details={
                "action_fingerprint": action_fingerprint,
                "budget_version": budget.ledger.version,
                "event_kinds": [event.kind for event in events],
            },
        )

    record(
        "automation_runtime.authorized",
        "ToolRuntime",
        {
            "run_id": run_id,
            "capsule_id": capsule.id,
            "tool": tool.name,
            "action_fingerprint": action_fingerprint,
            "resource_count": len(resources),
            "egress_count": len(egress_targets),
            "recipient_count": len(recipients),
            "destination_domains": domains,
            "budget_event_kinds": [event.kind for event in events],
            "budget_version": budget.ledger.version,
            "soft_exceeded": budget.soft_exceeded,
        },
        task_id=task.id,
    )
    return AutomationExecutionAuthorization(
        run_id=run_id,
        capsule_id=capsule.id,
        action_fingerprint=action_fingerprint,
        soft_exceeded=budget.soft_exceeded,
        budget_version=budget.ledger.version,
    )


def _current_plan_revision(task_id: str, context: dict[str, Any]) -> int:
    declared = _positive_int(
        context.get("automation_plan_revision", context.get("plan_revision")),
        default=0,
    )
    if declared > 0:
        return declared
    rows = db.fetch_many("plans", "task_id = ?", (task_id,), limit=100)
    return max((_positive_int(row.get("version"), default=0) for row in rows), default=0)


def _deny(
    *,
    task: Task,
    run_id: str,
    tool: ToolDefinition,
    reason: str,
    event_type: str,
    hard_stop: bool,
    details: dict[str, Any] | None = None,
) -> NoReturn:
    record(
        event_type,
        "ToolRuntime",
        {
            "run_id": run_id,
            "tool": tool.name,
            "reason": reason,
            "hard_stop": hard_stop,
            **dict(details or {}),
        },
        task_id=task.id,
    )
    raise AutomationExecutionDenied(reason, run_id=run_id, hard_stop=hard_stop)


def _budget_events(
    *,
    tool: ToolDefinition,
    step: PlanStep,
    args: dict[str, Any],
    context: dict[str, Any],
    action_fingerprint: str,
    threaded_tools: bool,
) -> list[BudgetConsumeRequest]:
    write = _is_write(tool, args)
    external_send = _is_external_send(tool, args)
    ui_inputs = _ui_input_count(tool, args, context)
    subprocesses = _subprocess_count(tool, args, context)
    has_side_effect = write or external_send or bool(ui_inputs) or bool(subprocesses)
    events = [
        BudgetConsumeRequest(
            kind="tool_call",
            action_fingerprint=(
                action_fingerprint if args.get("dry_run") is not True and has_side_effect else ""
            ),
        )
    ]
    if write:
        events.append(BudgetConsumeRequest(kind="write"))
    if external_send:
        events.append(BudgetConsumeRequest(kind="external_send"))
    if ui_inputs:
        events.append(BudgetConsumeRequest(kind="ui_input", amount=ui_inputs))
    if subprocesses:
        events.append(BudgetConsumeRequest(kind="subprocess", amount=subprocesses))
    if _is_retry(step, context):
        events.append(BudgetConsumeRequest(kind="retry"))
    if threaded_tools:
        fanout = _positive_int(
            context.get("automation_parallel_fanout", context.get("parallel_fanout")),
            default=2,
        )
        events.append(BudgetConsumeRequest(kind="parallel", parallel_fanout=max(2, fanout)))
    return events


def _bound_resources(tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> list[str]:
    resources = _string_values(context.get("automation_resource"))
    resources.extend(_string_values(context.get("automation_resources")))
    resource_kinds = {str(item).strip().casefold() for item in tool.resource_kinds}
    if tool.requires_authorized_path or resource_kinds & _FILE_RESOURCE_KINDS:
        for _name, value in candidate_authorized_paths(args):
            normalized = _normalize_file_resource(value)
            if normalized:
                resources.append(normalized)
    resources.extend(_target_urls(args))
    return _bounded_unique(resources, label="resources")


def _bound_recipients(tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> list[str]:
    if not _is_external_send(tool, args):
        return []
    recipients = _string_values(context.get("automation_recipient"))
    recipients.extend(_string_values(context.get("automation_recipients")))
    for raw_key, value in args.items():
        if _normalize_key(raw_key) in _RECIPIENT_KEYS:
            recipients.extend(_string_values(value))
    normalized = [str(item).strip().casefold() for item in recipients if str(item).strip()]
    return _bounded_unique(normalized, label="recipients")


def _bound_domains(args: dict[str, Any], context: dict[str, Any], recipients: list[str]) -> list[str]:
    domains = _string_values(context.get("automation_destination_domain"))
    domains.extend(_string_values(context.get("automation_destination_domains")))
    domains.extend(_url_domain(value) for value in _target_urls(args))
    domains.extend(_email_domain(recipient) for recipient in recipients)
    normalized = [_normalize_domain(item) for item in domains]
    return _bounded_unique([item for item in normalized if item], label="domains")


def _bound_egress(context: dict[str, Any], domains: list[str], recipients: list[str]) -> list[str]:
    targets = _string_values(context.get("automation_data_egress"))
    targets.extend(_string_values(context.get("automation_data_egress_targets")))
    targets.extend(f"origin:{domain}" for domain in domains)
    targets.extend(f"recipient:{recipient}" for recipient in recipients)
    return _bounded_unique(targets, label="egress targets")


def _target_urls(args: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for raw_key, value in args.items():
        key = _normalize_key(raw_key)
        if key in _URL_KEYS or key.endswith("_url"):
            urls.extend(_normalize_url_resource(item) for item in _string_values(value))
        elif key in {"action", "request"} and isinstance(value, dict):
            urls.extend(_target_urls(value))
        elif key == "actions" and isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    urls.extend(_target_urls(item))
    return _bounded_unique([item for item in urls if item], label="URLs")


def _is_write(tool: ToolDefinition, args: dict[str, Any]) -> bool:
    if args.get("dry_run") is True:
        return False
    effects = _effect_tokens(tool)
    if effects & _WRITE_EFFECTS or tool.destructive:
        return True
    risk = str(getattr(tool.risk_level, "value", tool.risk_level) or "")
    return not tool.is_read_only() and risk.startswith(("R2", "R3"))


def _is_external_send(tool: ToolDefinition, args: dict[str, Any]) -> bool:
    if args.get("dry_run") is True:
        return False
    effects = _effect_tokens(tool)
    return bool(
        effects & _EXTERNAL_SEND_EFFECTS
        or any("send" in effect or "submit" in effect or "upload" in effect for effect in effects)
        or (tool.external_network and _is_write(tool, args))
    )


def _ui_input_count(tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> int:
    if args.get("dry_run") is True or not (_effect_tokens(tool) & _UI_INPUT_EFFECTS):
        return 0
    explicit = _positive_int(context.get("automation_ui_input_count"), default=0)
    if explicit:
        return min(explicit, 10000)
    fields = args.get("fields")
    if isinstance(fields, dict) and fields:
        return min(len(fields), 10000)
    actions = args.get("actions")
    if isinstance(actions, list) and actions:
        return min(len(actions), 10000)
    return 1


def _subprocess_count(tool: ToolDefinition, args: dict[str, Any], context: dict[str, Any]) -> int:
    if args.get("dry_run") is True:
        return 0
    metadata = {
        *(_effect_tokens(tool)),
        *(str(item).strip().casefold() for item in tool.capabilities),
        *(str(item).strip().casefold() for item in tool.resource_kinds),
    }
    subprocess = bool(
        metadata & _SUBPROCESS_EFFECTS
        or any(token in item for item in metadata for token in ("developer_runtime", "process", "shell", "subprocess"))
    )
    if not subprocess:
        return 0
    return min(_positive_int(context.get("automation_subprocess_count"), default=1), 10000)


def _is_retry(step: PlanStep, context: dict[str, Any]) -> bool:
    if context.get("automation_is_retry") is True:
        return True
    model_action = step.model_action if isinstance(step.model_action, dict) else {}
    if model_action.get("automation_retry") is True or model_action.get("retry_of_step_id"):
        return True
    description = str(step.description or "").strip().casefold()
    observation = str(step.expected_observation or "").strip().casefold()
    return description.startswith(("retry after ", "recover failed step:")) or observation.startswith("recovery for ")


def _action_fingerprint(
    tool: ToolDefinition,
    args: dict[str, Any],
    *,
    resources: list[str],
    egress_targets: list[str],
    recipients: list[str],
    domains: list[str],
) -> str:
    payload = {
        "tool": tool.name,
        "tool_version": str(tool.tool_version or "1"),
        "args": _without_runtime_controls(args),
        "resources": resources,
        "egress_targets": egress_targets,
        "recipients": recipients,
        "domains": domains,
    }
    return stable_content_hash(payload)


def _without_runtime_controls(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_runtime_controls(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if _normalize_key(key) not in _RUNTIME_CONTROL_ARG_KEYS
        }
    if isinstance(value, list | tuple):
        return [_without_runtime_controls(item) for item in value]
    if isinstance(value, set):
        return [_without_runtime_controls(item) for item in sorted(value, key=str)]
    if isinstance(value, Path):
        return str(value)
    return value


def _effect_tokens(tool: ToolDefinition) -> set[str]:
    return {str(item).strip().casefold() for item in tool.effects if str(item).strip()}


def _normalize_key(value: Any) -> str:
    return str(value or "").replace("-", "_").strip().casefold()


def _normalize_file_resource(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _normalize_url_resource(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        parsed_port = parsed.port
    except ValueError as exc:
        raise IntentCapsuleError("destination URL is invalid") from exc
    if not parsed.scheme or not parsed.netloc:
        return text
    host = parsed.hostname or ""
    if not host:
        return ""
    port = f":{parsed_port}" if parsed_port else ""
    normalized_host = host.casefold()
    host_for_url = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    netloc = f"{host_for_url}{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", "", ""))


def _url_domain(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text if "://" in text else f"//{text}")
        return str(parsed.hostname or "").casefold()
    except ValueError as exc:
        raise IntentCapsuleError("destination URL is invalid") from exc


def _email_domain(recipient: str) -> str:
    text = str(recipient or "").strip().casefold()
    return text.rsplit("@", 1)[1] if "@" in text else ""


def _normalize_domain(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    if text.startswith("origin:"):
        text = text.split(":", 1)[1]
    try:
        parsed = urlsplit(text if "://" in text else f"//{text}")
        return str(parsed.hostname or text).casefold()
    except ValueError as exc:
        raise IntentCapsuleError("destination domain is invalid") from exc


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str | Path):
        return [str(value)] if str(value).strip() else []
    if isinstance(value, list | tuple | set):
        values: list[str] = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []


def _bounded_unique(values: list[str], *, label: str) -> list[str]:
    unique = sorted({str(item).strip() for item in values if str(item).strip()}, key=str.casefold)
    if len(unique) > _MAX_BOUND_TARGETS:
        raise IntentCapsuleError(f"too many {label} to bind safely")
    return unique


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
