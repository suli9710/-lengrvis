"""Fresh completion evidence, derived from reviewed execution records."""

from __future__ import annotations

from pathlib import Path

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import ButlerMatter
from app.core.errors import SecurityError
from app.core.schemas import ToolResult, now_iso
from app.orchestration.resource_state import resource_state
from app.orchestration.tool_execution_journal import runtime_review_allows_result_reuse
from app.services.butler_scopes import application_in_scope, matter_scopes, origin_of, path_in_scope


def execution_history(matter: ButlerMatter) -> list[dict]:
    """Only journal-derived history may inform continuation; chat cannot supply it."""
    from app.policy.redaction import redact_value

    reviewed = {call["id"]: result for call, result in reviewed_results(matter)}
    history = []
    for task_id in matter.task_ids:
        for call in reversed(db.list_tool_calls_for_task(task_id)):
            if call.get("status") == "prepared":
                continue
            result = reviewed.get(call["id"])
            history.append(
                {
                    "tool_call_id": call["id"],
                    "tool": call["tool_name"],
                    "status": call.get("status"),
                    "args": redact_value(call.get("args") or {}),
                    "reviewed_ok": result is not None,
                    "resources": result.output.get("_resource_state_after", []) if result else [],
                    "observation": {
                        key: result.output[key]
                        for key in ("url", "extracted", "elements", "controls")
                        if key in result.output
                    }
                    if result
                    else {},
                }
            )
    return history


def uncertain_execution(matter: ButlerMatter) -> bool:
    reviewed_ids = {call["id"] for call, _ in reviewed_results(matter)}
    for task_id in matter.task_ids:
        for call in db.list_tool_calls_for_task(task_id):
            if str(call.get("risk_level", "")).startswith("R0"):
                continue
            if is_submission(call):
                from app.services.butler_effects import submission_may_have_happened

                if not submission_may_have_happened(call):
                    continue
            if call.get("status") in {"executing", "outcome_unknown"}:
                return True
            if call.get("status") == "committed" and call["id"] not in reviewed_ids:
                results = db.fetch_many("tool_results", "tool_call_id = ?", (call["id"],), limit=10)
                if any(
                    not (result := ToolResult.model_validate(data)).ok
                    and not result.output.get("outcome_unknown")
                    and runtime_review_allows_result_reuse(result)
                    for data in results
                ):
                    # A reviewed explicit failure is known. It is not success
                    # evidence, but must not permanently prevent replanning.
                    continue
                return True
    return False


def is_submission(call: dict) -> bool:
    from app.services.browser_session_scope import _canonical_action_kind

    args = call.get("args") or {}
    action = args.get("action") if isinstance(args, dict) else None
    action = {**args, **(action if isinstance(action, dict) else {})}
    return call.get("tool_name") == "browser.submit_form" or (
        call.get("tool_name") == "browser.act" and _canonical_action_kind(action.get("kind")) == "submit"
    )


def resume_readiness(matter: ButlerMatter) -> tuple[bool, str]:
    matter_scopes(matter)
    if matter.artifacts:
        from app.services.office_artifacts import verify_artifact

        for artifact in matter.artifacts:
            if artifact.state == "writing":
                return False, "文档保存曾中断，需核对预留成果；不会覆盖或重复生成。"
            if artifact.state == "verified":
                try:
                    verify_artifact(matter, artifact)
                except (ValueError, OSError):
                    return False, "已有成果发生变化或不可读取，请核对后继续；不会覆盖用户修改。"
    if uncertain_execution(matter):
        return False, "已有操作的执行结果尚不明确，需先核对，不能自动重放。"
    if not reviewed_results(matter):
        return True, "尚无已执行动作，可接续原检查点。"
    valid, _, reason = verify_execution(matter)
    return valid, reason


def progress_key(evidence: list[dict], reason: str) -> str:
    """Repeated observations do not become progress merely by getting new IDs."""
    volatile = {"verified_at", "observed_at", "created_at", "updated_at", "tool_call_id", "task_id", "run_id"}

    def stable(value):
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items() if key not in volatile}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    # Repeating the same read also must not enlarge the fingerprint.
    observations = sorted({store.source_key(stable(item)) for item in evidence})
    return store.source_key(observations, reason)


