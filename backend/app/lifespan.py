from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.config import AppSettings
from app.core import db
from app.core.audit import record
from app.core.session_context import get_session_context_store
from app.indexer.file_watcher import get_file_watcher
from app.llm.registry import get_effective_settings
from app.mcp import get_mcp_registry
from app.orchestration.agent_bus import AgentBus
from app.orchestration.dispatcher import EventDispatcher
from app.perception.environment_stream import get_environment_stream
from app.services.guardian_runtime import runtime
from app.services.guardian_scheduler import get_guardian_scheduler
from app.services.scheduler_service import get_scheduler
from app.tools.registry import register_all_tools

AsyncCleanup = Callable[[], Awaitable[None]]


@asynccontextmanager
async def full_backend_lifespan(app: FastAPI):
    db.init_db()
    _prepare_run_runtime()
    settings = get_effective_settings()
    await _load_mcp_tools(settings)

    async with AsyncExitStack() as stack:
        _register_process_cleanups(stack)
        session_store = _load_session_context()
        await _start_scheduler(stack)
        await _start_file_environment(stack, settings)
        stack.callback(session_store.save)
        _register_runtime_cleanups(stack)
        yield


@asynccontextmanager
async def guardian_lifespan(app: FastAPI):
    db.init_db()
    async with AsyncExitStack() as stack:
        await runtime.start()
        stack.push_async_callback(runtime.stop)
        scheduler = get_guardian_scheduler()
        await scheduler.start()
        stack.push_async_callback(scheduler.stop)
        yield


def _prepare_run_runtime() -> None:
    from app.services.run_service import enter_foreground_runtime, recover_interrupted_runs

    enter_foreground_runtime()
    try:
        recovered = recover_interrupted_runs()
        if recovered:
            record("lifespan.runs_recovered", "lifespan", {"run_ids": recovered})
    except Exception as exc:  # noqa: BLE001
        record("lifespan.run_recovery_failed", "lifespan", {"error": str(exc)})


async def _load_mcp_tools(settings: AppSettings) -> None:
    mcp_registry = get_mcp_registry()
    mcp_registry.load_from_settings(settings)
    try:
        mcp_definitions = await mcp_registry.adapt_to_tool_definitions()
    except Exception as exc:  # noqa: BLE001
        mcp_definitions = []
        record("mcp.startup_load_failed", "lifespan", {"error": str(exc)})
    register_all_tools(extra_definitions=mcp_definitions, settings=settings)


def _register_process_cleanups(stack: AsyncExitStack) -> None:
    from app.services.ollama_service import stop_spawned_server
    from app.services.task_pool import get_pool

    stack.callback(stop_spawned_server)
    stack.push_async_callback(lambda: get_pool().shutdown())


def _register_runtime_cleanups(stack: AsyncExitStack) -> None:
    from app.llm.openai_compatible import close_shared_http_client
    from app.orchestration.agent_bus import flush_agent_message_writes
    from app.services.run_service import shutdown_runs

    stack.push_async_callback(close_shared_http_client)
    stack.push_async_callback(lambda: asyncio.to_thread(flush_agent_message_writes))
    stack.push_async_callback(_drain_runs, shutdown_runs)


async def _drain_runs(shutdown_runs: AsyncCleanup) -> None:
    try:
        await shutdown_runs()
    except Exception as exc:  # noqa: BLE001
        record("lifespan.run_drain_failed", "lifespan", {"error": str(exc)})


def _load_session_context() -> Any:
    session_store = get_session_context_store()
    session_store.load_global_latest()
    return session_store


async def _start_scheduler(stack: AsyncExitStack) -> None:
    scheduler = get_scheduler()
    await scheduler.start()
    stack.push_async_callback(scheduler.stop)


async def _start_file_environment(stack: AsyncExitStack, settings: AppSettings) -> None:
    watcher = get_file_watcher()
    environment_bus = AgentBus()
    environment_stream = get_environment_stream(
        dispatcher=EventDispatcher(environment_bus),
        bus=environment_bus,
        settings=settings,
        reset=True,
    )
    file_environment_sink = environment_stream.file_change_sink()
    watcher.subscribe_changes(file_environment_sink)
    await environment_stream.start()
    await watcher.start(settings.allowed_directories)
    stack.push_async_callback(_stop_file_environment, watcher, environment_stream, file_environment_sink)


async def _stop_file_environment(watcher: Any, environment_stream: Any, file_environment_sink: Any) -> None:
    await watcher.stop()
    watcher.unsubscribe_changes(file_environment_sink)
    await environment_stream.stop()
