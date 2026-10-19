"""Identity and scope constraints for managed browser sessions."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

BROWSER_ACTION_KINDS = {
    "open",
    "navigate",
    "click",
    "fill",
    "submit",
    "scroll",
    "wait",
    "screenshot",
    "observe",
    "cua",
}
WRITE_ACTION_KINDS = {"click", "fill", "submit", "scroll", "cua"}


_TASK_ID_KEYS = ("task_id", "taskId", "browser_task_id")
_ACCOUNT_ID_KEYS = (
    "account_id",
    "accountId",
    "browser_account_id",
    "account",
    "principal_id",
    "principalId",
    "user_id",
    "userId",
    "tenant_id",
    "tenantId",
)
_ORIGIN_SCOPE_KEYS = ("allowed_origins", "browser_allowed_origins")
_ACTION_SCOPE_KEYS = ("allowed_actions", "browser_allowed_actions")


def _optional_identity(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _mapping_values(mapping: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        if key in mapping:
            value = _optional_identity(mapping.get(key))
            if value:
                values.append(value)
    return values


def _coherent_identity(*mappings: dict[str, Any], keys: tuple[str, ...], label: str) -> str | None:
    values = _mapping_values(mappings[0], keys) if mappings else []
    for mapping in mappings[1:]:
        values.extend(_mapping_values(mapping, keys))
    unique = {value for value in values}
    if len(unique) > 1:
        raise ValueError(f"Conflicting {label} values cannot be used for one browser session.")
    return next(iter(unique), None)


def _requested_session_identity(
    args: dict[str, Any],
    action: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str | None, str | None]:
    task_id = _coherent_identity(args, action, context, keys=_TASK_ID_KEYS, label="task_id")
    account_id = _coherent_identity(args, action, context, keys=_ACCOUNT_ID_KEYS, label="account_id")
    runtime = context.get("runtime")
    if account_id is None and context.get("butler_matter_id") and runtime is not None:
        from app.orchestration.butler_runtime import bound_matter
        from app.services.butler_scopes import matter_scopes, origin_of

        matter = bound_matter(runtime.task)
        origin = origin_of(str(action.get("url") or args.get("url") or ""))
        accounts = {scope.account_id for scope in matter_scopes(matter) if origin in scope.origins} if matter else set()
        if len(accounts) == 1:
            account_id = next(iter(accounts)) or None
        elif len(accounts) > 1:
            raise ValueError("Multiple authorized accounts match; select the account explicitly.")
    return task_id, account_id


def _scope_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, list | tuple | set | frozenset):
        raw = [str(part).strip() for part in value]
    else:
        raise ValueError("Browser session scope values must be strings or lists of strings.")
    return [part for part in raw if part]


def _scope_from_mapping(mapping: dict[str, Any], keys: tuple[str, ...]) -> tuple[bool, list[str]]:
    for key in keys:
        if key in mapping:
            return True, _scope_values(mapping.get(key))
    return False, []


def _normalise_origin_scope(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        origin = _url_origin(value)
        if origin not in normalized:
            normalized.append(origin)
    return normalized


def _normalise_action_scope(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        action = _canonical_action_kind(value)
        if action not in BROWSER_ACTION_KINDS:
            raise ValueError(f"Unsupported browser action in allowed_actions: {value}")
        if action not in normalized:
            normalized.append(action)
    return normalized


def _session_scope(
    args: dict[str, Any],
    context: dict[str, Any],
    *,
    initial_url: str = "",
) -> tuple[list[str], list[str]]:
    context_has_origins, context_origins_raw = _scope_from_mapping(context, _ORIGIN_SCOPE_KEYS)
    args_has_origins, args_origins_raw = _scope_from_mapping(args, _ORIGIN_SCOPE_KEYS)
    context_origins = _normalise_origin_scope(context_origins_raw)
    args_origins = _normalise_origin_scope(args_origins_raw)
    if context_has_origins and args_has_origins and not set(args_origins).issubset(context_origins):
        raise ValueError("Browser session allowed_origins cannot exceed the task scope.")
    allowed_origins = args_origins if args_has_origins else context_origins

    if initial_url:
        initial_origin = _url_origin(initial_url)
        if allowed_origins and initial_origin not in allowed_origins:
            raise ValueError("Initial browser URL is outside allowed_origins.")
        if not allowed_origins:
            allowed_origins = [initial_origin]

    context_has_actions, context_actions_raw = _scope_from_mapping(context, _ACTION_SCOPE_KEYS)
    args_has_actions, args_actions_raw = _scope_from_mapping(args, _ACTION_SCOPE_KEYS)
    context_actions = _normalise_action_scope(context_actions_raw)
    args_actions = _normalise_action_scope(args_actions_raw)
    if context_has_actions and args_has_actions and not set(args_actions).issubset(context_actions):
        raise ValueError("Browser session allowed_actions cannot exceed the task scope.")
    allowed_actions = args_actions if args_has_actions else context_actions
    if not allowed_actions and not (context_has_actions or args_has_actions):
        allowed_actions = sorted(BROWSER_ACTION_KINDS)
    return allowed_origins, allowed_actions


def _canonical_action_kind(value: Any) -> str:
    action = str(value or "").casefold().replace("_", "-").strip()
    if action.startswith("browser."):
        action = action.removeprefix("browser.")
    return {
        "read-page": "observe",
        "open-url": "open",
        "wait-for-selector": "wait",
        "click-element": "click",
        "fill-form": "fill",
        "submit-form": "submit",
        "cua-run": "cua",
    }.get(action, action)


def _url_origin(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Browser origin must be an absolute http(s) URL without credentials.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Browser origin contains an invalid port.") from exc
    if port is None:
        port = 443 if parsed.scheme.casefold() == "https" else 80
    hostname = parsed.hostname.casefold().rstrip(".")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    return f"{parsed.scheme.casefold()}://{hostname}:{port}"
