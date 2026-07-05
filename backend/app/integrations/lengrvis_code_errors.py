from __future__ import annotations

from typing import Any, Protocol

from app.integrations.lengrvis_code_constants import (
    ERROR_BAD_NDJSON,
    ERROR_CANCELLED,
    ERROR_LAUNCH_FAILURE,
    ERROR_LENGRVIS_RESULT,
    ERROR_NON_ZERO_EXIT,
    ERROR_PERMISSION_DENIAL,
)


class LengrvisCodeErrorSummaryLike(Protocol):
    cancelled: bool
    launch_error: str
    result: dict[str, Any] | None
    returncode: int | None
    invalid_lines: list[str]

    @property
    def permission_denials(self) -> list[Any]: ...


def classify_lengrvis_code_error(summary: LengrvisCodeErrorSummaryLike) -> str | None:
    if summary.cancelled:
        return ERROR_CANCELLED
    if summary.launch_error:
        return ERROR_LAUNCH_FAILURE
    if summary.permission_denials:
        return ERROR_PERMISSION_DENIAL
    if summary.result and summary.result.get("is_error"):
        return ERROR_LENGRVIS_RESULT
    if summary.result and summary.result.get("is_error") is False:
        return None
    if summary.returncode not in {None, 0}:
        return ERROR_NON_ZERO_EXIT
    if summary.invalid_lines:
        return ERROR_BAD_NDJSON
    return None
