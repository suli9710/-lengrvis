from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class IndexShape:
    columns: tuple[str, ...]
    descending: tuple[bool, ...] = ()
    unique: bool = False
    partial: bool = False
    where_sql: str = ""


def require_table_columns(conn: sqlite3.Connection, requirements: dict[str, set[str]]) -> None:
    for table, required_columns in requirements.items():
        columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        missing = sorted(required_columns - columns)
        if missing:
            raise RuntimeError(f"schema migration left {table} without required columns: {', '.join(missing)}")


def require_primary_key_columns(conn: sqlite3.Connection, requirements: dict[str, tuple[str, ...]]) -> None:
    for table, required_columns in requirements.items():
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        columns = tuple(
            str(row[1]) for row in sorted((row for row in rows if int(row[5]) > 0), key=lambda row: int(row[5]))
        )
        if columns != required_columns:
            expected = ", ".join(required_columns)
            actual = ", ".join(columns) or "missing"
            raise RuntimeError(f"schema migration left {table} primary key as {actual}; expected {expected}")


def require_not_null_columns(conn: sqlite3.Connection, requirements: dict[str, set[str]]) -> None:
    for table, required_columns in requirements.items():
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        not_null = {str(row[1]) for row in rows if int(row[3]) == 1}
        missing = sorted(required_columns - not_null)
        if missing:
            raise RuntimeError(f"schema migration left {table} nullable critical columns: {', '.join(missing)}")


def require_index_columns(conn: sqlite3.Connection, requirements: dict[str, tuple[str, ...]]) -> None:
    for index, required_columns in requirements.items():
        rows = conn.execute(f'PRAGMA index_info("{index}")').fetchall()
        columns = tuple(str(row[2]) for row in sorted(rows, key=lambda row: int(row[0])))
        if columns != required_columns:
            expected = ", ".join(required_columns)
            actual = ", ".join(columns) or "missing"
            raise RuntimeError(f"schema migration left {index} with columns {actual}; expected {expected}")


def require_named_index_shapes(
    conn: sqlite3.Connection,
    requirements: dict[str, dict[str, IndexShape]],
) -> None:
    for table, table_requirements in requirements.items():
        listed = {str(row[1]): row for row in conn.execute(f'PRAGMA index_list("{table}")').fetchall()}
        for index, requirement in table_requirements.items():
            row = listed.get(index)
            if row is None:
                raise RuntimeError(f"schema migration left {table} without required index {index}")
            if bool(row[2]) != requirement.unique:
                expected = "unique" if requirement.unique else "non-unique"
                raise RuntimeError(f"schema migration left {index} with the wrong uniqueness; expected {expected}")
            if bool(row[4]) != requirement.partial:
                expected = "partial" if requirement.partial else "non-partial"
                raise RuntimeError(f"schema migration left {index} with the wrong predicate mode; expected {expected}")
            key_rows = [
                index_row
                for index_row in conn.execute(f'PRAGMA index_xinfo("{index}")').fetchall()
                if int(index_row[5]) == 1
            ]
            key_rows.sort(key=lambda index_row: int(index_row[0]))
            columns = tuple(str(index_row[2]) for index_row in key_rows)
            descending = tuple(bool(index_row[3]) for index_row in key_rows)
            expected_descending = requirement.descending or (False,) * len(requirement.columns)
            if columns != requirement.columns or descending != expected_descending:
                expected = _render_index_columns(requirement.columns, expected_descending)
                actual = _render_index_columns(columns, descending) if columns else "missing"
                raise RuntimeError(f"schema migration left {index} with key {actual}; expected {expected}")
            if requirement.where_sql:
                sql_row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                    (index,),
                ).fetchone()
                normalized_sql = _normalize_sql(sql_row[0] if sql_row is not None else "")
                if _normalize_sql(requirement.where_sql) not in normalized_sql:
                    raise RuntimeError(
                        f"schema migration left {index} without required predicate {requirement.where_sql}"
                    )


def require_unique_index_columns(
    conn: sqlite3.Connection,
    requirements: dict[str, set[tuple[str, ...]]],
) -> None:
    for table, required_indexes in requirements.items():
        available: set[tuple[str, ...]] = set()
        for row in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
            if not bool(row[2]) or bool(row[4]):
                continue
            index = str(row[1])
            columns = tuple(
                str(index_row[2])
                for index_row in sorted(
                    conn.execute(f'PRAGMA index_info("{index}")').fetchall(),
                    key=lambda index_row: int(index_row[0]),
                )
            )
            available.add(columns)
        for required_columns in required_indexes:
            if required_columns not in available:
                rendered = ", ".join(required_columns)
                raise RuntimeError(f"schema migration left {table} without UNIQUE index on ({rendered})")


def require_foreign_key_sets(
    conn: sqlite3.Connection,
    requirements: dict[str, set[tuple[str, str, str, str]]],
) -> None:
    for table, required_foreign_keys in requirements.items():
        actual = {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
            for row in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        }
        if actual != required_foreign_keys:
            expected = ", ".join(_render_foreign_key(item) for item in sorted(required_foreign_keys))
            found = ", ".join(_render_foreign_key(item) for item in sorted(actual)) or "none"
            raise RuntimeError(f"schema migration left {table} foreign keys as {found}; expected {expected}")


def require_foreign_key(
    conn: sqlite3.Connection,
    *,
    table: str,
    from_column: str,
    target_table: str,
    target_column: str,
    on_delete: str,
) -> None:
    rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    expected = (from_column, target_table, target_column, on_delete.upper())
    actual = {(str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper()) for row in rows}
    if expected not in actual:
        raise RuntimeError(
            f"schema migration left {table} without {from_column} -> {target_table}.{target_column} "
            f"ON DELETE {on_delete.upper()}"
        )


def _normalize_sql(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _render_index_columns(columns: tuple[str, ...], descending: tuple[bool, ...]) -> str:
    return ", ".join(
        f"{column} DESC" if is_descending else column for column, is_descending in zip(columns, descending, strict=True)
    )


def _render_foreign_key(value: tuple[str, str, str, str]) -> str:
    from_column, target_table, target_column, on_delete = value
    return f"{from_column}->{target_table}.{target_column} ON DELETE {on_delete}"
