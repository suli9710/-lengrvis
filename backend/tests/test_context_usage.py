from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_context import router
from app.config import AppSettings
from app.context_management import count_messages_tokens, rough_token_count
from app.context_usage import (
    AGENT_HISTORY_CATEGORY,
    AUTO_COMPACT_BUFFER_CATEGORY,
    FREE_SPACE_CATEGORY,
    MANUAL_COMPACT_BUFFER_CATEGORY,
    MCP_TOOLS_CATEGORY,
    SESSION_MEMORY_CATEGORY,
    SYSTEM_CONTEXT_CATEGORY,
    TOOLS_REGISTRY_CATEGORY,
    analyze_context_usage,
    context_usage_to_dict,
)


def _settings(**overrides) -> AppSettings:
    settings = AppSettings(
        model_context_window=1000,
        model_auto_compact_token_limit=600,
        max_tokens=100,
        context_manual_compact_buffer_tokens=50,
        context_warning_buffer_tokens=20,
        context_error_buffer_tokens=10,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _by_id(report):
    return {category.id: category for category in report.categories}


def test_analyze_context_usage_groups_prompt_sources():
    messages = [
        {"role": "system", "content": "You are careful."},
        {"role": "assistant", "content": "I will inspect the repo."},
        {"role": "tool", "content": {"type": "tool_result", "content": "file output"}},
    ]
    tools = [
        {"name": "file.read", "description": "Read a file", "input_schema": {"type": "object"}},
        {"name": "mcp.docs.search", "description": "Search docs", "input_schema": {"type": "object"}},
    ]
    session_context = {
        "conversation_summary": "Working on context UX.",
        "notes": ["prefer small patches"],
    }

    report = analyze_context_usage(
        messages=messages,
        tool_definitions=tools,
        session_context=session_context,
        settings=_settings(),
        include_registered_tools=False,
    )

    categories = _by_id(report)
    assert categories[SYSTEM_CONTEXT_CATEGORY].tokens == count_messages_tokens([messages[0]])
    assert categories[AGENT_HISTORY_CATEGORY].tokens == count_messages_tokens(messages[1:])
    assert categories[TOOLS_REGISTRY_CATEGORY].item_count == 1
    assert categories[MCP_TOOLS_CATEGORY].item_count == 1
    assert categories[SESSION_MEMORY_CATEGORY].tokens == rough_token_count(session_context)
    assert report.used_tokens == sum(
        categories[category_id].tokens
        for category_id in [
            SYSTEM_CONTEXT_CATEGORY,
            TOOLS_REGISTRY_CATEGORY,
            MCP_TOOLS_CATEGORY,
            SESSION_MEMORY_CATEGORY,
            AGENT_HISTORY_CATEGORY,
        ]
    )


def test_analyze_context_usage_reports_free_space_and_buffers():
    report = analyze_context_usage(
        messages=[{"role": "user", "content": "hello"}],
        tool_definitions=[],
        session_context={},
        settings=_settings(),
        include_registered_tools=False,
    )

    categories = _by_id(report)
    assert report.effective_context_window == 900
    assert report.auto_compact_threshold == 600
    assert categories[MANUAL_COMPACT_BUFFER_CATEGORY].tokens == 50
    assert categories[AUTO_COMPACT_BUFFER_CATEGORY].tokens == 250
    assert categories[FREE_SPACE_CATEGORY].tokens == 900 - report.used_tokens - 50 - 250
    assert report.total_tokens == sum(category.tokens for category in report.categories)


def test_analyze_context_usage_clamps_free_space_when_context_is_overfilled():
    report = analyze_context_usage(
        messages=[{"role": "user", "content": "x" * 5000}],
        tool_definitions=[],
        session_context={},
        settings=_settings(),
        include_registered_tools=False,
    )

    categories = _by_id(report)
    assert categories[FREE_SPACE_CATEGORY].tokens == 0
    assert report.warning["is_above_auto_compact_threshold"] is True


def test_context_usage_route_estimates_payload():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/context/usage",
        json={
            "messages": [{"role": "developer", "content": "Follow local policy."}, {"role": "user", "content": "Ship it."}],
            "tools": [{"name": "file.read", "description": "Read a file", "input_schema": {"type": "object"}}],
            "session_context": {"conversation_summary": "short"},
            "include_registered_tools": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    category_ids = {category["id"] for category in payload["categories"]}
    assert SYSTEM_CONTEXT_CATEGORY in category_ids
    assert AGENT_HISTORY_CATEGORY in category_ids
    assert TOOLS_REGISTRY_CATEGORY in category_ids
    assert payload["used_tokens"] > 0


def test_context_usage_to_dict_is_api_ready():
    report = analyze_context_usage(
        messages=[],
        tool_definitions=[],
        session_context={},
        settings=_settings(context_auto_compact_enabled=False),
        include_registered_tools=False,
    )

    payload = context_usage_to_dict(report)

    assert payload["warning"]["is_above_auto_compact_threshold"] is False
    assert isinstance(payload["categories"][0]["percent"], float)