def browser_progress_key(observation: dict) -> str:
    return store.source_key({key: observation.get(key) for key in ("url", "title", "text", "dom_hash")})


def browser_observation(matter: ButlerMatter, subject: str) -> dict | None:
    """Read only a unique, live, task-owned BrowserHost session already on the requested origin."""
    from app.services.browser_host_bridge_service import browser_host_bridge_hub

    scopes = matter_scopes(matter)
    origin = origin_of(subject)
    if not origin:
        return None
    matching_scopes = [scope for scope in scopes if "observe" in scope.allowed_actions and origin in scope.origins]
    if matter.source != "user" and not matching_scopes:
        return None
    state = browser_host_bridge_hub.status()
    if not state.get("connected"):
        return None
    owned_tasks = {*matter.task_ids, matter.active_task_id} - {""}
    candidates = []
    for session in (state.get("snapshot") or {}).get("sessions", []):
        if not isinstance(session, dict) or session.get("task_id") not in owned_tasks:
            continue
        if origin_of(str(session.get("current_url") or "")) != origin:
            continue
        if (
            session.get("paused")
            or session.get("takeover")
            or session.get("status") not in {"active", "ready", "idle", "running"}
        ):
            continue
        if matching_scopes and not any(
            scope.account_id == str(session.get("account_id") or "") for scope in matching_scopes
        ):
            continue
        observed = session.get("last_observation")
        if (
            not isinstance(observed, dict)
            or origin_of(str(observed.get("url") or session.get("current_url") or "")) != origin
        ):
            continue
        candidates.append(
            {
                **observed,
                "session_id": session["id"],
                "task_id": session["task_id"],
                "account_id": session.get("account_id") or "",
                "url": session.get("current_url"),
                "observed_at": observed.get("observed_at") or observed.get("timestamp") or "",
            }
        )
    exact = [item for item in candidates if item.get("url") == subject]
    candidates = exact or candidates
    return candidates[0] if len(candidates) == 1 else None


def reviewed_results(matter: ButlerMatter) -> list[tuple[dict, ToolResult]]:
    rows = []
    for task_id in matter.task_ids:
        for call in reversed(db.list_tool_calls_for_task(task_id)):
            if call.get("status") != "committed":
                continue
            results = db.fetch_many("tool_results", "tool_call_id = ?", (call["id"],), limit=10)
            for data in results:
                result = ToolResult.model_validate(data)
                if result.ok and runtime_review_allows_result_reuse(result):
                    rows.append((call, result))
                    break
    return rows


def _same_state(expected: dict, actual: dict) -> bool:
    keys = {"exists", "is_file", "is_dir", "size", "mtime_ns", "inode", "sha256"}
    return "exists" in expected and all(actual.get(key) == expected[key] for key in keys if key in expected)


def _same_control(left: dict, right: dict) -> bool:
    keys = ("process_id", "automation_id", "name", "control_type", "class_name")
    return bool(left.get("process_id") and (left.get("automation_id") or left.get("name"))) and all(
        left.get(key) == right.get(key) for key in keys if left.get(key)
    )


