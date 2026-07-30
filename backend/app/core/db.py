from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import get_base_settings, get_env
from app.core.db_migrations import apply_schema_migrations
from app.core.db_schema import ensure_document_chunks_fts, initialize_schema
from app.core.db_tables import (
    _ENSURE_COLUMNS_TABLES,
    _SAFE_COLUMN_DEFINITION_RE,
    _SAFE_COLUMN_NAME_RE,
)
from app.core.db_tables import (
    DATA_TABLES as DATA_TABLES,
)
from app.core.db_tables import (
    UNSAFE_WHERE_TOKENS as UNSAFE_WHERE_TOKENS,
)
from app.core.db_tables import (
    WHERE_ALLOWED_COLUMNS as WHERE_ALLOWED_COLUMNS,
)
from app.core.db_tables import (
    WHERE_COMPARISON_RE as WHERE_COMPARISON_RE,
)
from app.core.db_tables import (
    WHERE_CONDITION_JOINER_RE as WHERE_CONDITION_JOINER_RE,
)
from app.core.db_tables import (
    WHERE_IN_RE as WHERE_IN_RE,
)
from app.core.db_tables import (
    WHERE_NULL_RE as WHERE_NULL_RE,
)
from app.core.db_tables import (
    WHERE_OR_RE as WHERE_OR_RE,
)

_DATA_DIR_OVERRIDE: ContextVar[str | None] = ContextVar("lengrvis_data_dir_override", default=None)
AUDIT_GENESIS_HASH = "0" * 64
AUDIT_HMAC_SECRET_FILE = "audit_hmac.secret"  # noqa: S105
AUDIT_HMAC_SECRET_DIR = "secrets"  # noqa: S105
AUDIT_FAIL_CLOSED_ENV_VAR = "LENGRVIS_AUDIT_FAIL_CLOSED"
AUDIT_ANCHOR_FILE = "audit_chain.anchor.json"
SENSITIVE_RECORD_INTEGRITY_VERSION = 1
SENSITIVE_RECORD_INTEGRITY_TABLE = "sensitive_record_integrity"
SENSITIVE_RECORD_INTEGRITY_KINDS = frozenset({"approvals", "app_settings", "permission_policies", "audit_chain_heads"})
AUDIT_APPEND_ONLY_TRIGGERS = frozenset(
    {
        "audit_events_no_update",
        "audit_events_no_delete",
        "audit_chain_heads_no_update",
        "audit_chain_heads_no_delete",
    }
)

# Audit hot-path caches (2-H2): avoid re-reading the HMAC secret file and
# re-querying the chain tail on every event. RLock is required because a cold
# chain-head cache can verify a persisted head, which re-enters the secret cache.
_AUDIT_CACHE_LOCK = threading.RLock()
_AUDIT_SECRET_CACHE: dict[str, str] = {}
_AUDIT_CHAIN_HEADS: dict[str, tuple[int, str]] = {}

# Single-process event-write serializer (R4-C2): audit/run_events inserts open
# BEGIN IMMEDIATE transactions from both the event loop and worker threads
# (watcher, scheduler, cancel storms writing step.invalid_transition_audited).
# Serializing these hot writes inside the process prevents them from racing
# each other into "database is locked" once any writer holds the SQLite write
# lock longer than busy_timeout. Cross-process contention is still covered by
# WAL + busy_timeout. RLock so a path that already holds the lock can audit
# its own failure without self-deadlocking.
_EVENT_WRITE_LOCK = threading.RLock()


class SensitiveRecordIntegrityError(RuntimeError):
    """Raised when a locally stored sensitive record fails HMAC verification."""


_STARTUP_SENSITIVE_INTEGRITY_STATUS: dict[str, Any] = {"ok": True, "checked": 0, "failures": []}


@dataclass
class _ThreadConnectionState:
    path: Path
    conn: sqlite3.Connection
    depth: int = 0


_CONNECTION_LOCAL = threading.local()


def reset_audit_caches() -> None:
    with _AUDIT_CACHE_LOCK:
        _AUDIT_SECRET_CACHE.clear()
        _AUDIT_CHAIN_HEADS.clear()


