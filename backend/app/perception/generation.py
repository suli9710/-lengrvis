"""Bind local perception snapshots to the data they observed, without serializing it."""

from __future__ import annotations

from typing import Any

from app.core.local_data_lifecycle import data_generation, local_data_lock
from app.core.privacy_erase_state import erase_is_reserved


def capture_generation() -> tuple[str, int]:
    with local_data_lock:
        identity, generation = data_generation()
        # A snapshot captured inside an unfinished erase must never be replayed
        # after the reservation releases, even if backend deletion already ran.
        return identity, -1 if erase_is_reserved() else generation


def source_generation(source: Any) -> tuple[str, int]:
    current = data_generation()
    generation = getattr(source, "_data_generation", current)
    if generation != current:
        return generation
    for name in ("screen_state", "app_context"):
        child = getattr(source, name, None)
        if child is not None:
            child_generation = source_generation(child)
            if child_generation != current:
                return child_generation
    return generation
