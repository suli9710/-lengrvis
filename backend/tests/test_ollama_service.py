"""Tests for the Ollama lifecycle service."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import ollama_service


def test_status_not_installed():
    with patch.object(ollama_service, "is_installed", return_value=False):
        result = asyncio.run(ollama_service.status())
        assert result["installed"] is False
        assert result["running"] is False
        assert result["models"] == []


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
        return ["qwen2.5:3b-instruct", "llama3:8b"]

    with patch.object(ollama_service, "is_installed", return_value=True), \
         patch.object(ollama_service, "is_running", side_effect=_running), \
         patch.object(ollama_service, "list_models", side_effect=_models):
        result = asyncio.run(ollama_service.status())
        assert result["installed"] is True
        assert result["running"] is True
        assert "qwen2.5:3b-instruct" in result["models"]
        assert result["has_recommended"] is True


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


def test_is_installed_checks_path():
    with patch("shutil.which", return_value=None):
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


def test_install_already_installed():
    with patch.object(ollama_service, "is_installed", return_value=True):
        result = asyncio.run(ollama_service.install())
        assert result["ok"] is True
        assert "already installed" in result["message"]


def test_install_non_windows():
    with patch.object(ollama_service, "is_installed", return_value=False), \
         patch.object(ollama_service.sys, "platform", "linux"):
        result = asyncio.run(ollama_service.install())
        assert result["ok"] is False
        assert "Windows" in result["error"]


def test_install_winget_not_found():
    async def _raise_fnf(*args, **kwargs):
        raise FileNotFoundError()

    with patch.object(ollama_service, "is_installed", return_value=False), \
         patch.object(ollama_service.sys, "platform", "win32"), \
         patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
        result = asyncio.run(ollama_service.install())
        assert result["ok"] is False
        assert "winget" in result["error"].lower()


def test_pull_model_connection_error():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=ConnectionError("refused"))
        mock_client_cls.return_value = mock_client

        with patch.object(ollama_service, "record"):
            result = asyncio.run(ollama_service.pull_model("test-model"))
            assert result["ok"] is False
            assert result["model"] == "test-model"


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