def register_settings_invalidation_hook(fn: Callable[[], None]) -> None:
    from app.core.db_settings import register_settings_invalidation_hook as _register_hook

    _register_hook(fn)


def _notify_settings_invalidated() -> None:
    from app.core.db_settings import notify_settings_invalidated

    notify_settings_invalidated()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def db_path() -> Path:
    override = _DATA_DIR_OVERRIDE.get()
    data_dir = Path(override or get_base_settings().data_dir)
    path = data_dir / "lengrvis.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def audit_hmac_secret_path() -> Path:
    configured = str(get_env("LENGRVIS_AUDIT_HMAC_SECRET_FILE") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = db_path().parent / path
        return path.resolve(strict=False)
    return db_path().parent / AUDIT_HMAC_SECRET_DIR / AUDIT_HMAC_SECRET_FILE


@contextmanager
def using_data_dir(data_dir: str | Path | None) -> Iterator[None]:
    if not data_dir:
        yield
        return
    token = _DATA_DIR_OVERRIDE.set(str(data_dir))
    try:
        yield
    finally:
        _DATA_DIR_OVERRIDE.reset(token)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    state = _thread_connection_state()
    if state is not None and state.path != path:
        _close_thread_connection(state)
        state = None
    if state is None:
        state = _ThreadConnectionState(path=path, conn=_open_connection(path))
        _CONNECTION_LOCAL.state = state

    conn = state.conn
    state.depth += 1
    try:
        yield conn
    except BaseException:
        if state.depth == 1 and conn.in_transaction:
            conn.rollback()
        raise
    else:
        if state.depth == 1 and conn.in_transaction:
            conn.commit()
    finally:
        state.depth = max(0, state.depth - 1)


def _thread_connection_state() -> _ThreadConnectionState | None:
    state = getattr(_CONNECTION_LOCAL, "state", None)
    if isinstance(state, _ThreadConnectionState):
        return state
    return None


def _open_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL + busy_timeout: watcher/scheduler threads and async handlers open
    # concurrent connections; without these, writers race into
    # "database is locked" errors under load. Set the timeout before WAL
    # initialization because changing journal mode can itself need the lock.
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _close_thread_connection(state: _ThreadConnectionState) -> None:
    try:
        if state.conn.in_transaction:
            state.conn.rollback()
    finally:
        state.conn.close()
        if getattr(_CONNECTION_LOCAL, "state", None) is state:
            _CONNECTION_LOCAL.state = None


def close_thread_connection() -> None:
    state = _thread_connection_state()
    if state is not None:
        _close_thread_connection(state)


def reset_connection_state() -> None:
    """Drop thread-local DB handles before tests or runtime data-dir switches."""
    close_thread_connection()
    reset_audit_caches()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


_INIT_DB_LOCK = threading.Lock()
_INITIALIZED_DB_PATHS: set[str] = set()


def reset_init_db_cache() -> None:
    with _INIT_DB_LOCK:
        _INITIALIZED_DB_PATHS.clear()
    close_thread_connection()


def init_db(*, force: bool = False) -> None:
    # init_db is used as a lazy-init guard on hot paths (event publishing,
    # usage recording, ...). Re-running the full schema script there is pure
    # overhead, so each db path is initialized once per process.
    path = str(db_path())
    if not force and path in _INITIALIZED_DB_PATHS and Path(path).exists():
        _ensure_cached_schema()
        return
    with _INIT_DB_LOCK:
        if not force and path in _INITIALIZED_DB_PATHS and Path(path).exists():
            _ensure_cached_schema()
            return
        _init_db_schema()
        _INITIALIZED_DB_PATHS.add(path)


def _init_db_schema() -> None:
    with connect() as conn:
        initialize_schema(conn, _ensure_columns)
        apply_schema_migrations(conn)


def _ensure_cached_schema() -> None:
    state = _thread_connection_state()
    if state is not None and state.conn.in_transaction:
        return
    with connect() as conn:
        ensure_document_chunks_fts(conn)


def upsert_model(table: str, model: BaseModel, *, task_id: str | None = None, status: str | None = None) -> None:
    from app.core.db_upserts import upsert_model as _upsert_model

    _upsert_model(table, model, task_id=task_id, status=status)


def reserve_tool_call(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    from app.core.db_tool_calls import reserve_tool_call as _reserve_tool_call

    return _reserve_tool_call(data)


def fetch_tool_call(call_id: str) -> dict[str, Any] | None:
    from app.core.db_tool_calls import fetch_tool_call as _fetch_tool_call

    return _fetch_tool_call(call_id)


def list_tool_calls_for_task(task_id: str, *, limit: int = 5000) -> list[dict[str, Any]]:
    from app.core.db_tool_calls import list_tool_calls_for_task as _list_tool_calls_for_task

    return _list_tool_calls_for_task(task_id, limit=limit)


def list_tool_call_ids_by_status(status: str) -> list[str]:
    from app.core.db_tool_calls import list_tool_call_ids_by_status as _list_tool_call_ids_by_status

    return _list_tool_call_ids_by_status(status)


def claim_tool_call_execution(call_id: str, started_at: str) -> dict[str, Any] | None:
    from app.core.db_tool_calls import claim_tool_call_execution as _claim_tool_call_execution

    return _claim_tool_call_execution(call_id, started_at)


def commit_tool_call_execution(call_id: str, committed_at: str) -> dict[str, Any] | None:
    from app.core.db_tool_calls import commit_tool_call_execution as _commit_tool_call_execution

    return _commit_tool_call_execution(call_id, committed_at)


def recover_tool_call_execution(call_id: str, recovered_at: str) -> dict[str, Any] | None:
    from app.core.db_tool_calls import recover_tool_call_execution as _recover_tool_call_execution

    return _recover_tool_call_execution(call_id, recovered_at)


def mark_tool_call_outcome_unknown(
    call_id: str,
    outcome_unknown_at: str,
    *,
    expected_status: str,
) -> dict[str, Any] | None:
    from app.core.db_tool_calls import mark_tool_call_outcome_unknown as _mark_tool_call_outcome_unknown

    return _mark_tool_call_outcome_unknown(
        call_id,
        outcome_unknown_at,
        expected_status=expected_status,
    )


def register_read_barrier(table: str, barrier: Callable[[], None]) -> None:
    from app.core.db_queries import register_read_barrier as _register_read_barrier

    _register_read_barrier(table, barrier)


def _apply_read_barrier(table_name: str) -> None:
    from app.core.db_queries import apply_read_barrier

    apply_read_barrier(table_name)


def fetch_one(table: str, record_id: str) -> dict[str, Any] | None:
    from app.core.db_queries import fetch_one as _fetch_one

    return _fetch_one(table, record_id)


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    if table not in _ENSURE_COLUMNS_TABLES:
        return
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            # P1-6 fix: Validate column name before using it in ALTER TABLE.
            # SQLite does not support parameterized identifiers in DDL, so we
            # validate against a strict regex to prevent SQL injection.
            if not _SAFE_COLUMN_NAME_RE.fullmatch(name):
                raise ValueError(f"Invalid column name for ALTER TABLE: {name!r}")
            if not _SAFE_COLUMN_DEFINITION_RE.fullmatch(definition):
                raise ValueError(f"Invalid column definition for ALTER TABLE: {definition!r}")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def fetch_many_by_fields(
    table: str,
    filters: dict[str, Any] | None = None,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    from app.core.db_queries import fetch_many_by_fields as _fetch_many_by_fields

    return _fetch_many_by_fields(table, filters, limit=limit)


def fetch_many_in(
    table: str, column: str, values: list[Any] | tuple[Any, ...], *, limit: int = 200
) -> list[dict[str, Any]]:
    from app.core.db_queries import fetch_many_in as _fetch_many_in

    return _fetch_many_in(table, column, values, limit=limit)


def fetch_many(table: str, where: str = "", args: tuple[Any, ...] = (), limit: int = 200) -> list[dict[str, Any]]:
    """Compatibility fetch with a narrow WHERE grammar.

    Prefer ``fetch_many_by_fields`` and ``fetch_many_in`` for new code so SQL
    fragments do not spread beyond this module.
    """
    from app.core.db_queries import fetch_many as _fetch_many

    return _fetch_many(table, where, args, limit)


def _fetch_many_data(
    table_name: str, where_clause: str = "", args: tuple[Any, ...] = (), limit: int = 200
) -> list[dict[str, Any]]:
    from app.core.db_queries import fetch_many_data

    return fetch_many_data(table_name, where_clause, args, limit)


def _data_table_name(table: str) -> str:
    from app.core.db_queries import data_table_name

    return data_table_name(table)


def _where_clause(table_name: str, where: str, args: tuple[Any, ...]) -> str:
    from app.core.db_queries import where_clause

    return where_clause(table_name, where, args)


def _validate_where_conditions(table_name: str, clause: str) -> None:
    from app.core.db_queries import validate_where_conditions

    validate_where_conditions(table_name, clause)


def _validate_where_condition_part(table_name: str, allowed_columns: frozenset[str], part: str) -> None:
    from app.core.db_queries import validate_where_condition_part

    validate_where_condition_part(table_name, allowed_columns, part)


def _where_column(table_name: str, column: str, *, allowed_columns: frozenset[str] | None = None) -> str:
    from app.core.db_queries import where_column

    return where_column(table_name, column, allowed_columns=allowed_columns)


def _query_limit(limit: int) -> int:
    from app.core.db_queries import query_limit

    return query_limit(limit)


def claim_scheduled_task_run(
    schedule_id: str,
    *,
    expected_next_run_at: str,
    claimed_data: dict[str, Any],
) -> dict[str, Any] | None:
    from app.core.db_scheduling import claim_scheduled_task_run as _claim_scheduled_task_run

    return _claim_scheduled_task_run(
        schedule_id,
        expected_next_run_at=expected_next_run_at,
        claimed_data=claimed_data,
    )


def complete_scheduled_task_run(
    schedule_id: str,
    *,
    expected_last_run_at: str,
    expected_next_run_at: str,
    last_status: str,
    last_task_id: str = "",
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    from app.core.db_scheduling import complete_scheduled_task_run as _complete_scheduled_task_run

    return _complete_scheduled_task_run(
        schedule_id,
        expected_last_run_at=expected_last_run_at,
        expected_next_run_at=expected_next_run_at,
        last_status=last_status,
        last_task_id=last_task_id,
        updated_at=updated_at,
    )


def set_scheduled_task_enabled(
    schedule_id: str,
    enabled: bool,
    *,
    next_run_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any] | None:
    from app.core.db_scheduling import set_scheduled_task_enabled as _set_scheduled_task_enabled

    return _set_scheduled_task_enabled(schedule_id, enabled, next_run_at=next_run_at, updated_at=updated_at)


def insert_perception_observation(payload: dict[str, Any]) -> None:
    from app.core.db_scheduling import insert_perception_observation as _insert_perception_observation

    _insert_perception_observation(payload)


def insert_perception_suggestion(payload: dict[str, Any]) -> None:
    from app.core.db_scheduling import insert_perception_suggestion as _insert_perception_suggestion

    _insert_perception_suggestion(payload)


def next_run_event_sequence(run_id: str) -> int:
    from app.core.db_run_events import next_run_event_sequence as _next_run_event_sequence

    return _next_run_event_sequence(run_id)


def insert_run_event(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    from app.core.db_run_events import insert_run_event as _insert_run_event

    return _insert_run_event(model)


def _insert_run_event_record(data: dict[str, Any]) -> dict[str, Any]:
    from app.core.db_run_events import insert_run_event_record

    return insert_run_event_record(data)


def _insert_run_event_locked(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    from app.core.db_run_events import insert_run_event_locked

    return insert_run_event_locked(conn, data)


def insert_audit_event(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    from app.core.db_audit_chain import insert_audit_event as _insert_audit_event

    return _insert_audit_event(model)


def _insert_audit_event_record(data: dict[str, Any]) -> dict[str, Any]:
    from app.core.db_audit_chain import insert_audit_event_record

    return insert_audit_event_record(data)


def verify_audit_log(*, limit: int | None = None) -> dict[str, Any]:
    from app.core.db_audit_chain import verify_audit_log as _verify_audit_log

    return _verify_audit_log(limit=limit)


def _audit_column_mismatch(row: sqlite3.Row, data: dict[str, Any]) -> list[str]:
    from app.core.db_audit_chain import audit_column_mismatch

    return audit_column_mismatch(row, data)


def _missing_audit_append_only_triggers(conn: sqlite3.Connection) -> list[str]:
    from app.core.db_audit_chain import missing_audit_append_only_triggers

    return missing_audit_append_only_triggers(conn)


def erase_local_user_data(*, include_settings: bool = False) -> dict[str, int]:
    from app.core.db_maintenance import erase_local_user_data as _erase_local_user_data

    return _erase_local_user_data(include_settings=include_settings)


def local_product_diagnostics(*, recent_limit: int = 200) -> dict[str, Any]:
    from app.core.db_maintenance import local_product_diagnostics as _local_product_diagnostics

    return _local_product_diagnostics(recent_limit=recent_limit)


def _prepare_audit_event_locked(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    from app.core.db_audit_chain import prepare_audit_event_locked

    return prepare_audit_event_locked(conn, data)


def _storage_safe_audit_event(data: dict[str, Any]) -> dict[str, Any]:
    from app.core.db_audit_chain import storage_safe_audit_event

    return storage_safe_audit_event(data)


def _restore_remote_input_approval_binding_ids(stored: dict[str, Any], original_payload: dict[str, Any]) -> None:
    from app.core.db_audit_chain import restore_remote_input_approval_binding_ids

    restore_remote_input_approval_binding_ids(stored, original_payload)


def _store_audit_chain_head(sequence: int, event_hash: str, *, event_id: str = "") -> None:
    from app.core.db_audit_chain import store_audit_chain_head

    store_audit_chain_head(sequence, event_hash, event_id=event_id)


def _invalidate_audit_chain_head() -> None:
    from app.core.db_audit_chain import invalidate_audit_chain_head

    invalidate_audit_chain_head()


def _latest_persisted_audit_chain_head(conn: sqlite3.Connection) -> dict[str, Any] | None:
    from app.core.db_audit_chain import latest_persisted_audit_chain_head

    return latest_persisted_audit_chain_head(conn)


def _audit_chain_head_integrity_payload(
    *,
    record_id: str,
    sequence: int,
    event_hash: str,
    event_id: str,
    created_at: str,
) -> str:
    from app.core.db_audit_chain import audit_chain_head_integrity_payload

    return audit_chain_head_integrity_payload(
        record_id=record_id,
        sequence=sequence,
        event_hash=event_hash,
        event_id=event_id,
        created_at=created_at,
    )


def audit_anchor_path() -> Path:
    from app.core.db_audit_chain import audit_anchor_path as _audit_anchor_path

    return _audit_anchor_path()


def _write_audit_anchor(sequence: int, event_hash: str, *, event_id: str = "") -> None:
    from app.core.db_audit_chain import write_audit_anchor

    write_audit_anchor(sequence, event_hash, event_id=event_id)


def _read_audit_anchor() -> dict[str, Any] | None:
    from app.core.db_audit_chain import read_audit_anchor

    return read_audit_anchor()


def _audit_event_hash(event: dict[str, Any]) -> str:
    from app.core.db_audit_chain import audit_event_hash

    return audit_event_hash(event)


def _audit_event_hmac(event_hash: str, *, secret: str | None = None) -> str:
    from app.core.db_audit_chain import audit_event_hmac

    return audit_event_hmac(event_hash, secret=secret)


def _audit_hmac_secret() -> str:
    from app.core.db_audit_chain import audit_hmac_secret

    return audit_hmac_secret()


def _active_audit_hmac_secret_path() -> Path:
    from app.core.db_audit_chain import active_audit_hmac_secret_path

    return active_audit_hmac_secret_path()


def fetch_run_events(run_id: str, *, after_sequence: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
    from app.core.db_run_events import fetch_run_events as _fetch_run_events

    return _fetch_run_events(run_id, after_sequence=after_sequence, limit=limit)


def delete_run_events_before(cutoff_iso: str) -> int:
    from app.core.db_run_events import delete_run_events_before as _delete_run_events_before

    return _delete_run_events_before(cutoff_iso)


def claim_approval_for_execution(approval_id: str, consumed_at: str) -> dict[str, Any] | None:
    from app.core.db_approvals import claim_approval_for_execution as _claim_approval_for_execution

    return _claim_approval_for_execution(approval_id, consumed_at)


def expire_stale_approvals(expired_at: str) -> list[dict[str, Any]]:
    from app.core.db_approvals import expire_stale_approvals as _expire_stale_approvals

    return _expire_stale_approvals(expired_at)


def expire_approval_if_pending(approval_id: str, expired_at: str, reason: str = "") -> dict[str, Any] | None:
    from app.core.db_approvals import expire_approval_if_pending as _expire_approval_if_pending

    return _expire_approval_if_pending(approval_id, expired_at, reason)


def expire_approval_if_unconsumed(
    approval_id: str,
    expired_at: str,
    reason: str = "",
    *,
    statuses: set[str] | None = None,
) -> dict[str, Any] | None:
    from app.core.db_approvals import expire_approval_if_unconsumed as _expire_approval_if_unconsumed

    return _expire_approval_if_unconsumed(approval_id, expired_at, reason, statuses=statuses)


def expire_pending_approvals_for_task(task_id: str, expired_at: str, reason: str = "") -> list[dict[str, Any]]:
    from app.core.db_approvals import expire_pending_approvals_for_task as _expire_pending_approvals_for_task

    return _expire_pending_approvals_for_task(task_id, expired_at, reason)


def count_pending_remote_input_approvals(grant_id: str, device_id: str) -> int:
    from app.core.db_approvals import count_pending_remote_input_approvals as _count_pending_remote_input_approvals

    return _count_pending_remote_input_approvals(grant_id, device_id)


def decide_approval_atomically(
    approval_id: str,
    status: str,
    decided_at: str,
    *,
    authorized_at: str | None = None,
    auth_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from app.core.db_approvals import decide_approval_atomically as _decide_approval_atomically

    return _decide_approval_atomically(
        approval_id,
        status,
        decided_at,
        authorized_at=authorized_at,
        auth_context=auth_context,
    )


def reauthorize_approval_atomically(
    approval_id: str,
    authorized_at: str,
    auth_context: dict[str, Any],
) -> dict[str, Any] | None:
    from app.core.db_approvals import reauthorize_approval_atomically as _reauthorize_approval_atomically

    return _reauthorize_approval_atomically(approval_id, authorized_at, auth_context)


def set_setting(key: str, value: Any) -> None:
    from app.core.db_settings import set_setting as _set_setting

    _set_setting(key, value)


def get_settings_overrides() -> dict[str, Any]:
    from app.core.db_settings import get_settings_overrides as _get_settings_overrides

    return _get_settings_overrides()


def store_sensitive_record_integrity(
    table: str,
    record_id: str,
    data: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    from app.core.db_sensitive_integrity import store_sensitive_record_integrity as _store_public

    _store_public(table, record_id, data, conn=conn)


def require_sensitive_record_integrity(table: str, record_id: str, data: str) -> None:
    from app.core.db_sensitive_integrity import require_sensitive_record_integrity as _require_public

    _require_public(table, record_id, data)


def sensitive_integrity_check() -> dict[str, Any]:
    from app.core.db_sensitive_integrity import sensitive_integrity_check as _sensitive_integrity_check

    return _sensitive_integrity_check()


def bootstrap_sensitive_record_integrity() -> dict[str, Any]:
    from app.core.db_sensitive_integrity import bootstrap_sensitive_record_integrity as _bootstrap

    return _bootstrap()


def set_startup_sensitive_integrity_status(status: dict[str, Any]) -> None:
    from app.core.db_sensitive_integrity import set_startup_sensitive_integrity_status as _set_status

    _set_status(status)


def get_startup_sensitive_integrity_status() -> dict[str, Any]:
    from app.core.db_sensitive_integrity import get_startup_sensitive_integrity_status as _get_status

    return _get_status()


def require_sensitive_integrity_ok() -> None:
    from app.core.db_sensitive_integrity import require_sensitive_integrity_ok as _require_ok

    _require_ok()


def audit_fail_closed_enabled() -> bool:
    from app.core.db_sensitive_integrity import audit_fail_closed_enabled as _enabled

    return _enabled()


def audit_fail_closed_status() -> dict[str, Any]:
    from app.core.db_sensitive_integrity import audit_fail_closed_status as _status

    return _status()


def require_audit_fail_closed_ok() -> None:
    from app.core.db_sensitive_integrity import require_audit_fail_closed_ok as _require_ok

    _require_ok()


def _iter_sensitive_record_rows(conn: sqlite3.Connection) -> Iterator[tuple[str, sqlite3.Row, str]]:
    from app.core.db_sensitive_integrity import iter_sensitive_record_rows

    yield from iter_sensitive_record_rows(conn)


def _begin_immediate_transaction(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _ensure_sensitive_record_integrity_schema(conn: sqlite3.Connection) -> None:
    from app.core.db_sensitive_integrity import ensure_sensitive_record_integrity_schema

    ensure_sensitive_record_integrity_schema(conn)


def _store_sensitive_record_integrity(conn: sqlite3.Connection, table: str, record_id: str, data: str) -> None:
    from app.core.db_sensitive_integrity import store_sensitive_record_integrity_locked

    store_sensitive_record_integrity_locked(conn, table, record_id, data)


def _sensitive_record_integrity_row_exists(conn: sqlite3.Connection, table: str, record_id: str) -> bool:
    from app.core.db_sensitive_integrity import sensitive_record_integrity_row_exists

    return sensitive_record_integrity_row_exists(conn, table, record_id)


def _sensitive_integrity_bootstrap_payload() -> str:
    from app.core.db_sensitive_integrity import sensitive_integrity_bootstrap_payload

    return sensitive_integrity_bootstrap_payload()


def _sensitive_integrity_bootstrap_digest() -> str:
    from app.core.db_sensitive_integrity import sensitive_integrity_bootstrap_digest

    return sensitive_integrity_bootstrap_digest()


def _sensitive_integrity_bootstrap_completed(conn: sqlite3.Connection) -> bool:
    from app.core.db_sensitive_integrity import sensitive_integrity_bootstrap_completed

    return sensitive_integrity_bootstrap_completed(conn)


def _mark_sensitive_integrity_bootstrap_completed(conn: sqlite3.Connection) -> None:
    from app.core.db_sensitive_integrity import mark_sensitive_integrity_bootstrap_completed

    mark_sensitive_integrity_bootstrap_completed(conn)


def _require_sensitive_record_integrity(conn: sqlite3.Connection, table: str, record_id: str, data: str) -> None:
    from app.core.db_sensitive_integrity import require_sensitive_record_integrity_locked

    require_sensitive_record_integrity_locked(conn, table, record_id, data)


def _sensitive_record_digest(table: str, record_id: str, data: str) -> str:
    from app.core.db_sensitive_integrity import sensitive_record_digest

    return sensitive_record_digest(table, record_id, data)


def _lazy_memory_api(name: str) -> Callable[..., Any]:
    def invoke(*args: Any, **kwargs: Any) -> Any:
        from app.core import db_memory

        return getattr(db_memory, name)(*args, **kwargs)

    invoke.__name__ = name
    return invoke


delete_memory = _lazy_memory_api("delete_memory")
get_memory = _lazy_memory_api("get_memory")
list_memories = _lazy_memory_api("list_memories")
upsert_memory = _lazy_memory_api("upsert_memory")
