"""Process-wide resource ownership across event loops and tool threads."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from uuid import uuid4

from app.tools.tool_abort import raise_if_tool_aborted

_guard = threading.Lock()
_owners: dict[str, tuple[str, int]] = {}
_owner_context: ContextVar[str | None] = ContextVar("resource_lease_owner", default=None)


def _claim(keys: tuple[str, ...], owner: str) -> bool:
    with _guard:
        if any(key in _owners and _owners[key][0] != owner for key in keys):
            return False
        for key in keys:
            _owners[key] = (owner, _owners.get(key, (owner, 0))[1] + 1)
        return True


def _release(keys: tuple[str, ...], owner: str) -> None:
    with _guard:
        for key in keys:
            held = _owners.get(key)
            if held and held[0] == owner:
                if held[1] == 1:
                    del _owners[key]
                else:
                    _owners[key] = (owner, held[1] - 1)


@contextmanager
def hold_resource_lease(keys, context=None):
    keys = tuple(sorted(set(keys)))
    owner = _owner_context.get() or uuid4().hex
    while not _claim(keys, owner):
        raise_if_tool_aborted(context)
        time.sleep(0.025)
    token = _owner_context.set(owner)
    try:
        raise_if_tool_aborted(context)
        yield
    finally:
        _owner_context.reset(token)
        _release(keys, owner)


@asynccontextmanager
async def hold_async_resource_lease(keys):
    keys = tuple(sorted(set(keys)))
    owner = _owner_context.get() or uuid4().hex
    while not _claim(keys, owner):
        await asyncio.sleep(0.025)
    token = _owner_context.set(owner)
    try:
        yield
    finally:
        _owner_context.reset(token)
        _release(keys, owner)
