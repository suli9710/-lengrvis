from __future__ import annotations

from pathlib import Path

import pytest

from app.config import env_aliases, env_value
from app.policy.approval_binding import APPROVAL_HMAC_ENV_KEYS, approval_secret
from app.security.lan import allow_lan_desktop_api


def test_env_example_uses_only_lengrvis_assignment_prefix(project_root: Path) -> None:
    text = (project_root / ".env.example").read_text(encoding="utf-8")
    assignment_keys = [
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    ]

    assert assignment_keys
    assert all(key.startswith("LENGRVIS_") for key in assignment_keys)
    assert not any(key.startswith(("MARVIS_", "MAVRIS_")) for key in assignment_keys)
    assert "MARVIS_" not in text
    assert "MAVRIS_" not in text
    assert {
        "LENGRVIS_PROVIDER_NAME",
        "LENGRVIS_API_KEY",
        "LENGRVIS_ALLOW_MOCK_FALLBACK",
        "LENGRVIS_JWT_SECRET",
        "LENGRVIS_BACKEND_URL",
    }.issubset(set(assignment_keys))


def test_config_env_lookup_does_not_alias_legacy_prefixes() -> None:
    assert env_aliases("LENGRVIS_API_KEY") == ("LENGRVIS_API_KEY",)
    assert env_value({"LENGRVIS_API_KEY": "new-secret"}, "LENGRVIS_API_KEY") == "new-secret"
    assert env_value({"MARVIS_API_KEY": "legacy-secret"}, "LENGRVIS_API_KEY") is None
    assert env_value({"MAVRIS_API_KEY": "legacy-secret"}, "LENGRVIS_API_KEY") is None


@pytest.mark.parametrize("legacy_key", ["MARVIS_ALLOW_LAN_DESKTOP_API", "MAVRIS_ALLOW_LAN_DESKTOP_API"])
def test_lan_desktop_api_uses_only_lengrvis_env_key(monkeypatch: pytest.MonkeyPatch, legacy_key: str) -> None:
    monkeypatch.delenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", raising=False)
    monkeypatch.setenv(legacy_key, "1")

    assert allow_lan_desktop_api() is False

    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "yes")

    assert allow_lan_desktop_api() is True


def test_approval_hmac_secret_uses_only_lengrvis_env_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_APPROVAL_HMAC_SECRET", raising=False)
    monkeypatch.setenv("MARVIS_APPROVAL_HMAC_SECRET", "legacy-secret")
    monkeypatch.setenv("MAVRIS_APPROVAL_HMAC_SECRET", "legacy-secret")

    assert APPROVAL_HMAC_ENV_KEYS == ("LENGRVIS_APPROVAL_HMAC_SECRET",)

    generated = approval_secret()

    assert generated != "legacy-secret"
    assert (tmp_path / "approval_hmac.secret").exists()

    monkeypatch.setenv("LENGRVIS_APPROVAL_HMAC_SECRET", "new-secret")

    assert approval_secret() == "new-secret"


def test_approval_hmac_secret_can_come_from_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LENGRVIS_APPROVAL_HMAC_SECRET=dotenv-secret\n", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(env_file))
    monkeypatch.delenv("LENGRVIS_APPROVAL_HMAC_SECRET", raising=False)

    assert approval_secret() == "dotenv-secret"
    assert not (tmp_path / "data" / "approval_hmac.secret").exists()
