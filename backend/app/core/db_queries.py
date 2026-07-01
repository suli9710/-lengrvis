from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from app.core import db

# Read barriers: tables whose writes are deferred to a background writer
# register a flush callable here so readers keep read-your-writes semantics.
_READ_BARRIERS: dict[str, Callable[[], None]] = {}


def register_read_barrier(table: str, barrier: Callable[[], None]) -> None:
    _READ_BARRIERS[data_table_name(table)] = barrier


def apply_read_barrier(table_name: str) -> None:
    barrier = _READ_BARRIERS.get(table_name)
    if barrier is not None:
        barrier()


def fetch_one(table: str, record_id: str) -> dict[str, Any] | None:
    table_name = data_table_name(table)
    apply_read_barrier(table_name)
    with db.connect() as conn:
        row = conn.execute(f"SELECT data FROM {table_name} WHERE id = ?", (record_id,)).fetchone()  # noqa: S608
        if row and table_name in db.SENSITIVE_RECORD_INTEGRITY_KINDS:
            db._require_sensitive_record_integrity(conn, table_name, record_id, row["data"])
    return json.loads(row["data"]) if row else None


def fetch_many_by_fields(
    table: str,
    filters: dict[str, Any] | None = None,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    table_name = data_table_name(table)
    clauses: list[str] = []
    args: list[Any] = []
    for column, value in dict(filters or {}).items():
        column_name = where_column(table_name, column)
        if value is None:
            clauses.append(f"{column_name} IS NULL")
        else:
            clauses.append(f"{column_name} = ?")
            args.append(value)
    return fetch_many_data(table_name, " AND ".join(clauses), tuple(args), limit)


def fetch_many_in(
    table: str, column: str, values: list[Any] | tuple[Any, ...], *, limit: int = 200
) -> list[dict[str, Any]]:
    table_name = data_table_name(table)
    column_name = where_column(table_name, column)
    args = tuple(values)
    if not args:
        return []
    placeholders = ", ".join("?" for _ in args)
    return fetch_many_data(table_name, f"{column_name} IN ({placeholders})", args, limit)


def fetch_many(table: str, where: str = "", args: tuple[Any, ...] = (), limit: int = 200) -> list[dict[str, Any]]:
    table_name = data_table_name(table)
    return fetch_many_data(table_name, where_clause(table_name, where, args), args, limit)


def fetch_many_data(
    table_name: str, where_clause_value: str = "", args: tuple[Any, ...] = (), limit: int = 200
) -> list[dict[str, Any]]:
    apply_read_barrier(table_name)
    query = f"SELECT data FROM {table_name}"  # noqa: S608
    if where_clause_value:
        query += f" WHERE {where_clause_value}"
    query += " ORDER BY created_at DESC LIMIT ?"
    with db.connect() as conn:
        rows = conn.execute(query, (*args, query_limit(limit))).fetchall()
        if table_name in db.SENSITIVE_RECORD_INTEGRITY_KINDS:
            for row in rows:
                data = json.loads(row["data"])
                record_id = str(data.get("id") or "")
                if record_id:
                    db._require_sensitive_record_integrity(conn, table_name, record_id, row["data"])
    return [json.loads(row["data"]) for row in rows]


def data_table_name(table: str) -> str:
    table_name = str(table or "").strip()
    if table_name not in db.DATA_TABLES:
        raise ValueError(f"Unsupported table: {table}")
    return table_name


def where_clause(table_name: str, where: str, args: tuple[Any, ...]) -> str:
    clause = str(where or "").strip()
    if not clause:
        if args:
            raise ValueError("WHERE arguments require a WHERE clause")
        return ""
    if any(token in clause for token in db.UNSAFE_WHERE_TOKENS):
        raise ValueError("Unsafe WHERE clause")
    if db.WHERE_OR_RE.search(clause):
        raise ValueError("Unsupported WHERE clause")
    if clause.count("?") != len(args):
        raise ValueError("WHERE placeholder count does not match arguments")
    validate_where_conditions(table_name, clause)
    return clause


def validate_where_conditions(table_name: str, clause: str) -> None:
    allowed_columns = db.WHERE_ALLOWED_COLUMNS.get(table_name, frozenset())
    if not allowed_columns:
        raise ValueError(f"WHERE clauses are not supported for table: {table_name}")
    if not re.fullmatch(r"[A-Za-z0-9_?\s().,=<>!]+", clause):
        raise ValueError("Unsafe WHERE clause")

    parts = [part.strip() for part in db.WHERE_CONDITION_JOINER_RE.split(clause) if part.strip()]
    if not parts:
        raise ValueError("Unsafe WHERE clause")
    for part in parts:
        validate_where_condition_part(table_name, allowed_columns, part)


def validate_where_condition_part(table_name: str, allowed_columns: frozenset[str], part: str) -> None:
    match = db.WHERE_COMPARISON_RE.fullmatch(part) or db.WHERE_IN_RE.fullmatch(part) or db.WHERE_NULL_RE.fullmatch(part)
    if not match:
        raise ValueError("Unsupported WHERE clause")
    where_column(table_name, match.group(1), allowed_columns=allowed_columns)


def where_column(table_name: str, column: str, *, allowed_columns: frozenset[str] | None = None) -> str:
    column_name = str(column or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column_name):
        raise ValueError(f"Unsupported WHERE column for {table_name}: {column}")
    allowed = allowed_columns if allowed_columns is not None else db.WHERE_ALLOWED_COLUMNS.get(table_name, frozenset())
    if column_name not in allowed:
        raise ValueError(f"Unsupported WHERE column for {table_name}: {column_name}")
    return column_name


def query_limit(limit: int) -> int:
    value = int(limit)
    if value < 1:
        raise ValueError("Limit must be positive")
    return value
