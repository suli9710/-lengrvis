"""Versioned, integrity-checked persistence for the butler coordinator."""
# Table identifiers come only from the closed model-to-table mapping below.
# ruff: noqa: S608

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TypeVar

from app.core import db
from app.core.butler_models import AutonomyScope, ButlerMatter, MatterStatus
from app.core.local_data_lifecycle import tracks_local_data
from app.core.schemas import now_iso

T = TypeVar("T", AutonomyScope, ButlerMatter)
TABLES = {AutonomyScope: "autonomy_scopes", ButlerMatter: "butler_matters"}


class ButlerConflict(ValueError):
    pass


def get(model: type[T], record_id: str) -> T | None:
    db.init_db()
    table = TABLES[model]
    with db.connect() as conn:
        row = conn.execute(f"SELECT data FROM {table} WHERE id = ?", (record_id,)).fetchone()  # noqa: S608
        if row is None:
            return None
        db._require_sensitive_record_integrity(conn, table, record_id, row["data"])
        return model.model_validate_json(row["data"])


def list_records(model: type[T], *, limit: int | None = 200) -> list[T]:
    db.init_db()
    table = TABLES[model]
    with db.connect() as conn:
        suffix, arguments = (" LIMIT ?", (min(1000, max(1, limit)),)) if limit is not None else ("", ())
        rows = conn.execute(f"SELECT id, data FROM {table} ORDER BY updated_at DESC" + suffix, arguments).fetchall()
        result = []
        for row in rows:
            db._require_sensitive_record_integrity(conn, table, row["id"], row["data"])
            result.append(model.model_validate_json(row["data"]))
        return result


@tracks_local_data
def save(record: T, *, expected_version: int | None = None) -> T:
    db.init_db()
    table = TABLES[type(record)]
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        old = conn.execute(f"SELECT data FROM {table} WHERE id = ?", (record.id,)).fetchone()  # noqa: S608
        previous = None
        if old:
            db._require_sensitive_record_integrity(conn, table, record.id, old["data"])
            previous = type(record).model_validate_json(old["data"])
            if expected_version is None or previous.version != expected_version:
                raise ButlerConflict("事项或授权已更新，请读取最新状态后重试。")
            record = record.model_copy(update={"version": previous.version + 1, "updated_at": now_iso()})
        elif expected_version is not None:
            raise ButlerConflict("事项或授权已不存在。")
        # Mutators operate on existing models; assignment itself is not a
        # Pydantic validation boundary. Never persist a record that cannot be
        # loaded on the next tick (for example an overlong revised goal).
        record = type(record).model_validate(record.model_dump())
        if isinstance(record, ButlerMatter):
            _validate_dependencies(conn, record, previous)
        data = record.model_dump_json()
        conn.execute(  # noqa: S608
            f"INSERT INTO {table}(id, data, created_at, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (record.id, data, record.created_at, record.updated_at),
        )
        db._store_sensitive_record_integrity(conn, table, record.id, data)
    return record


def execution_started(matter: ButlerMatter) -> bool:
    return bool(matter.task_ids or matter.run_ids or matter.active_run_id or matter.active_task_id)


def _validate_dependencies(conn, record: ButlerMatter, previous: ButlerMatter | None) -> None:
    changed = record.depends_on != (previous.depends_on if previous else [])
    starting = (record.status == MatterStatus.PLANNING and (not previous or previous.status != record.status)) or bool(
        previous and not execution_started(previous) and execution_started(record)
    )
    first_execution = not execution_started(previous or record)
    if not changed and not (starting and record.depends_on and first_execution):
        return
    if (
        changed
        and previous
        and (
            execution_started(previous)
            or previous.status in {MatterStatus.PLANNING, MatterStatus.RUNNING, MatterStatus.WAITING_APPROVAL}
        )
    ):
        raise ButlerConflict("事项已开始执行，不能再调整前置关系。")
    graph = {}
    for row in conn.execute("SELECT id,data FROM butler_matters"):
        db._require_sensitive_record_integrity(conn, "butler_matters", row["id"], row["data"])
        graph[row["id"]] = ButlerMatter.model_validate_json(row["data"])
    graph[record.id] = record
    record.depends_on = list(dict.fromkeys(record.depends_on))
    visited = set()
    active = set()

    stack = [(record.id, False)]
    while stack:
        identity, leaving = stack.pop()
        if leaving:
            active.remove(identity)
            visited.add(identity)
            continue
        if identity in active:
            raise ValueError("前置事项不能形成循环或引用自身。")
        if identity in visited:
            continue
        if identity not in graph:
            raise ValueError("前置事项不存在，请刷新后重新选择。")
        active.add(identity)
        stack.append((identity, True))
        stack.extend((parent, False) for parent in graph[identity].depends_on)
    if starting and first_execution:
        from app.services.butler_coordination import dependency_blocks

        if dependency_blocks(record, graph):
            raise ButlerConflict("前置事项尚未完成核验，暂不能启动。")
        record.dependency_revisions = {key: graph[key].outcome_revision for key in record.depends_on}


def mutate(model: type[T], record_id: str, change: Callable[[T], None], *, expected_version: int | None = None) -> T:
    for _ in range(3):
        current = get(model, record_id)
        if current is None:
            raise KeyError(record_id)
        if expected_version is not None and current.version != expected_version:
            raise ButlerConflict("此回答对应的事项版本已过期。")
        version = current.version
        change(current)
        try:
            return save(current, expected_version=version)
        except ButlerConflict:
            if expected_version is not None:
                raise
    raise ButlerConflict("事项正被其他操作更新，请重试。")


def control_state() -> dict:
    db.init_db()
    settings = db.get_settings_overrides()
    return {
        "paused": settings.get("butler_paused", False),
        "enabled": settings.get("butler_enabled", False),
        "coordination_enabled": settings.get("butler_coordination_enabled", False),
        "auto_memory_enabled": settings.get("butler_auto_memory_enabled", True),
    }


def set_control(
    *,
    paused: bool | None = None,
    enabled: bool | None = None,
    coordination_enabled: bool | None = None,
    auto_memory_enabled: bool | None = None,
) -> dict:
    db.init_db()
    if paused is not None:
        db.set_setting("butler_paused", paused)
    if enabled is not None:
        db.set_setting("butler_enabled", enabled)
    if coordination_enabled is not None:
        db.set_setting("butler_coordination_enabled", coordination_enabled)
    if auto_memory_enabled is not None:
        db.set_setting("butler_auto_memory_enabled", auto_memory_enabled)
    return control_state()


def source_key(*parts: object) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
