from __future__ import annotations

import asyncio
import logging
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
from app.observability.best_effort import log_best_effort_failure
from app.orchestration.agent_bus import AgentBus
from app.orchestration.dispatcher import EventDispatcher
from app.perception.environment_stream import get_environment_stream
from app.services.guardian_runtime import runtime
from app.services.guardian_scheduler import get_guardian_scheduler
from app.services.scheduler_service import get_scheduler
from app.tools.registry import register_all_tools

AsyncCleanup = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


@asynccontextmanager
async def full_backend_lifespan(app: FastAPI):
    db.init_db()
    _enforce_local_data_protection()
    _prepare_run_runtime()
    settings = get_effective_settings()

    async with AsyncExitStack() as stack:
        stack.callback(db.close_thread_connection)
        await _load_mcp_tools(settings)
        stack.push_async_callback(get_mcp_registry().close)
        _register_process_cleanups(stack)
        session_store = _load_session_context()
        await _start_scheduler(stack)
        await _start_file_environment(stack, settings)
        stack.callback(session_store.save)
        _register_runtime_cleanups(stack)
        yield


def _enforce_local_data_protection() -> None:
    from app.services.local_retention_service import cleanup_expired_task_details
    from app.services.task_recording_service import migrate_plaintext_recordings

    cleanup_expired_task_details()
    migration = migrate_plaintext_recordings()
    if migration["migrated"]:
        record(
            "privacy.task_recordings_encrypted",
            "lifespan",
            {
                "migrated": migration["migrated"],
                "scanned": migration["scanned"],
                "storage": "dpapi_wrapped_aes_256_gcm",
            },
        )


@asynccontextmanager
async def guardian_lifespan(app: FastAPI):
    db.init_db()
    async with AsyncExitStack() as stack:
        stack.callback(db.close_thread_connection)
        await runtime.start()
        stack.push_async_callback(runtime.stop)
        scheduler = get_guardian_scheduler()
        await scheduler.start()
        stack.push_async_callback(scheduler.stop)
        yield


def _prepare_run_runtime() -> None:
    from app.orchestration.tool_execution_journal import recover_interrupted_tool_executions
    from app.services.run_service import enter_foreground_runtime, recover_interrupted_runs

    enter_foreground_runtime()
    try:
        unknown_tool_calls = recover_interrupted_tool_executions()
        if unknown_tool_calls:
            record(
                "lifespan.tool_executions_recovered",
                "lifespan",
                {"outcome_unknown_tool_call_ids": unknown_tool_calls},
            )
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        record("lifespan.tool_execution_recovery_failed", "lifespan", {"error": str(exc)})
    try:
        recovered = recover_interrupted_runs()
        if recovered:
            record("lifespan.runs_recovered", "lifespan", {"run_ids": recovered})
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        record("lifespan.run_recovery_failed", "lifespan", {"error": str(exc)})


async def _load_mcp_tools(settings: AppSettings) -> None:
    mcp_registry = get_mcp_registry()
    mcp_registry.load_from_settings(settings)
    try:
        mcp_definitions = await mcp_registry.adapt_to_tool_definitions()
    except (KeyError, TypeError, ValueError) as exc:
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
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
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
    from app.automation.file_trigger import AutomationFileTriggerService

    watcher = get_file_watcher()
    automation_triggers = AutomationFileTriggerService(allowed_directories=settings.allowed_directories)
    environment_bus = AgentBus()
    environment_stream = get_environment_stream(
        dispatcher=EventDispatcher(environment_bus),
        bus=environment_bus,
        settings=settings,
        reset=True,
    )
    file_environment_sink = environment_stream.file_change_sink()
    environment_start_attempted = False
    watcher_start_attempted = False
    subscription_attempted = False
    automation_start_attempted = False
    try:
        subscription_attempted = True
        watcher.subscribe_changes(file_environment_sink)
        environment_start_attempted = True
        await environment_stream.start()
        watcher_start_attempted = True
        await watcher.start(settings.allowed_directories)
        automation_start_attempted = True
        await automation_triggers.start(watcher)
    except BaseException:  # noqa: BLE001 - startup must unwind partially initialized resources.
        try:
            await _stop_file_environment(
                watcher,
                environment_stream,
                file_environment_sink,
                automation_triggers,
                stop_automation=automation_start_attempted,
                stop_watcher=watcher_start_attempted,
                unsubscribe=subscription_attempted,
                stop_environment=environment_start_attempted,
            )
        except BaseException as cleanup_exc:  # noqa: BLE001 - preserve the original startup failure.
            log_best_effort_failure(logger, "lifespan.file_environment_startup_cleanup", cleanup_exc)
        raise
    stack.push_async_callback(
        _stop_file_environment,
        watcher,
        environment_stream,
        file_environment_sink,
        automation_triggers,
    )


async def _stop_file_environment(
    watcher: Any,
    environment_stream: Any,
    file_environment_sink: Any,
    automation_triggers: Any,
    *,
    stop_automation: bool = True,
    stop_watcher: bool = True,
    unsubscribe: bool = True,
    stop_environment: bool = True,
) -> None:
    failures: list[BaseException] = []

    async def _await_cleanup(operation: str, cleanup: AsyncCleanup) -> None:
        try:
            await cleanup()
        except BaseException as exc:  # noqa: BLE001 - teardown must continue through every owned resource.
            failures.append(exc)
            log_best_effort_failure(logger, operation, exc)

    if stop_automation:
        await _await_cleanup("lifespan.automation_triggers.stop", automation_triggers.stop)
    if stop_watcher:
        await _await_cleanup("lifespan.file_watcher.stop", watcher.stop)
    if unsubscribe:
        try:
            watcher.unsubscribe_changes(file_environment_sink)
        except BaseException as exc:  # noqa: BLE001 - teardown must continue through every owned resource.
            failures.append(exc)
            log_best_effort_failure(logger, "lifespan.file_watcher.unsubscribe", exc)
    if stop_environment:
        await _await_cleanup("lifespan.environment_stream.stop", environment_stream.stop)
    if failures:
        raise failures[0]