def verify_execution(matter: ButlerMatter) -> tuple[bool, list[dict], str]:
    rows = reviewed_results(matter)
    if not rows:
        return False, [], "尚无经过审查的实际执行结果。"
    latest_states: dict[str, dict] = {}
    unverified = []
    evidence = []
    for index, (call, result) in enumerate(rows):
        name = call["tool_name"]
        args = call.get("args") or {}
        after = result.output.get("_resource_state_after") or []
        for state in after:
            if isinstance(state, dict) and state.get("path"):
                latest_states[str(Path(state["path"]).resolve()).casefold()] = state
        modified = name.startswith(("ui_automation.", "browser.")) and name.split(".", 1)[1] in {
            "type_text",
            "click",
            "hotkey",
            "key_press",
            "click_at",
            "drag",
            "fill_form",
            "submit_form",
            "click_element",
            "act",
        }
        if name == "browser.act" and (args.get("action") or {}).get("kind") in {
            "observe",
            "wait",
            "screenshot",
            "open",
            "navigate",
        }:
            modified = False
        if name == "ui_automation.type_text":
            verified = any(
                later["tool_name"] == "ui_automation.get_property"
                and output.output.get("verified") is True
                and _same_control(args, later.get("args") or {})
                for later, output in rows[index + 1 :]
            )
            if not verified:
                unverified.append(name)
        elif (
            modified
            and not result.output.get("verified")
            and not any(condition.kind != "execution_evidence" for condition in matter.completion_conditions)
        ):
            unverified.append(name)
        if result.changed_paths and not after:
            unverified.append(name)
        evidence.append(
            {
                "tool_call_id": call["id"],
                "tool": name,
                "verified_at": now_iso(),
                "rollback_available": bool(result.rollback_info),
                "artifacts": list(result.changed_paths),
                "observation": {
                    key: result.output[key]
                    for key in ("sha256", "verified", "chars", "exists", "value", "text", "url")
                    if key in result.output and key not in {"text", "value"}
                },
            }
        )
    for expected in latest_states.values():
        try:
            actual = resource_state(expected["path"])
        except OSError:
            return False, evidence, "执行结果对应的文件已不可读取。"
        if not _same_state(expected, actual):
            evidence.append({"resource": expected["path"], "observation": actual, "matched": False})
            return False, evidence, "执行后的资源又发生变化，需要重新核验。"
    if unverified:
        return False, evidence, "需要补充实际结果核验：" + ", ".join(sorted(set(unverified)))
    return True, evidence, "实际执行记录与资源状态已核验。"


