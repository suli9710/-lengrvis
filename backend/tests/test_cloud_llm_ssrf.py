from __future__ import annotations

import pytest

from app.config import AppSettings
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.registry import _build_cloud_provider


def test_build_cloud_provider_rejects_private_base_url():
    settings = AppSettings(
        provider_name="openai",
        base_url="http://192.168.1.10/v1",
        api_key="sk-test",
        requires_openai_auth=True,
    )
    with pytest.raises(ValueError, match="blocked to prevent SSRF"):
        _build_cloud_provider(settings)


def test_openai_provider_rejects_private_base_url_on_request():
    settings = AppSettings(
        provider_name="openai",
        base_url="http://169.254.169.254/v1",
        api_key="sk-test",
        requires_openai_auth=True,
    )
    provider = OpenAICompatibleProvider(settings)
    with pytest.raises(ValueError, match="blocked to prevent SSRF"):
        provider._api_base_url()
