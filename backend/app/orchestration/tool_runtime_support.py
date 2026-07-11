from __future__ import annotations

import asyncio
import copy
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.content_provenance import propagate_content_envelope
from app.core.schemas import PlanStep, SafetyReview, ToolResult
from app.orchestration.runtime_context import TaskRuntimeContext
from app.policy.redaction import REDACTED, contains_sensitive_key, redact_public_text, redact_value

logger = logging.getLogger(__name__)

_DEFAULT_TOOL_TIMEOUT_SECONDS = 300.0
_MAX_DAEMON_TOOL_THREADS = 32
_TOOL_THREAD_SLOTS = threading.BoundedSemaphore(_MAX_DAEMON_TOOL_THREADS)


# Failure strings that give the reflection layer nothing to reason about
# (see os_reflection._is_low_information_failure). Tool failures passing
# through the runtime are enriched so automated recovery stays possible
# instead of degrading to ask_user.
_LOW_INFORMATION_ERRORS = {"", "planned failure", "tool failed.", "failed", "unknown error"}


def _actionable_error_text(raw_error: str, step: PlanStep) -> str:
    """Ensure a tool-declared error string carries actionable context."""
    text = str(raw_error or "").strip()
    if text.casefold() not in _LOW_INFORMATION_ERRORS:
        return text
    args_hint = ", ".join(sorted((step.args or {}).keys())) or "none"
    base = text or "Tool reported a failure without details"
    return f"{base} (tool={step.tool_name}, args keys: {args_hint}). Verify the arguments or choose another tool."


def _exception_error_text(exc: BaseException, step: PlanStep) -> str:
    """Build a non-empty, typed error string for unexpected tool exceptions."""
    detail = _safe_runtime_error_text(exc) or "no exception message"
    return f"{type(exc).__name__}: {detail} (tool={step.tool_name})"


def _safe_runtime_error_text(value: Any) -> str:
    return _message_safe_text(str(redact_value(str(value or "")) or ""))


@dataclass(slots=True)
class RuntimeExecutionResult:
    kind: str
    result: ToolResult | None = None


def _withheld_tool_result(result: ToolResult, review: SafetyReview, runtime: TaskRuntimeContext) -> ToolResult:
    reason = review.safe_alternative or "Tool result was withheld by SafetyReviewAgent."
    _discard_persisted_result(result, runtime)
    return _withheld_result_stub(
        result,
        reason=reason,
        review_id=review.id,
        review_verdict=review.verdict.value,
    )


def _withheld_result_stub(
    result: ToolResult,
    *,
    reason: str,
    review_id: str = "",
    review_verdict: str = "",
) -> ToolResult:
    output: dict[str, Any] = {
        "ok": False,
        "withheld": True,
        "reason": reason,
    }
    if review_id:
        output["post_tool_review_id"] = review_id
    if review_verdict:
        output["post_tool_review_verdict"] = review_verdict
    content_envelope = (
        propagate_content_envelope(result.content_envelope, output, sanitizer="safety_review_withhold")
        if result.content_envelope is not None
        else None
    )
    return ToolResult(
        id=result.id,
        tool_call_id=result.tool_call_id,
        ok=False,
        output=output,
        error=reason,
        observation="Tool result was withheld by SafetyReviewAgent.",
        content_envelope=content_envelope,
    )


def _discard_persisted_result(result: ToolResult, runtime: TaskRuntimeContext) -> None:
    runtime.large_results.pop(result.id, None)
    output = result.output if isinstance(result.output, dict) else {}
    if not output.get("persisted_result"):
        return
    path = str(output.get("path") or "").strip()
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete withheld large result file: %s", path)


_MESSAGE_SAFE_URL_KEYS = {"url", "final_url", "source_url", "target_url", "href"}
_MESSAGE_SAFE_ARTIFACT_KEYS = {"screenshot_url", "artifact_url"}
_MESSAGE_SAFE_IDENTIFIER_KEYS = {"id", "task_id", "step_id", "tool_call_id", "run_id"}


def _message_safe_tool_result(result: ToolResult) -> ToolResult:
    original_output = result.output if isinstance(result.output, dict) else {}
    output = _message_safe_value(copy.deepcopy(result.output))
    if isinstance(output, dict) and output.get("persisted_result") and original_output.get("path"):
        output["path"] = Path(str(original_output.get("path") or "")).name
    return result.model_copy(
        update={
            "output": output,
            "error": _message_safe_text(result.error),
            "observation": _message_safe_text(result.observation),
        },
        deep=True,
    )


def _message_safe_value(value: Any, *, key: str = "") -> Any:
    if key and contains_sensitive_key(key):
        return REDACTED if value is not None else value
    if isinstance(value, dict):
        return {str(item_key): _message_safe_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_message_safe_value(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_message_safe_value(item, key=key) for item in value]
    if isinstance(value, set):
        return [_message_safe_value(item, key=key) for item in sorted(value, key=str)]
    if isinstance(value, str):
        normalized_key = key.replace("-", "_").casefold()
        if normalized_key in _MESSAGE_SAFE_ARTIFACT_KEYS:
            return _message_safe_artifact_ref(value)
        if normalized_key in _MESSAGE_SAFE_URL_KEYS:
            return _message_safe_url(value)
        return _message_safe_text(value, preserve_generic_tokens=normalized_key in _MESSAGE_SAFE_IDENTIFIER_KEYS)
    return value


def _message_safe_text(text: str, *, preserve_generic_tokens: bool = False) -> str:
    return redact_public_text(str(text or ""), redact_generic_tokens=not preserve_generic_tokens)


def _message_safe_url(value: str) -> str:
    text = _message_safe_text(value)
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and (parsed.query or parsed.fragment):
        query = "***" if parsed.query else ""
        return parsed._replace(query=query, fragment="").geturl()
    return text


def _message_safe_artifact_ref(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    candidate = parsed.path if parsed.scheme else text.split("?", 1)[0].split("#", 1)[0]
    return _message_safe_text(Path(candidate.replace("\\", "/")).name)


@dataclass(slots=True)
class _ToolWorkerHandle:
    future: asyncio.Future[Any]
    abort_event: threading.Event
    abandoned: bool = False


