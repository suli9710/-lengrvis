"""Tests for the Ollama lifecycle service."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import ollama_service


@pytest.fixture
def no_bundled_ollama(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("LENGRVIS_BUNDLED_OLLAMA_DIR", raising=False)
    monkeypatch.delenv("LENGRVIS_BUNDLED_OLLAMA_DIR", raising=False)
    monkeypatch.delenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", raising=False)
    monkeypatch.delenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", raising=False)
    monkeypatch.delenv("LENGRVIS_OLLAMA_BUNDLE_MANIFEST", raising=False)
    monkeypatch.delenv("LENGRVIS_OLLAMA_BUNDLE_MANIFEST", raising=False)
    monkeypatch.setattr(ollama_service, "_bundle_anchor_dirs", lambda: [tmp_path / "empty-bundle-anchor"])


def test_repository_vendor_ollama_is_not_a_bundled_source(monkeypatch, tmp_path: Path):
    for key in (
        "LENGRVIS_BUNDLED_OLLAMA_DIR",
        "LENGRVIS_BUNDLED_OLLAMA_DIR",
        "LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR",
        "LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR",
        "LENGRVIS_OLLAMA_BUNDLE_MANIFEST",
        "LENGRVIS_OLLAMA_BUNDLE_MANIFEST",
    ):
        monkeypatch.delenv(key, raising=False)

    runtime_dir = tmp_path / "vendor" / "ollama"
    models_dir = tmp_path / "vendor" / "ollama-models"
    manifest_path = tmp_path / "vendor" / "ollama-bundle-manifest.json"
    model_manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b"
    runtime_dir.mkdir(parents=True)
    model_manifest.parent.mkdir(parents=True)
    (runtime_dir / ("ollama.exe" if ollama_service.sys.platform == "win32" else "ollama")).write_text("fake", encoding="utf-8")
    model_manifest.write_text("{}", encoding="utf-8")
    manifest_path.write_text('{"schema":1,"accepted_licenses":true}', encoding="utf-8")
    monkeypatch.setattr(ollama_service, "_bundle_anchor_dirs", lambda: [tmp_path])

    assert ollama_service.bundled_runtime_available() is False
    assert ollama_service._bundled_ollama_models_dir() is None
    assert ollama_service._ollama_bundle_manifest_summary()["present"] is False


def _write_valid_bundle_manifest(manifest_path: Path, model: str = ollama_service.RECOMMENDED_MODEL) -> None:
    manifest_path.write_text(
        '{"schema":1,'
        f'"model":"{model}",'
        '"accepted_licenses":true,'
        '"runtime":{"summary":{"sha256":"runtime-hash","files":1}},'
        '"models":{"model_manifest":"manifests/registry.ollama.ai/library/qwen2.5/3b",'
        '"summary":{"sha256":"models-hash","files":1}}}',
        encoding="utf-8",
    )


def test_status_not_installed():
    with patch.object(ollama_service, "is_installed", return_value=False):
        result = asyncio.run(ollama_service.status())
        assert result["installed"] is False
        assert result["running"] is False
        assert result["models"] == []
        assert "readiness" in result


def test_status_installed_not_running():
    async def _not_running():
        return False

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", side_effect=_not_running):
        result = asyncio.run(ollama_service.status())
        assert result["installed"] is True
        assert result["running"] is False
        assert result["models"] == []


def test_status_installed_and_running():
    async def _running():
        return True

    async def _models():
        return ["qwen2.5:3b", "llama3:8b"]

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", side_effect=_running), \
         patch.object(ollama_service, "list_models", side_effect=_models):
        result = asyncio.run(ollama_service.status())
        assert result["installed"] is True
        assert result["running"] is True
        assert "qwen2.5:3b" in result["models"]
        assert result["has_recommended"] is True
        assert "readiness" in result


def test_status_running_without_recommended_model():
    async def _running():
        return True

    async def _models():
        return ["llama3:8b"]

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", side_effect=_running), \
         patch.object(ollama_service, "list_models", side_effect=_models):
        result = asyncio.run(ollama_service.status())
        assert result["installed"] is True
        assert result["running"] is True
        assert result["has_recommended"] is False


def test_status_matches_model_tag_prefix():
    async def _running():
        return True

    async def _models():
        return ["qwen2.5:3b-instruct-q4_0"]

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", side_effect=_running), \
         patch.object(ollama_service, "list_models", side_effect=_models):
        result = asyncio.run(ollama_service.status())
        assert result["has_recommended"] is True


def test_setup_plan_reports_next_action_for_missing_runtime(monkeypatch):
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )
    with patch.object(ollama_service, "is_installed", return_value=False):
        result = asyncio.run(ollama_service.setup_plan("qwen2.5:3b"))
        assert result["ready"] is False
        assert result["next_action"] == "install_runtime"
        assert result["steps"][0]["state"] == "done"
        assert result["steps"][1]["state"] == "current"


def test_setup_plan_clean_machine_no_runtime_no_model_has_safe_verification(monkeypatch, tmp_path: Path, no_bundled_ollama):
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [
                {"key": "memory", "label": "Memory", "ok": True, "actual": "16.0 GB", "required": "8.0 GB"},
                {"key": "disk", "label": "Free disk space", "ok": True, "actual": "64.0 GB", "required": "8.0 GB"},
                {"key": "cpu", "label": "CPU cores", "ok": True, "actual": "8", "required": "4"},
            ],
        },
    )

    with patch("shutil.which", return_value=None), \
         patch.object(ollama_service.os.path, "exists", return_value=False):
        result = asyncio.run(ollama_service.setup_plan("qwen2.5:3b"))

    evidence = {item["key"]: item for item in result["evidence"]}
    verification = result["verification"]
    payload = json.dumps(result)

    assert result["ready"] is False
    assert result["installed"] is False
    assert result["running"] is False
    assert result["has_model"] is False
    assert result["models"] == []
    assert result["runtime_source"] == "missing"
    assert result["bundled_runtime_available"] is False
    assert result["bundled_models_available"] is False
    assert result["bundled_model_available"] is False
    assert result["bundle_manifest"]["present"] is False
    assert result["next_action"] == "install_runtime"
    assert result["repair_action"]["code"] == "install_runtime"
    assert "one-click setup" in result["repair_action"]["detail"]
    assert verification["ready"] is False
    assert verification["next_action"] == "install_runtime"
    assert verification["paths_redacted"] is True
    assert verification["privacy_fallback"] == "local_only_until_ready"
    assert verification["runtime"] == {"checked": True, "found": False, "source": "missing", "path": ""}
    assert verification["model"]["listed"] is False
    assert verification["model"]["models_seen"] == []
    assert verification["bundle"]["model_proven"] is False
    assert verification["bundle"]["manifest_present"] is False
    assert verification["bundle"]["paths"] == ""
    assert evidence["runtime"]["ok"] is False
    assert evidence["runtime"]["path"] == ""
    assert evidence["model"]["ok"] is False
    assert evidence["model"]["models_seen"] == []
    assert evidence["bundle_manifest"]["ok"] is False
    assert evidence["bundle_manifest"]["path"] == ""
    assert evidence["bundled_model"]["ok"] is False
    assert evidence["bundled_model"]["models_path"] == ""
    assert evidence["bundled_model"]["model_manifest_path"] == ""
    assert str(tmp_path) not in payload
    assert str(tmp_path).replace("\\", "\\\\") not in payload
    assert "C:\\Users" not in payload
    assert "sk-" not in payload


def test_setup_plan_blocks_insufficient_resources_before_runtime_work(monkeypatch, no_bundled_ollama):
    blocked_readiness = {
        "can_install": False,
        "recommended_model": "qwen2.5:3b",
        "reason": "Local AI setup needs Memory >= 8.0 GB, Free disk space >= 8.0 GB.",
        "checks": [
            {"key": "memory", "label": "Memory", "ok": False, "actual": "4.0 GB", "required": "8.0 GB"},
            {"key": "disk", "label": "Free disk space", "ok": False, "actual": "2.0 GB", "required": "8.0 GB"},
            {"key": "cpu", "label": "CPU cores", "ok": True, "actual": "8", "required": "4"},
        ],
    }
    monkeypatch.setattr(ollama_service, "hardware_readiness", lambda model=None: blocked_readiness)

    with patch("shutil.which", return_value=None), \
         patch.object(ollama_service.os.path, "exists", return_value=False):
        result = asyncio.run(ollama_service.setup_plan("qwen2.5:3b"))

    hardware_evidence = next(item for item in result["evidence"] if item["key"] == "hardware")
    assert result["ready"] is False
    assert result["can_install"] is False
    assert result["next_action"] == "hardware_blocked"
    assert result["repair_action"]["code"] == "free_resources_for_local_ai"
    assert "will not silently use cloud or mock AI" in result["repair_action"]["detail"]
    assert result["verification"]["next_action"] == "hardware_blocked"
    assert result["verification"]["ready"] is False
    assert result["steps"][0]["state"] == "blocked"
    assert result["steps"][1]["state"] == "pending"
    assert result["steps"][2]["state"] == "pending"
    assert result["steps"][3]["state"] == "pending"
    assert hardware_evidence["ok"] is False
    assert hardware_evidence["failed_checks"] == ["memory", "disk"]
    assert hardware_evidence["checks"] == blocked_readiness["checks"]


def test_setup_plan_ready_when_runtime_and_model_exist(monkeypatch):
    async def _running():
        return True

    async def _models():
        return ["qwen2.5:3b"]

    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )
    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", side_effect=_running), \
         patch.object(ollama_service, "list_models", side_effect=_models):
        result = asyncio.run(ollama_service.setup_plan("qwen2.5:3b"))
        assert result["ready"] is True
        assert result["next_action"] == "ready"


def test_is_installed_checks_path(no_bundled_ollama):
    with patch("shutil.which", return_value=None), \
         patch("os.path.exists", return_value=False):
        assert ollama_service.is_installed() is False
    with patch("shutil.which", return_value="C:\\Program Files\\Ollama\\ollama.exe"):
        assert ollama_service.is_installed() is True


def test_is_running_returns_false_on_connection_error():
    async def _mock_get(*args, **kwargs):
        raise ConnectionError("refused")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client_cls.return_value = mock_client

        result = asyncio.run(ollama_service.is_running())
        assert result is False


def test_list_models_returns_empty_on_failure():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client_cls.return_value = mock_client

        result = asyncio.run(ollama_service.list_models())
        assert result == []


def test_install_already_installed(no_bundled_ollama):
    with patch("shutil.which", return_value="C:\\Program Files\\Ollama\\ollama.exe"):
        result = asyncio.run(ollama_service.install())
        assert result["ok"] is True
        assert "already installed" in result["message"]


def test_install_non_windows():
    with patch.object(ollama_service, "is_installed", return_value=False), \
         patch.object(ollama_service, "_ollama_runtime_source", return_value="missing"), \
         patch.object(ollama_service.sys, "platform", "linux"):
        result = asyncio.run(ollama_service.install())
        assert result["ok"] is False
        assert "Windows" in result["error"]


def test_install_winget_not_found():
    async def _raise_fnf(*args, **kwargs):
        raise FileNotFoundError()

    with patch.object(ollama_service, "is_installed", return_value=False), \
         patch.object(ollama_service, "_ollama_runtime_source", return_value="missing"), \
         patch.object(ollama_service.sys, "platform", "win32"), \
         patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
        result = asyncio.run(ollama_service.install())
        assert result["ok"] is False
        assert "winget" in result["error"].lower()


def test_bundled_runtime_takes_precedence_over_winget(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "ollama"
    runtime_dir.mkdir()
    executable = runtime_dir / ("ollama.exe" if ollama_service.sys.platform == "win32" else "ollama")
    executable.write_text("fake", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_DIR", str(runtime_dir))

    with patch("shutil.which", return_value=None), \
         patch("asyncio.create_subprocess_exec") as subprocess_exec:
        result = asyncio.run(ollama_service.install())

    assert result["ok"] is True
    assert result["source"] == "bundled"
    assert result["executable"] == ""
    subprocess_exec.assert_not_called()


def test_setup_plan_reports_bundled_runtime_and_model(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "ollama"
    models_dir = tmp_path / "ollama-models"
    manifest_path = tmp_path / "ollama-bundle-manifest.json"
    manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b"
    runtime_dir.mkdir()
    manifest.parent.mkdir(parents=True)
    (runtime_dir / ("ollama.exe" if ollama_service.sys.platform == "win32" else "ollama")).write_text("fake", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    manifest_path.write_text(
        '{"schema":1,"model":"qwen2.5:3b","accepted_licenses":true,'
        '"runtime":{"summary":{"sha256":"runtime-hash","files":1}},'
        '"models":{"summary":{"sha256":"models-hash","files":1}}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_DIR", str(runtime_dir))
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("LENGRVIS_OLLAMA_BUNDLE_MANIFEST", str(manifest_path))
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )

    with patch("shutil.which", return_value=None), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=False):
        result = asyncio.run(ollama_service.setup_plan("qwen2.5:3b"))

    assert result["runtime_source"] == "bundled"
    assert result["bundled_runtime_available"] is True
    assert result["bundled_models_available"] is True
    assert result["bundled_model_available"] is True
    assert result["bundle_manifest"]["present"] is True
    assert result["bundle_manifest"]["valid"] is True
    assert result["bundle_manifest"]["path"] == ""
    assert result["bundled_runtime_path"] == ""
    assert result["bundled_models_path"] == ""
    assert result["bundle_manifest"]["models_sha256"] == "models-hash"
    assert "included with Lengrvis" in result["steps"][3]["detail"]
    payload = json.dumps(result)
    assert str(tmp_path) not in payload
    assert str(tmp_path).replace("\\", "\\\\") not in payload
    assert str(runtime_dir) not in payload
    assert str(models_dir) not in payload


def test_setup_plan_accepts_bom_encoded_bundle_manifest(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "ollama"
    models_dir = tmp_path / "ollama-models"
    manifest_path = tmp_path / "ollama-bundle-manifest.json"
    manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b"
    runtime_dir.mkdir()
    manifest.parent.mkdir(parents=True)
    (runtime_dir / ("ollama.exe" if ollama_service.sys.platform == "win32" else "ollama")).write_text("fake", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    manifest_path.write_text(
        '{"schema":1,"model":"qwen2.5:3b","accepted_licenses":true,'
        '"runtime":{"summary":{"sha256":"runtime-hash","files":1}},'
        '"models":{"summary":{"sha256":"models-hash","files":1}}}',
        encoding="utf-8-sig",
    )
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_DIR", str(runtime_dir))
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("LENGRVIS_OLLAMA_BUNDLE_MANIFEST", str(manifest_path))
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )

    with patch("shutil.which", return_value=None), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=False):
        result = asyncio.run(ollama_service.setup_plan("qwen2.5:3b"))

    assert result["bundle_manifest"]["present"] is True
    assert result["bundle_manifest"]["valid"] is True
    assert result["bundle_manifest"]["models_sha256"] == "models-hash"


def test_setup_plan_redacts_bundle_manifest_string_fields(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "ollama"
    models_dir = tmp_path / "ollama-models"
    manifest_path = tmp_path / "ollama-bundle-manifest.json"
    model_manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b"
    private_manifest = r"C:\Users\Suli\private\models\qwen2.5\3b?token=sk-manifest-secret-1234567890"
    runtime_dir.mkdir()
    model_manifest.parent.mkdir(parents=True)
    (runtime_dir / ("ollama.exe" if ollama_service.sys.platform == "win32" else "ollama")).write_text("fake", encoding="utf-8")
    model_manifest.write_text("{}", encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "model": "qwen2.5:3b",
                "accepted_licenses": True,
                "runtime": {"summary": {"sha256": "runtime-hash token=sk-runtime-secret-1234567890", "files": 1}},
                "models": {"model_manifest": private_manifest, "summary": {"sha256": "models-hash", "files": 1}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_DIR", str(runtime_dir))
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("LENGRVIS_OLLAMA_BUNDLE_MANIFEST", str(manifest_path))
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )

    with patch("shutil.which", return_value=None), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=False):
        result = asyncio.run(ollama_service.setup_plan("qwen2.5:3b"))

    payload = json.dumps(result)
    assert result["bundle_manifest"]["present"] is True
    assert result["bundle_manifest"]["valid"] is True
    assert result["bundle_manifest"]["model"] == "qwen2.5:3b"
    assert "[REDACTED_LOCAL_PATH]" in result["bundle_manifest"]["model_manifest"]
    assert "sk-manifest-secret-1234567890" not in payload
    assert "sk-runtime-secret-1234567890" not in payload
    assert r"C:\Users\Suli" not in payload
    assert str(tmp_path) not in payload
    assert str(tmp_path).replace("\\", "\\\\") not in payload
    assert result["evidence"][4]["value"] == "qwen2.5:3b"


def test_ollama_action_error_redacts_paths_urls_and_tokens(monkeypatch):
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )
    monkeypatch.setattr(ollama_service, "is_installed", lambda: False)
    monkeypatch.setattr(ollama_service, "_ollama_runtime_source", lambda: "missing")
    monkeypatch.setattr(ollama_service, "_bundled_model_available", lambda model: False)
    raw = r"failed at C:\Users\Suli\.ollama\models\blob with token=sk-1234567890abcdef and http://127.0.0.1:11434/api/pull"

    result = ollama_service._ollama_action_error(raw, "download_model", model="qwen2.5:3b")

    assert "C:\\Users" not in result["error"]
    assert "http://127.0.0.1" not in result["error"]
    assert "sk-1234567890abcdef" not in result["error"]
    assert "[REDACTED_LOCAL_PATH]" in result["error"]
    assert "[REDACTED_URL]" in result["error"]
    assert result["repair_action"]["code"] == "download_model"


def test_setup_plan_does_not_claim_bundled_model_without_manifest(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "ollama"
    models_dir = tmp_path / "ollama-models"
    manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b"
    runtime_dir.mkdir()
    manifest.parent.mkdir(parents=True)
    (runtime_dir / ("ollama.exe" if ollama_service.sys.platform == "win32" else "ollama")).write_text("fake", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_DIR", str(runtime_dir))
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", str(models_dir))
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )

    with patch("shutil.which", return_value=None), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
         patch.object(ollama_service, "list_models", new_callable=AsyncMock, return_value=[]):
        result = asyncio.run(ollama_service.setup_plan("qwen2.5:3b"))

    assert result["bundled_models_available"] is True
    assert result["bundled_model_available"] is False
    assert result["has_model"] is False
    assert result["next_action"] == "download_model"
    assert result["bundle_manifest"]["present"] is False
    assert result["evidence"][4]["key"] == "bundle_manifest"
    assert result["evidence"][4]["ok"] is False
    assert "manifest" in result["evidence"][5]["detail"]


def test_setup_plan_reports_restart_repair_when_running_service_not_using_bundled_models(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "ollama"
    models_dir = tmp_path / "ollama-models"
    manifest_path = tmp_path / "ollama-bundle-manifest.json"
    manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b"
    runtime_dir.mkdir()
    manifest.parent.mkdir(parents=True)
    (runtime_dir / ("ollama.exe" if ollama_service.sys.platform == "win32" else "ollama")).write_text("fake", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    _write_valid_bundle_manifest(manifest_path)
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_DIR", str(runtime_dir))
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("LENGRVIS_OLLAMA_BUNDLE_MANIFEST", str(manifest_path))
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )

    with patch("shutil.which", return_value=None), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
         patch.object(ollama_service, "list_models", new_callable=AsyncMock, return_value=[]):
        result = asyncio.run(ollama_service.setup_plan("qwen2.5:3b"))

    bundled_evidence = next(item for item in result["evidence"] if item["key"] == "bundled_model")
    assert result["bundled_model_available"] is True
    assert result["bundled_model_configured"] is False
    assert result["next_action"] == "restart_runtime_with_bundled_models"
    assert result["repair_action"]["code"] == "restart_runtime_with_bundled_models"
    assert result["steps"][3]["label"] == "Restart local service for bundled model"
    assert "not using" in result["steps"][3]["detail"]
    assert bundled_evidence["ok"] is True
    assert bundled_evidence["configured"] is False


def test_start_server_requires_installed_ollama():
    with patch.object(ollama_service, "is_installed", return_value=False), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=False):
        result = asyncio.run(ollama_service.start_server())
        assert result["ok"] is False
        assert "not installed" in result["error"]


def test_start_server_launches_ollama_when_available():
    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "_ollama_executable", return_value="ollama"), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=False), \
         patch("subprocess.Popen") as popen, \
         patch.object(ollama_service, "record"):
        result = asyncio.run(ollama_service.start_server())
        assert result["ok"] is True
        popen.assert_called_once()


def test_start_server_uses_bundled_models_dir(monkeypatch, tmp_path: Path):
    models_dir = tmp_path / "ollama-models"
    models_dir.mkdir()
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", str(models_dir))

    with patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=False), \
         patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "_ollama_executable", return_value="ollama"), \
         patch("subprocess.Popen") as popen, \
         patch.object(ollama_service, "record"):
        result = asyncio.run(ollama_service.start_server())

    assert result["ok"] is True
    assert result["models_dir"] == ""
    assert result["models_dir_configured"] is True
    _, kwargs = popen.call_args
    assert kwargs["env"]["OLLAMA_MODELS"] == str(models_dir)


def test_pull_model_connection_error():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client_cls.return_value = mock_client

        with patch.object(ollama_service, "record"):
            result = asyncio.run(ollama_service.pull_model("qwen2.5:3b"))
            assert result["ok"] is False
            assert result["model"] == "qwen2.5:3b"
            assert result["repair_action"]["code"] == "download_model"


def test_pull_model_rejects_unsupported_model_without_http_call():
    with patch("httpx.AsyncClient") as mock_client_cls:
        result = asyncio.run(ollama_service.pull_model("not-approved:70b"))

    assert result["ok"] is False
    assert result["status"] == "error"
    assert "restricted" in result["error"]
    assert result["model"] == "not-approved:70b"
    mock_client_cls.assert_not_called()


def test_pull_model_uses_default():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        with patch.object(ollama_service, "record"):
            result = asyncio.run(ollama_service.pull_model())
            assert result["ok"] is True
            assert result["model"] == ollama_service.RECOMMENDED_MODEL


def test_assess_hardware_blocks_underpowered_machine():
    result = ollama_service.assess_hardware(
        memory_total_bytes=4 * 1024**3,
        disk_free_bytes=4 * 1024**3,
        cpu_logical_cores=2,
    )
    assert result["can_install"] is False
    assert len([check for check in result["checks"] if not check["ok"]]) == 3
    assert result["next_action"] == "hardware_blocked"
    assert result["repair_action"]["code"] == "free_resources_for_local_ai"


def test_assess_hardware_recommends_medium_model_when_resources_allow():
    result = ollama_service.assess_hardware(
        memory_total_bytes=32 * 1024**3,
        disk_free_bytes=64 * 1024**3,
        cpu_logical_cores=12,
    )
    assert result["can_install"] is True
    assert result["recommended_model"] == ollama_service.FALLBACK_MEDIUM_MODEL
    assert result["next_action"] == "continue_setup"
    assert result["repair_action"]["code"] == "continue_setup"


@pytest.mark.asyncio
async def test_install_local_model_stops_when_hardware_not_ready(monkeypatch):
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": False,
            "recommended_model": "qwen2.5:3b",
            "reason": "not enough memory",
            "checks": [],
        },
    )
    results = []
    async for progress in ollama_service.install_local_model():
        results.append(progress)
    assert len(results) == 1
    assert results[0]["phase"] == "hardware"
    assert results[0]["status"] == "error"
    assert results[0]["error"] == "not enough memory"
    assert results[0]["model"] == "qwen2.5:3b"
    assert results[0]["next_action"] == "hardware_blocked"
    assert results[0]["repair_action"]["code"] == "free_resources_for_local_ai"
    assert "will not silently use cloud or mock AI" in results[0]["repair_action"]["detail"]
    assert results[0]["verification"]["next_action"] == "hardware_blocked"
    assert results[0]["verification"]["ready"] is False
    assert any(item["key"] == "hardware" and item["ok"] is False for item in results[0]["evidence"])


@pytest.mark.asyncio
async def test_install_local_model_reports_start_server_failure(monkeypatch):
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )
    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=False), \
         patch.object(ollama_service, "start_server", new_callable=AsyncMock, return_value={"ok": False, "error": "port busy"}):
        results = []
        async for progress in ollama_service.install_local_model("qwen2.5:3b"):
            results.append(progress)

    assert results[-1]["phase"] == "start"
    assert results[-1]["status"] == "error"
    assert results[-1]["error"] == "port busy"
    assert results[-1]["repair_action"]["code"] == "start_runtime"


@pytest.mark.asyncio
async def test_install_local_model_stops_on_pull_error(monkeypatch, no_bundled_ollama):
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )

    async def _pull_error(model=None):
        yield {"status": "error", "error": "network down"}

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
         patch.object(ollama_service, "list_models", new_callable=AsyncMock, return_value=[]), \
         patch.object(ollama_service, "pull_model_streaming", side_effect=_pull_error):
        results = []
        async for progress in ollama_service.install_local_model("qwen2.5:3b"):
            results.append(progress)

    assert results[-1]["phase"] == "pull"
    assert results[-1]["status"] == "error"
    assert results[-1]["error"] == "network down"
    assert results[-1]["repair_action"]["code"] == "download_model"
    assert not any(item.get("phase") == "switch" for item in results)


@pytest.mark.asyncio
async def test_install_local_model_verifies_model_is_listed_after_pull_success(monkeypatch, no_bundled_ollama):
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )

    async def _pull_success(model=None):
        yield {"status": "success", "model": model or "qwen2.5:3b"}

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
         patch.object(ollama_service, "list_models", new_callable=AsyncMock, return_value=[]) as list_models, \
         patch.object(ollama_service, "pull_model_streaming", side_effect=_pull_success):
        results = []
        async for progress in ollama_service.install_local_model("qwen2.5:3b"):
            results.append(progress)

    assert list_models.await_count >= 2
    assert results[-1]["phase"] == "pull"
    assert results[-1]["status"] == "error"
    assert results[-1]["next_action"] == "download_model"
    assert results[-1]["repair_action"]["code"] == "download_model"
    assert "did not list the model after refresh" in results[-1]["error"]
    assert results[-1]["verification"]["ready"] is False
    assert results[-1]["verification"]["model"]["listed"] is False
    assert any(item["key"] == "model" and item["ok"] is False for item in results[-1]["evidence"])
    assert not any(item.get("phase") == "switch" for item in results)


@pytest.mark.asyncio
async def test_install_local_model_requires_visible_bundled_model_before_switch(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "ollama"
    models_dir = tmp_path / "ollama-models"
    manifest_path = tmp_path / "ollama-bundle-manifest.json"
    manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b"
    runtime_dir.mkdir()
    manifest.parent.mkdir(parents=True)
    (runtime_dir / ("ollama.exe" if ollama_service.sys.platform == "win32" else "ollama")).write_text("fake", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    _write_valid_bundle_manifest(manifest_path)
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_DIR", str(runtime_dir))
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("LENGRVIS_OLLAMA_BUNDLE_MANIFEST", str(manifest_path))
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )

    running_states = iter([False, True])

    async def _is_running():
        return next(running_states, True)

    with patch.object(ollama_service, "is_running", side_effect=_is_running), \
         patch.object(ollama_service, "start_server", new_callable=AsyncMock, return_value={"ok": True, "models_dir": str(models_dir)}), \
         patch.object(ollama_service, "list_models", new_callable=AsyncMock, return_value=[]), \
         patch.object(ollama_service, "pull_model_streaming") as pull:
        results = []
        async for progress in ollama_service.install_local_model("qwen2.5:3b"):
            results.append(progress)

    assert results[-1]["phase"] == "pull"
    assert results[-1]["status"] == "error"
    assert results[-1]["next_action"] == "download_model"
    assert results[-1]["repair_action"]["code"] == "download_model"
    assert "did not list" in results[-1]["error"]
    assert "verify the bundled model package" in results[-1]["error"]
    assert not any(item.get("phase") == "switch" for item in results)
    payload = json.dumps(results)
    assert str(tmp_path) not in payload
    assert str(tmp_path).replace("\\", "\\\\") not in payload
    assert str(models_dir) not in payload
    pull.assert_not_called()


@pytest.mark.asyncio
async def test_install_local_model_does_not_pull_when_bundled_model_needs_service_restart(monkeypatch, tmp_path: Path):
    runtime_dir = tmp_path / "ollama"
    models_dir = tmp_path / "ollama-models"
    manifest_path = tmp_path / "ollama-bundle-manifest.json"
    manifest = models_dir / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "3b"
    runtime_dir.mkdir()
    manifest.parent.mkdir(parents=True)
    (runtime_dir / ("ollama.exe" if ollama_service.sys.platform == "win32" else "ollama")).write_text("fake", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    _write_valid_bundle_manifest(manifest_path)
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_DIR", str(runtime_dir))
    monkeypatch.setenv("LENGRVIS_BUNDLED_OLLAMA_MODELS_DIR", str(models_dir))
    monkeypatch.setenv("LENGRVIS_OLLAMA_BUNDLE_MANIFEST", str(manifest_path))
    monkeypatch.setattr(
        ollama_service,
        "hardware_readiness",
        lambda model=None: {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        },
    )

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
         patch.object(ollama_service, "list_models", new_callable=AsyncMock, return_value=[]), \
         patch.object(ollama_service, "pull_model_streaming") as pull:
        results = []
        async for progress in ollama_service.install_local_model("qwen2.5:3b"):
            results.append(progress)

    assert results[-1]["phase"] == "pull"
    assert results[-1]["status"] == "error"
    assert results[-1]["next_action"] == "restart_runtime_with_bundled_models"
    assert results[-1]["repair_action"]["code"] == "restart_runtime_with_bundled_models"
    assert "Close Ollama and retry setup" in results[-1]["error"]
    pull.assert_not_called()
