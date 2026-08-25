"""Versioned, fail-closed persistence for orchestration RunState checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.orchestration.execution_models import (
    CURRENT_RUN_STATE_SCHEMA_VERSION,
    MIN_SUPPORTED_RUN_STATE_SCHEMA_VERSION,
    RunState,
)


class RunStateCheckpointError(ValueError):
    """Raised when a persisted RunState checkpoint cannot be safely resumed."""


def parse_run_state_checkpoint(raw: Mapping[str, Any] | None) -> RunState:
    """Migrate and validate a persisted checkpoint at the storage seam."""

    return RunState.model_validate(migrate_run_state_payload(public_run_state_payload(raw)))


def public_run_state_payload(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Strip private runtime metadata before RunState validation."""

    return {key: value for key, value in (raw or {}).items() if not str(key).startswith("_")}


def state_payload_with_runtime(raw: Mapping[str, Any] | None, state: RunState) -> dict[str, Any]:
    """Serialize a current checkpoint while preserving private runtime metadata."""

    payload = state.model_dump(mode="json")
    runtime = (raw or {}).get("_runtime")
    if isinstance(runtime, dict) and runtime:
        payload["_runtime"] = dict(runtime)
    return payload


def migrate_run_state_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a current-version checkpoint or fail closed.

    Unversioned rows are the pre-checkpoint legacy format and are treated as
    schema v1. Explicit v1/v2 rows receive their named migrations. A future or
    unsupported historical version is never guessed at because that could
    silently change execution semantics.
    """

    migrated = dict(payload)
    if "schema_version" not in migrated:
        migrated = _migrate_legacy_run_state_to_v1(migrated)
        version = MIN_SUPPORTED_RUN_STATE_SCHEMA_VERSION
    else:
        raw_version = migrated.get("schema_version")
        if isinstance(raw_version, bool) or not isinstance(raw_version, int):
            raise RunStateCheckpointError(f"invalid RunState schema_version {raw_version!r}; refusing to resume")
        version = raw_version

    if version > CURRENT_RUN_STATE_SCHEMA_VERSION:
        raise RunStateCheckpointError(
            f"unsupported future RunState schema_version {version}; current={CURRENT_RUN_STATE_SCHEMA_VERSION}"
        )
    if version < MIN_SUPPORTED_RUN_STATE_SCHEMA_VERSION:
        raise RunStateCheckpointError(
            f"unsupported historical RunState schema_version {version}; "
            f"minimum_supported={MIN_SUPPORTED_RUN_STATE_SCHEMA_VERSION}"
        )

    migrations = {
        1: _migrate_run_state_v1_to_v2,
        2: _migrate_run_state_v2_to_v3,
    }
    while version < CURRENT_RUN_STATE_SCHEMA_VERSION:
        migration = migrations.get(version)
        if migration is None:  # pragma: no cover - guarded by the range checks above.
            raise RunStateCheckpointError(f"no migration for RunState schema_version {version}")
        migrated = migration(migrated)
        version = int(migrated["schema_version"])
    return migrated


def _migrate_legacy_run_state_to_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    migrated["schema_version"] = MIN_SUPPORTED_RUN_STATE_SCHEMA_VERSION
    return migrated


def _migrate_run_state_v1_to_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    migrated.setdefault("route_rule", "ambiguous_fallback")
    migrated["schema_version"] = 2
    return migrated


def _migrate_run_state_v2_to_v3(payload: Mapping[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    migrated.setdefault("continuation_kind", "")
    migrated["schema_version"] = CURRENT_RUN_STATE_SCHEMA_VERSION
    return migrated
