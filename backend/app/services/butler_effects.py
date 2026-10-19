"""Matter-level protection against repeating a business submission across runs."""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit, urlunsplit

from app.core import db
from app.core.butler_models import ButlerMatter
from app.core.schemas import ToolCall
from app.policy.approval_binding import hmac_digest
from app.services.butler_evidence import is_submission


def canonical_submission_url(value: str) -> str:
    parts = urlsplit(value)
    host = (parts.hostname or "").encode("idna").decode("ascii").lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port is not None and port != (443 if parts.scheme == "https" else 80):
        host += f":{port}"
    segments: list[str] = []
    for segment in (parts.path or "/").split("/"):
        dot = segment.lower().replace("%2e", ".")
        if dot == "..":
            if len(segments) > 1:
                segments.pop()
        elif dot != ".":
            segments.append(segment)
    if parts.path.split("/")[-1].lower().replace("%2e", ".") in {".", ".."}:
        segments.append("")
    return urlunsplit((parts.scheme.lower(), host, "/".join(segments) or "/", parts.query, ""))


def submission_identity(tool_name: str, args: dict) -> str | None:
    if not is_submission({"tool_name": tool_name, "args": args}):
        return None
    nested = args.get("action")
    action = {**args, **(nested if isinstance(nested, dict) else {})}
    return hmac_digest(
        {
            "url": canonical_submission_url(str(action.get("url") or "")),
            "selector": action.get("selector") or "form",
            "fields": action.get("fields") or {},
        },
        prefix="browser-submission/v2",
    )


def submission_binding_hmac(call: ToolCall | dict) -> str:
    data = call.model_dump() if isinstance(call, ToolCall) else call
    return hmac_digest(
        {
            key: data.get(key, "")
            for key in ("id", "task_id", "step_id", "tool_name", "execution_intent_key", "submission_digest")
        },
        prefix="browser-submission-binding/v1",
    )


def _submission_matches(call: dict, identity: str, args: dict) -> bool:
    if not is_submission(call):
        return False
    digest = call.get("submission_digest")
    binding = call.get("submission_binding_hmac")
    if digest or binding:
        # Corrupt or copied bindings require reconciliation rather than a replay.
        if not digest or not binding or not hmac.compare_digest(str(binding), submission_binding_hmac(call)):
            return True
        if hmac.compare_digest(str(digest), identity):
            return True
        if not str(digest).startswith("browser-submission/v1:"):
            return False
        # Version 1 signed the URL's original spelling. Reconstruct its
        # canonical identity, or conservatively match a redacted old endpoint.
    prior_args = call.get("args") or {}
    if submission_identity(call["tool_name"], prior_args) == identity:
        return True
    # Legacy redacted journals cannot reconstruct a business identity. Keep
    # those submissions blocked at the same endpoint until their receipt is read.
    prior_nested = prior_args.get("action")
    nested = args.get("action")
    prior_action = {**prior_args, **(prior_nested if isinstance(prior_nested, dict) else {})}
    action = {**args, **(nested if isinstance(nested, dict) else {})}
    return (
        _contains_redaction(prior_action.get("selector")) or _contains_redaction(prior_action.get("fields"))
    ) and canonical_submission_url(str(prior_action.get("url") or "")) == canonical_submission_url(
        str(action.get("url") or "")
    )


def _contains_redaction(value) -> bool:
    if isinstance(value, dict):
        return any(_contains_redaction(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_redaction(item) for item in value)
    return value == "***"


def submission_may_have_happened(call: dict) -> bool:
    if call.get("dry_run") is True or call.get("status") not in {"committed", "outcome_unknown", "executing"}:
        return False
    rows = db.fetch_many("tool_results", "tool_call_id=?", (call["id"],), limit=10)
    if rows and all(
        row.get("output", {}).get("adapter") == "desktop_browser_host"
        and row.get("output", {}).get("commit_state") == "not_submitted"
        for row in rows
    ):
        return False
    return True


def repeated_submission(matter: ButlerMatter, tool_name: str, args: dict, *, current_tool_call_id: str = "") -> bool:
    identity = submission_identity(tool_name, args)
    if identity is None:
        return False
    return any(
        submission_may_have_happened(call)
        and not (
            call["id"] == current_tool_call_id
            and call["task_id"] == matter.active_task_id
            and call.get("status") == "executing"
        )
        and _submission_matches(call, identity, args)
        for task_id in matter.task_ids
        for call in db.list_tool_calls_for_task(task_id)
    )


def receipt_wait_timed_out(task, step, result) -> bool:
    """Keep a confirmed submission's checkpoint when only its DOM wait expires."""
    from app.orchestration.butler_runtime import bound_matter
    from app.services.butler_evidence import reviewed_results, uncertain_execution

    if (
        step.tool_name != "browser.wait_for_selector"
        or result is None
        or result.ok
        or result.error != "The requested browser element did not appear"
        or result.output.get("adapter") != "desktop_browser_host"
        or result.changed_paths
        or result.rollback_info
    ):
        return False
    matter = bound_matter(task)
    return bool(
        matter and not uncertain_execution(matter) and any(is_submission(call) for call, _ in reviewed_results(matter))
    )


async def inspect_pending_submission(matter: ButlerMatter) -> list[dict]:
    """Read available receipts before escalating an ambiguous submit; never replay it."""
    from app.services.butler_evidence import browser_observation, reviewed_results
    from app.services.butler_outcomes import check_browser_outcome
    from app.services.desktop_browser_adapter import request_task_observation

    reviewed = {call["id"] for call, _ in reviewed_results(matter)}
    pending = [
        call
        for task_id in matter.task_ids
        for call in db.list_tool_calls_for_task(task_id)
        if is_submission(call) and call["id"] not in reviewed and submission_may_have_happened(call)
    ]
    if not pending:
        return []
    evidence = []
    for condition in matter.completion_conditions:
        if condition.kind != "browser_fields":
            continue
        entry = {"condition": "pending_submission_readback", "subject": condition.subject, "matched": False}
        owned = browser_observation(matter, condition.subject)
        if owned:
            try:
                observation = await request_task_observation(
                    matter, owned["session_id"], selectors={check.label: check.selector for check in condition.checks}
                )
                entry["checks"] = check_browser_outcome(observation, condition)
                entry["matched"] = all(check["matched"] for check in entry["checks"])
            except (ValueError, RuntimeError, TimeoutError):
                entry["unavailable"] = True
        evidence.append(entry)
    return evidence