async def verify_conditions(matter: ButlerMatter) -> tuple[bool, list[dict], str]:
    scopes = matter_scopes(matter)
    if uncertain_execution(matter):
        return False, [], "已有操作结果尚不明确，需要先核对原执行，不能凭回执文字直接结束事项。"
    valid, evidence, reason = verify_execution(matter)
    if not valid:
        return valid, evidence, reason
    if matter.artifacts or any(condition.kind == "office_artifacts" for condition in matter.completion_conditions):
        from app.services.butler_office_completion import verify_office_completion

        office_ok, office_evidence, office_reason = verify_office_completion(matter)
        evidence.extend(office_evidence)
        if not office_ok:
            return False, evidence, office_reason
    if any(is_submission(call) for call, _ in reviewed_results(matter)) and not any(
        condition.kind == "browser_fields" and any(check.operator == "equals" for check in condition.checks)
        for condition in matter.completion_conditions
    ):
        return False, evidence, "提交已执行；请在原网页读取业务回执并核对具体字段，不能重复提交。"
    for condition in matter.completion_conditions:
        if condition.kind == "execution_evidence":
            continue
        if condition.kind == "office_artifacts":
            continue
        if condition.kind.startswith("file_"):
            from app.core.paths import resolve_authorized
            from app.llm.registry import get_effective_settings

            roots = list(get_effective_settings().allowed_directories)
            scopes = matter_scopes(matter)
            roots.extend(path for scope in scopes for path in scope.directories if path not in roots)
            if matter.source != "user":
                scopes = matter_scopes(matter)
                if not any(path_in_scope(condition.subject, scope) for scope in scopes):
                    return False, evidence, "结果核验超出授权目录。"
                roots = [path for scope in scopes for path in scope.directories]
            try:
                path = resolve_authorized(condition.subject, roots)
                matched = path.is_file()
                if matched and condition.kind == "file_text":
                    if condition.expected is None:
                        matched = False
                    else:
                        with path.open("r", encoding="utf-8-sig", newline="") as source:
                            matched = source.read(len(condition.expected) + 1) == condition.expected
                if matched and condition.kind == "file_equal":
                    reference = resolve_authorized(condition.reference, roots)
                    matched = reference.is_file() and _equal_files(path, reference)
                if matched and condition.kind == "file_json":
                    import json

                    from app.services.butler_outcomes import check_json_outcome

                    if path.stat().st_size > 4 * 1024 * 1024:
                        matched = False
                    else:
                        checks = check_json_outcome(json.loads(path.read_text(encoding="utf-8-sig")), condition)
                        matched = all(check["matched"] for check in checks)
                        evidence.append({"condition": condition.kind, "subject": condition.subject, "checks": checks})
            except (OSError, ValueError, SecurityError, RecursionError):
                matched = False
        elif condition.kind == "ui_text":
            from app.perception.ui_automation import WindowsCOMUIAutomationTarget

            if matter.source != "user" and not any(
                "observe" in scope.allowed_actions and application_in_scope(condition.selector.get("process_id"), scope)
                for scope in scopes
            ):
                return False, evidence, "结果核验超出授权应用。"
            if not any(
                call["tool_name"].startswith("ui_automation.")
                and _same_control(condition.selector, call.get("args") or {})
                for call, _ in reviewed_results(matter)
            ):
                return False, evidence, "需要先观察并绑定要核验的应用控件。"
            value = await WindowsCOMUIAutomationTarget().get_property(condition.selector, "text")
            matched = value is not None and value == condition.expected
        elif condition.kind in {"browser_text", "browser_fields"}:
            from app.services.butler_service import _after
            from app.services.desktop_browser_adapter import request_task_observation

            output = browser_observation(matter, condition.subject)
            if output:
                try:
                    output = await request_task_observation(
                        matter,
                        output["session_id"],
                        selectors={check.label: check.selector for check in condition.checks} or None,
                    )
                except (ValueError, RuntimeError, TimeoutError):
                    output = None
            matched = bool(
                output
                and _after(output.get("observed_at", ""), matter.created_at)
                and condition.expected
                and condition.expected in str(output.get("text") or output.get("content") or "")
            )
            if output and condition.kind == "browser_fields":
                from app.services.butler_outcomes import check_browser_outcome

                checks = check_browser_outcome(output, condition)
                matched = _after(output.get("observed_at", ""), matter.created_at) and all(
                    check["matched"] for check in checks
                )
                evidence.append({"condition": condition.kind, "subject": condition.subject, "checks": checks})
        else:
            matched = False
        if not matched:
            evidence.append({"condition": condition.kind, "subject": condition.subject, "matched": False})
            return False, evidence, "事项的完成条件尚未满足。"
        evidence.append({"condition": condition.kind, "verified_at": now_iso(), "matched": True})
    return True, evidence, reason


def _equal_files(left: Path, right: Path) -> bool:
    before = (left.stat(), right.stat())
    if before[0].st_size != before[1].st_size:
        return False
    with left.open("rb") as one, right.open("rb") as two:
        while True:
            block = one.read(1024 * 1024)
            if block != two.read(1024 * 1024):
                return False
            if not block:
                break
    after = (left.stat(), right.stat())
    return all(
        (a.st_size, a.st_mtime_ns, a.st_ino) == (b.st_size, b.st_mtime_ns, b.st_ino)
        for a, b in zip(before, after, strict=True)
    )


def undo_inventory(matter: ButlerMatter) -> dict[str, str]:
    """Offer whole-matter undo only when its complete journal can be reversed."""
    from app.tools.rollback_tools import build_rollback_plan, load_rollback_snapshot, rollback_snapshot_hmac

    inventory = {}
    for task_id in matter.task_ids:
        task = db.fetch_one("tasks", task_id)
        if task and task.get("status") == "rolled_back":
            continue
        if not task or task.get("status") not in {"completed", "failed", "denied"}:
            return {}
        snapshot = load_rollback_snapshot(task_id)
        plan = build_rollback_plan(task_id, snapshot=snapshot)
        if not plan.get("complete") or any(
            action in {"restore_from_recycle_bin", "permanent_delete_unrecoverable"}
            for step in plan.get("steps", [])
            for action in step.get("actions", [])
        ):
            return {}
        if plan.get("count", 0):
            inventory[task_id] = rollback_snapshot_hmac(snapshot)
    return inventory


def undo_available(matter: ButlerMatter) -> bool:
    return bool(undo_inventory(matter))
