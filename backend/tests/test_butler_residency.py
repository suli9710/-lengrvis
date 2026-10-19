from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import butler_store as store
from app.core import db
from app.core.butler_models import AutonomyScope, ButlerMatter, MatterStatus, WaitCondition
from app.services.butler_residency import residency_required
from app.services.butler_service import ButlerCoordinator
from app.services.guardian_runtime import GuardianRuntime


def test_active_scope_and_waits_keep_the_butler_resident_but_global_pause_does_not(tmp_path):
    assert not residency_required()
    scope = store.save(AutonomyScope(label="文件", directories=[str(tmp_path)]))
    store.set_control(enabled=True)
    assert residency_required()
    store.set_control(paused=True)
    assert not residency_required()
    store.set_control(paused=False, enabled=False)
    store.mutate(AutonomyScope, scope.id, lambda value: setattr(value, "enabled", False))
    store.save(
        ButlerMatter(
            goal="等待资料",
            status=MatterStatus.WAITING_EVENT,
            waiting=WaitCondition(kind="file_exists", subject=str(tmp_path / "later.txt")),
        )
    )
    assert residency_required()


@pytest.mark.asyncio
async def test_tray_background_does_not_stop_a_resident_butler(monkeypatch):
    guardian = GuardianRuntime()
    stopped = []

    async def nothing(*args, **kwargs):
        return {}

    async def stop(*args, **kwargs):
        stopped.append(True)

    monkeypatch.setattr(guardian, "_notify_full_background", nothing)
    monkeypatch.setattr(guardian, "status", nothing)
    monkeypatch.setattr(guardian, "stop_full_backend", stop)
    monkeypatch.setattr(guardian, "_butler_resident", lambda: True)
    await guardian.enter_background()
    assert stopped == []
    monkeypatch.setattr(guardian, "_butler_resident", lambda: False)
    await guardian.enter_background()
    assert stopped == [True]


@pytest.mark.asyncio
async def test_privacy_generation_change_stops_old_directory_watchers(monkeypatch, tmp_path):
    db.init_db()
    coordinator = ButlerCoordinator()
    stopped = []
    coordinator._watcher = SimpleNamespace(stop=lambda: stopped.append(True))
    coordinator._watch_roots = (str(tmp_path),)
    db.erase_local_user_data()
    await coordinator.tick()
    assert stopped == [True]
    await coordinator.stop()
