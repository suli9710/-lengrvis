"""Restore/adopt a live desktop tab only within the same signed matter."""

from __future__ import annotations

import asyncio
import re

from app.orchestration.butler_runtime import bound_matter
from app.services.browser_execution_grants import issue_browser_command
from app.services.browser_host_bridge_service import browser_host_bridge_hub
from app.services.butler_scopes import origin_of


def requested_page_read(task, tool, args: dict) -> bool:
    """Permit reading an exact user-nominated URL within the current scope.

    A prior page remains tainted and cannot nominate links on the user's behalf.
    Network validation and BrowserHost ownership checks still run in the tool.
    """
    from app.services.butler_scopes import action_scope, matter_scopes

    if tool.name != "browser.read_page" or tool.trust_tier != "builtin" or not tool.is_read_only():
        return False
    matter = bound_matter(task)
    if matter is None or matter.source != "user":
        return False
    texts = [matter.goal, *matter.answers.get("user_replies", [])]
    # These non-runtime fields are written only by the versioned user response API.
    runtime_answers = {
        "verification_feedback",
        "completed_execution_evidence",
        "user_replies",
        "resolved_wait",
        "current_local_time",
    }
    for key, answer in matter.answers.items():
        if key not in runtime_answers:
            texts.extend(answer if isinstance(answer, list) else [answer])
    nominated = {
        match.rstrip(",.;:!?)]}")
        for text in texts
        if isinstance(text, str)
        for match in re.findall(r'https?://[^\s<>"，。；、）】,]+', text)
    }
    if args.get("url") not in nominated:
        return False
    try:
        return action_scope(tool.name, args, matter_scopes(matter)) is not None
    except ValueError:
        return False


def bind_owned_readback(task, tool, args: dict) -> bool:
    """Pin a DOM-only read to a live owned tab; never authorize navigation.

    External page content remains tainted, but waiting for or reading a receipt
    does not execute that content. An explicit session prevents a disappearing
    tab from silently becoming a new network request at execution time.
    """
    if (
        tool.name not in {"browser.observe", "browser.wait_for_selector"}
        or tool.trust_tier != "builtin"
        or not tool.is_read_only()
    ):
        return False
    matter = bound_matter(task)
    if matter is None:
        return False
    state = browser_host_bridge_hub.status()
    if not state["connected"]:
        return False
    origin = origin_of(str(args.get("url") or ""))
    requested_id = args.get("session_id")
    matches = [
        item
        for item in (state.get("snapshot") or {}).get("sessions", [])
        if item.get("task_id") in matter.task_ids
        and item.get("status") not in {"closed", "stopped"}
        and not item.get("takeover")
        and not item.get("paused")
        and (not requested_id or item.get("id") == requested_id)
        and (not origin or origin_of(item.get("current_url", "")) == origin)
        and (not args.get("account_id") or args["account_id"] == item.get("account_id"))
    ]
    if len(matches) != 1 or (not requested_id and not origin):
        return False
    args["session_id"] = matches[0]["id"]
    return True


def restore_managed_session(args: dict, context: dict):
    from app.services.browser_activity_runtime import (
        BROWSER_ACTION_KINDS,
        BrowserSession,
        _normalise_origin_scope,
        _requested_session_identity,
    )

    matter = bound_matter(context["runtime"].task)
    if matter is None:
        return None
    state = browser_host_bridge_hub.status()
    if not state["connected"]:
        return None
    task_id, account = _requested_session_identity(args, {}, context)
    requested_id = args.get("session_id")
    origin = origin_of(str(args.get("url") or ""))
    matches = [
        item
        for item in (state.get("snapshot") or {}).get("sessions", [])
        if item.get("task_id") in matter.task_ids
        and item.get("status") not in {"closed", "stopped"}
        and (not context.get("browser_exact_read_url") or item.get("current_url") == context["browser_exact_read_url"])
        and (
            (requested_id and item["id"] == requested_id)
            or (
                not requested_id
                and origin_of(item.get("current_url", "")) == origin
                and str(item.get("account_id") or "") == (account or "")
            )
        )
    ]
    if not matches:
        if requested_id:
            raise ValueError("The retained browser tab is unavailable or belongs to another matter.")
        return None
    owned = [item for item in matches if item.get("task_id") == task_id]
    matches = owned or matches
    if len(matches) != 1:
        raise ValueError("Multiple retained tabs match; specify session_id.")
    previous = matches[0]
    if previous.get("takeover") or previous.get("paused"):
        raise ValueError("Return control of the original page before continuing.")
    if account and account != previous.get("account_id"):
        raise ValueError("Retained browser account differs from the requested account.")
    session = BrowserSession(
        id=previous["id"],
        task_id=task_id,
        account_id=previous.get("account_id") or None,
        current_url=previous["current_url"],
        title=previous.get("title", ""),
        allowed_origins=_normalise_origin_scope(
            list(previous.get("allowed_origins") or [origin_of(previous["current_url"])])
        ),
        allowed_actions=list(BROWSER_ACTION_KINDS),
        last_observation=previous.get("last_observation") or {},
    )
    if previous.get("task_id") != task_id:
        grant = issue_browser_command(
            session,
            {"kind": "observe", "url": session.current_url},
            {**context, "previous_browser_task_id": previous["task_id"]},
            operation="adopt",
        )
        result = asyncio.run(browser_host_bridge_hub.request_authorized_action(grant))
        if not result.get("ok"):
            raise ValueError("The original page could not be resumed safely.")
    return session
