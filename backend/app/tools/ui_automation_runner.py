"""Run UIAutomation adapters with bounded waits, cancellation, and metrics."""

from __future__ import annotations

import asyncio
import concurrent.futures
from contextlib import suppress
from typing import Any

from app.perception.ui_automation_observability import record_screenshot_capture_result
from app.tools.tool_abort import ToolAbortedError, raise_if_tool_aborted

DEFAULT_UI_AUTOMATION_TIMEOUT_SECONDS = 30.0


async def _with_timeout(coro, timeout_seconds: float | None, abort_context: dict[str, Any] | None) -> Any:
    task = asyncio.create_task(coro)
    loop = asyncio.get_running_loop()
    deadline = None if timeout_seconds is None or timeout_seconds <= 0 else loop.time() + timeout_seconds
    try:
        while True:
            raise_if_tool_aborted(abort_context)
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError
            wait_for = 0.05 if remaining is None else min(0.05, remaining)
            done, _ = await asyncio.wait({task}, timeout=wait_for)
            if task in done:
                return await task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def run_ui_automation(
    coro,
    action: str,
    *,
    timeout_seconds: float | None = DEFAULT_UI_AUTOMATION_TIMEOUT_SECONDS,
    abort_context: dict[str, Any] | None = None,
) -> Any:
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(_with_timeout(coro, timeout_seconds, abort_context))
        else:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = pool.submit(asyncio.run, _with_timeout(coro, timeout_seconds, abort_context))
            try:
                guard_timeout = None if timeout_seconds is None or timeout_seconds <= 0 else timeout_seconds + 1
                result = future.result(timeout=guard_timeout)
            finally:
                if not future.done():
                    future.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
    except TimeoutError:
        return record_screenshot_capture_result(
            action,
            {
                "ok": False,
                "error": f"UIAutomation {action} timed out.",
                "error_code": "ui_automation_timeout",
            },
            terminal="timeout",
        )
    except ToolAbortedError:
        record_screenshot_capture_result(action, None, terminal="aborted")
        raise
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: UIAutomation adapters should fail inline for tool callers.
        return record_screenshot_capture_result(
            action,
            {
                "ok": False,
                "error": f"UIAutomation {action} failed: {exc}",
                "error_code": "ui_automation_adapter_error",
            },
            terminal="exception",
        )
    return record_screenshot_capture_result(action, result)
