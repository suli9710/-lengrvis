"""Coordinate local-content writers with the explicit privacy erase boundary."""

from __future__ import annotations

import inspect
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps

from app.core.errors import AppError
from app.core.privacy_erase_state import erase_is_reserved

local_data_lock = threading.RLock()
_active: dict[str, int] = defaultdict(int)
_generations: dict[str, int] = defaultdict(int)


def data_identity() -> str:
    from app.core import db

    return str(db.db_path().resolve())


def data_generation() -> tuple[str, int]:
    with local_data_lock:
        key = data_identity()
        return key, _generations[key]


def advance_data_generation() -> None:
    with local_data_lock:
        _generations[data_identity()] += 1


@contextmanager
def local_data_operation(*, expected_generation: tuple[str, int] | None = None) -> Iterator[bool]:
    # Do not hold a thread lock across an await. The counter covers the whole
    # operation, while the shared lock atomically excludes a concurrent erase.
    with local_data_lock:
        key = data_identity()
        current = expected_generation is None or expected_generation == (key, _generations[key])
        if current and erase_is_reserved():
            if expected_generation is None:
                raise AppError("local_data_erasing", "本机数据清除尚未完成，请在隐私设置中重试。", 409)
            current = False
        if current:
            _active[key] += 1
    try:
        # Deferred writers bind to their enqueue generation. Checking it and
        # claiming the operation must be atomic with respect to erase.
        yield current
    finally:
        if current:
            with local_data_lock:
                _active[key] -= 1


def tracks_local_data(function):
    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_wrapper(*args, **kwargs):
            with local_data_operation():
                return await function(*args, **kwargs)

        return async_wrapper

    @wraps(function)
    def wrapper(*args, **kwargs):
        with local_data_operation():
            return function(*args, **kwargs)

    return wrapper


@contextmanager
def erasing_local_data(*, has_active_work: Callable[[], bool], reserved: bool = False) -> Iterator[None]:
    with local_data_lock:
        if not reserved and erase_is_reserved():
            raise AppError("local_data_erasing", "本机数据清除尚未完成，请在隐私设置中重试。", 409)
        if _active[data_identity()] or has_active_work():
            raise AppError("local_data_busy", "有任务或索引操作正在运行，请先停止后再删除本地数据。", 409)
        yield


def reserve_local_data_erase(*, include_settings: bool, confirmation_id: str, has_active_work: Callable[[], bool]):
    from app.core.privacy_erase_state import reserve_erase

    with local_data_lock:
        if _active[data_identity()] or has_active_work():
            raise AppError("local_data_busy", "有任务或索引操作正在运行，请先停止后再删除本地数据。", 409)
        return reserve_erase(include_settings=include_settings, confirmation_id=confirmation_id)


class LocalDataWritesMiddleware:
    """Cover request-to-worker handoffs as well as ordinary HTTP mutations."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") == "http"
            and scope.get("method") in {"POST", "PUT", "PATCH", "DELETE"}
            and scope.get("path", "").rstrip("/")
            not in {
                "/api/system/privacy/erase-local-data",
                "/api/system/privacy/erase-local-data/native-confirmation-challenge",
                "/api/system/privacy/erase-local-data/prepare",
                "/api/system/privacy/erase-local-data/complete",
            }
        ):
            try:
                with local_data_operation():
                    await self.app(scope, receive, send)
            except AppError as exc:
                from fastapi.responses import JSONResponse

                from app.core.errors import unified_error_body

                response = JSONResponse(unified_error_body(exc.message, code=exc.code), status_code=exc.status_code)
                await response(scope, receive, send)
        else:
            await self.app(scope, receive, send)
