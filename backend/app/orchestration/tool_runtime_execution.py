from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any
from weakref import WeakKeyDictionary

from app.core.audit import record
from app.core.schemas import PlanStep
from app.llm.registry import get_effective_settings
from app.orchestration.resource_state import (
    attach_dry_run_resource_state,
    capture_tool_resource_state,
    remember_read_states_for_tool,
    validate_write_preconditions,
)
from app.orchestration.tool_runtime_paths import ensure_authorized_paths, write_lock_keys
from app.orchestration.tool_runtime_support import (
    _DEFAULT_TOOL_TIMEOUT_SECONDS,
    _MAX_DAEMON_TOOL_THREADS,
    _TOOL_THREAD_SLOTS,
    _ToolWorkerHandle,
)
from app.tools.schemas import ToolDefinition

logger = logging.getLogger(__name__)

_SHARED_PATH_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()
_SHARED_PENDING_TOOL_COMPLETIONS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Future[Any]]] = (
    WeakKeyDictionary()
)


class ToolRuntimeExecutionMixin:
    async def execute_tool_with_locks(
        self,
        tool: ToolDefinition,
        step: PlanStep,
        args: dict[str, Any],
        context: dict[str, Any],
        *,
        threaded: bool = False,
    ) -> dict[str, Any]:
        ensure_authorized_paths(tool, args, context)
        lock_keys = write_lock_keys(tool, args)
        if not lock_keys:
            output = await self._execute_tool_body(tool, args, context, threaded=threaded, lock_keys=())
            return self._normalize_tool_output(tool, args, context, output)
        await self._await_pending_tool_completions(lock_keys, tool=tool)
        path_locks = self._locks_for_current_loop()
        locks = [path_locks.setdefault(key, asyncio.Lock()) for key in lock_keys]
        output = await self._execute_tool_under_locks(
            tool,
            args,
            context,
            locks,
            lock_keys=lock_keys,
            threaded=threaded,
        )
        return self._normalize_tool_output(tool, args, context, output)

    def _normalize_tool_output(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
        output: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(output, dict):
            output = {"result": output}
        attach_dry_run_resource_state(output, tool, args, context)
        remember_read_states_for_tool(tool, args, output, context)
        return output

    async def _execute_tool_under_locks(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
        locks: list[asyncio.Lock],
        *,
        lock_keys: list[str],
        threaded: bool = False,
    ) -> dict[str, Any]:
        if not locks:
            await self._await_pending_tool_completions(lock_keys, tool=tool)
            return await self._execute_tool_body(tool, args, context, threaded=threaded, lock_keys=lock_keys)
        async with locks[0]:
            return await self._execute_tool_under_locks(
                tool,
                args,
                context,
                locks[1:],
                lock_keys=lock_keys,
                threaded=threaded,
            )

    async def _execute_tool_body(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
        *,
        threaded: bool,
        lock_keys: list[str] | tuple[str, ...] = (),
    ) -> dict[str, Any]:
        current_state = capture_tool_resource_state(tool, args, context)
        if current_state:
            context["_resource_state_before"] = current_state
        validate_write_preconditions(
            tool=tool,
            args=args,
            context=context,
            current_state=current_state,
            expected_approval_state=context.get("_expected_resource_state"),
        )
        # Tool implementations are synchronous (file IO, COM automation, OCR,
        # subprocess, HTTP). Always run them off the event loop thread so a
        # slow tool cannot freeze every concurrent request and WebSocket.
        timeout = self._tool_execution_timeout(context)
        try:
            worker = self._start_daemon_tool_worker(tool, args, context)
        except RuntimeError as exc:
            return {"error": str(exc), "resource_exhausted": True, "retry_after_pending_completion": True}
        try:
            return await asyncio.wait_for(asyncio.shield(worker.future), timeout=timeout)
        except TimeoutError:
            self._remember_pending_tool_completion(lock_keys, worker.future, tool=tool, reason="timeout")
            pending_completion = bool(lock_keys and not worker.future.done())
            error = f"{tool.name} timed out after {timeout:.0f}s"
            if pending_completion:
                error = (
                    f"{error}; execution is still finishing in the background and follow-up "
                    "calls for the same resource/tool will wait before running."
                )
            return {
                "error": error,
                "timed_out": True,
                "pending_completion": pending_completion,
                "retry_after_pending_completion": pending_completion,
            }
        except asyncio.CancelledError:
            self._abort_tool_worker(worker, tool=tool, context=context)
            raise

    async def _await_pending_tool_completions(
        self,
        lock_keys: list[str] | tuple[str, ...],
        *,
        tool: ToolDefinition,
    ) -> None:
        while True:
            pending = self._pending_tool_completions_for_current_loop()
            waits = {future for key in lock_keys if (future := pending.get(key)) is not None and not future.done()}
            if not waits:
                return
            record(
                "tool.waiting_for_pending_completion",
                "ToolRuntime",
                {"tool": tool.name, "lock_keys": lock_keys, "pending": len(waits)},
            )
            await asyncio.gather(*(asyncio.shield(future) for future in waits), return_exceptions=True)

    def _remember_pending_tool_completion(
        self,
        lock_keys: list[str] | tuple[str, ...],
        worker: asyncio.Future[Any],
        *,
        tool: ToolDefinition,
        reason: str,
    ) -> None:
        if not lock_keys or worker.done():
            return
        pending = self._pending_tool_completions_for_current_loop()
        keys = tuple(lock_keys)
        for key in keys:
            pending[key] = worker
        record(
            "tool.pending_completion_registered",
            "ToolRuntime",
            {"tool": tool.name, "reason": reason, "lock_keys": list(keys)},
        )

        def release_completion(done: asyncio.Future[Any]) -> None:
            try:
                done.result()
            except BaseException as exc:  # noqa: BLE001
                logger.debug("timed-out tool %s finished with %s", tool.name, type(exc).__name__, exc_info=True)
            for key in keys:
                if pending.get(key) is done:
                    pending.pop(key, None)

        worker.add_done_callback(release_completion)

    def _abort_tool_worker(
        self,
        worker: _ToolWorkerHandle,
        *,
        tool: ToolDefinition,
        context: dict[str, Any],
    ) -> None:
        worker.abandoned = True
        worker.abort_event.set()
        runtime = context.get("runtime")
        if runtime is not None and hasattr(runtime, "abort_requested"):
            runtime.abort_requested = True
        record(
            "tool.worker_abort_requested",
            "ToolRuntime",
            {"tool": tool.name, "future_done": worker.future.done()},
        )

    def _start_daemon_tool_worker(
        self,
        tool: ToolDefinition,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> _ToolWorkerHandle:
        if not _TOOL_THREAD_SLOTS.acquire(blocking=False):
            raise RuntimeError(
                f"Tool worker capacity exhausted ({_MAX_DAEMON_TOOL_THREADS} in-flight sync tools); "
                "retry after pending tool executions finish."
            )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        abort_event = threading.Event()
        context["_tool_abort_event"] = abort_event
        handle = _ToolWorkerHandle(future=future, abort_event=abort_event)

        def complete_with_result(result: Any) -> None:
            if handle.abandoned or abort_event.is_set():
                return
            if not future.done():
                future.set_result(result)

        def complete_with_error(exc: BaseException) -> None:
            if handle.abandoned or abort_event.is_set():
                return
            if not future.done():
                future.set_exception(exc)

        def finish(callback: Any, *callback_args: Any) -> None:
            try:
                loop.call_soon_threadsafe(callback, *callback_args)
            except RuntimeError:
                logger.debug("tool worker %s finished after its event loop closed", tool.name, exc_info=True)

        def run_tool() -> None:
            try:
                if handle.abandoned or abort_event.is_set():
                    return
                result = tool.execute(args, context)
                if handle.abandoned or abort_event.is_set():
                    record(
                        "tool.worker_result_discarded",
                        "ToolRuntime",
                        {"tool": tool.name},
                    )
                    return
                finish(complete_with_result, result)
            except BaseException as exc:  # noqa: BLE001 - propagate tool crashes to the awaiting task.
                if handle.abandoned or abort_event.is_set():
                    return
                finish(complete_with_error, exc)
            finally:
                _TOOL_THREAD_SLOTS.release()

        thread = threading.Thread(target=run_tool, name=f"tool-{tool.name}", daemon=True)
        thread.start()
        return handle

    def _tool_execution_timeout(self, context: dict[str, Any]) -> float:
        settings = context.get("settings")
        if settings is not None:
            configured = getattr(settings, "tool_timeout_seconds", None)
            if configured is not None:
                return max(1.0, float(configured))
        configured = getattr(get_effective_settings(), "tool_timeout_seconds", None)
        if configured is not None:
            return max(1.0, float(configured))
        return _DEFAULT_TOOL_TIMEOUT_SECONDS

    def _locks_for_current_loop(self) -> dict[str, asyncio.Lock]:
        loop = asyncio.get_running_loop()
        locks = _SHARED_PATH_LOCKS.get(loop)
        if locks is None:
            locks = {}
            _SHARED_PATH_LOCKS[loop] = locks
        return locks

    def _pending_tool_completions_for_current_loop(self) -> dict[str, asyncio.Future[Any]]:
        loop = asyncio.get_running_loop()
        pending = _SHARED_PENDING_TOOL_COMPLETIONS.get(loop)
        if pending is None:
            pending = {}
            _SHARED_PENDING_TOOL_COMPLETIONS[loop] = pending
        return pending
