from __future__ import annotations

import pytest

from app.core import db
from app.llm import registry


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    registry.invalidate_settings_cache()
    db.init_db()


def test_effective_settings_cached_within_ttl() -> None:
    first = registry.get_effective_settings()
    second = registry.get_effective_settings()
    assert second is first


def test_set_setting_invalidates_cache() -> None:
    before = registry.get_effective_settings()
    db.set_setting("temperature", 0.42)
    after = registry.get_effective_settings()
    assert after is not before
    assert after.temperature == 0.42


def test_env_change_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    registry.get_effective_settings()
    monkeypatch.setenv("LENGRVIS_MODE", "privacy")
    refreshed = registry.get_effective_settings()
    assert refreshed.mode == "privacy"


def test_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    first = registry.get_effective_settings()
    real_monotonic = registry.time.monotonic
    monkeypatch.setattr(registry.time, "monotonic", lambda: real_monotonic() + 10.0)
    second = registry.get_effective_settings()
    assert second is not first


def test_erase_settings_invalidates_cache(tmp_path) -> None:
    db.set_setting("temperature", 0.33)
    cached = registry.get_effective_settings()
    db.erase_local_user_data(include_settings=True)
    refreshed = registry.get_effective_settings()
    assert refreshed is not cached
    assert refreshed.temperature != 0.33
