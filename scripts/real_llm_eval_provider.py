"""Provider selection and fail-fast checks for the real-LLM evaluation harness."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import httpx


def _provider_config_failure_reason(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return "cloud provider rejected the configured authentication"
        if status_code == 404:
            return "cloud provider endpoint or configured model was not found"
        if status_code == 429:
            return "cloud provider rate limit prevented the generation probe"
        if status_code in {502, 503, 504}:
            return "cloud provider endpoint is reachable but its model upstream is unavailable"
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
        return "configured cloud provider is not reachable"
    message = str(exc).lower()
    if "could not be resolved" in message:
        return "configured base URL hostname could not be resolved"
    if "base url" in message and "required" in message:
        return "configured base URL is required"
    if "loopback" in message or "private" in message or "ssrf" in message:
        return "configured base URL targets loopback/private/link-local/metadata hosts"
    if "absolute http" in message:
        return "configured base URL is not an absolute http(s) URL"
    if "api_key" in message:
        return "cloud provider is missing an API key"
    if "unsupported cloud provider" in message:
        return "configured provider is not supported for cloud routing"
    if "structured" in message or "planner contract probe" in message:
        return "cloud provider could not produce the required structured planner output"
    return f"{type(exc).__name__} while validating provider configuration"


def _provider_config_exit_message(exc: BaseException) -> str:
    return "\n".join(
        [
            "real-llm-eval: real provider preflight failed.",
            f"Reason: {_provider_config_failure_reason(exc)}.",
            (
                "The real LLM quality gate requires a non-mock provider with a "
                "non-private cloud/OpenAI-compatible base URL and API key before "
                "any golden tasks run."
            ),
            (
                "Configure LENGRVIS_PROVIDER_NAME, LENGRVIS_BASE_URL=https://..., "
                "LENGRVIS_API_KEY, LENGRVIS_MODEL, and LENGRVIS_MODE=efficiency "
                "(or matching config.yaml/.env values)."
            ),
            (
                "Loopback, private/LAN, link-local, and metadata hosts are blocked "
                "by the SSRF guard for this gate."
            ),
            "Secrets and configured URL values are intentionally omitted from this diagnostic.",
        ]
    )


def _local_provider_failure_reason(exc: BaseException) -> str:
    message = str(exc).lower()
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return "local provider rejected the configured authentication"
        if status_code == 404:
            return "local provider does not expose a compatible generation endpoint"
        if status_code in {502, 503, 504}:
            return "local provider endpoint is reachable but its model upstream is unavailable"
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
        return "configured local provider is not reachable"
    if "non-local base_url" in message:
        return "configured local provider base URL is not local"
    if "unable to load onnx" in message:
        return "configured ONNX local model could not be loaded"
    if "onnx text generation failed" in message:
        return "local ONNX provider failed during generation"
    if "empty probe response" in message:
        return "local provider returned an empty generation probe"
    if any(
        marker in message for marker in ("401", "403", "unauthorized", "authentication")
    ):
        return "local provider rejected the configured authentication"
    if "404" in message or "not found" in message:
        return "local provider does not expose a compatible generation endpoint"
    if any(
        marker in message
        for marker in (
            "all connection attempts failed",
            "connection refused",
            "connecterror",
            "could not be resolved",
            "network is unreachable",
            "timed out",
        )
    ):
        return "configured local provider is not reachable"
    if "privacy mode requires" in message or "local llm backend" in message:
        return "no reachable local LLM backend was detected"
    if "structured" in message or "planner contract probe" in message:
        return "local provider could not produce the required structured planner output"
    return f"{type(exc).__name__} while probing the local provider"


def _local_provider_exit_message(exc: BaseException) -> str:
    return "\n".join(
        [
            "real-llm-eval: local provider preflight failed.",
            f"Reason: {_local_provider_failure_reason(exc)}.",
            (
                "Privacy-compatible real LLM evaluation tasks can run against a "
                "local provider, but the configured local backend was unavailable "
                "or rejected before any golden tasks ran."
            ),
            (
                "Start Ollama, LM Studio, a llama.cpp-compatible local server, "
                "or configure an ONNX local model."
            ),
            (
                "Configure LENGRVIS_PROVIDER_NAME=ollama/lmstudio/llamacpp/onnx, "
                "LENGRVIS_BASE_URL for the local service when needed, "
                "LENGRVIS_MODEL, and LENGRVIS_MODE=privacy."
            ),
            "Secrets and configured URL values are intentionally omitted from this diagnostic.",
        ]
    )


def _should_report_local_provider_failure(settings: Any) -> bool:
    from app.llm.registry import LOCAL_PROVIDERS

    provider_name = (settings.provider_name or "").lower()
    mode = (settings.mode or "efficiency").lower()
    return mode == "privacy" or provider_name in LOCAL_PROVIDERS


def _validate_real_provider_preflight(settings: Any) -> None:
    from app.core.outbound_url import validate_outbound_http_url
    from app.llm.registry import CLOUD_PROVIDERS

    mode = (settings.mode or "efficiency").lower()
    if mode == "privacy":
        return
    provider_name = (settings.provider_name or "").lower()
    if provider_name not in CLOUD_PROVIDERS:
        return
    base_url = str(settings.base_url or "").strip()
    if not base_url:
        raise ValueError(
            "configured base URL is required for cloud/OpenAI-compatible real LLM eval."
        )
    validate_outbound_http_url(base_url, allow_private=False)


def _effective_task_mode(task: dict[str, Any], default_mode: str | None) -> str:
    task_mode = str(task.get("mode") or "").strip().casefold()
    fallback = str(default_mode or "efficiency").strip().casefold()
    return task_mode or fallback or "efficiency"


def _privacy_incompatible_task_capabilities(
    tasks: list[dict[str, Any]], default_mode: str | None
) -> set[str]:
    required: set[str] = set()
    for task in tasks:
        if _effective_task_mode(task, default_mode) != "privacy":
            continue
        expect = task.get("expect") or {}
        phases = expect.get("phase") or []
        if isinstance(phases, str):
            phases = [phases]
        normalized_phases = {str(phase).strip().casefold() for phase in phases}
        if "completed" not in normalized_phases or normalized_phases.intersection(
            {"denied", "awaiting_approval"}
        ):
            continue
        tools = expect.get("plan_tools") or expect.get("task_plan_tools") or []
        if any(str(tool_name).startswith("browser.") for tool_name in tools):
            required.add("browser network")
    return required


def _task_capability_exit_message(capabilities: set[str]) -> str:
    labels = ", ".join(sorted(capabilities))
    return "\n".join(
        [
            "real-llm-eval: task capability preflight failed.",
            f"Reason: the selected task set requires {labels}, which privacy mode forbids.",
            (
                "Configure LENGRVIS_MODE=efficiency with a real cloud provider, "
                "or select only privacy-compatible tasks."
            ),
            "No benchmark task ran and no quality report was written.",
            "Task identifiers, prompts, secrets, and configured URL values are intentionally omitted.",
        ]
    )


def _local_probe_settings(settings: Any) -> Any:
    timeout = min(max(int(getattr(settings, "timeout", 30) or 30), 1), 30)
    return settings.model_copy(
        update={
            "allow_mock_fallback": False,
            "llm_api_max_retries": 0,
            "structured_output_repair_retries": 0,
            "max_tokens": 128,
            "temperature": 0,
            "timeout": timeout,
        }
    )


def _probe_local_provider(provider: Any) -> None:
    async def probe() -> None:
        from app.agents.planner_agent import PLAN_SCHEMA, PlannerAgent
        from app.llm.openai_compatible import close_shared_http_client
        from pydantic import ValidationError

        try:
            response = await provider.structured_chat(
                [
                    {
                        "role": "user",
                        "content": (
                            "Return one read-only planner capability-probe step for "
                            "system.diagnostics, with all schema-required fields."
                        ),
                    }
                ],
                PLAN_SCHEMA,
            )
        finally:
            with suppress(Exception):
                await close_shared_http_client()
        if not isinstance(response, dict):
            raise RuntimeError(
                "provider planner contract probe returned invalid structured output"
            )
        try:
            PlannerAgent()._payload_to_plan("provider-capability-probe", response)
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(
                "provider planner contract probe returned runtime-invalid structured output"
            ) from exc

    asyncio.run(probe())


def _probe_cloud_provider(provider: Any) -> None:
    _probe_local_provider(provider)


def _require_real_provider(
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from app.llm.local_provider import LocalBackendUnavailable
    from app.llm.mock_provider import MockProvider
    from app.llm.registry import get_effective_settings, get_provider

    settings = get_effective_settings()
    if (settings.provider_name or "").lower() == "mock":
        raise SystemExit(
            "real-llm-eval refuses to run with provider_name=mock; configure a real provider first."
        )
    selected_tasks = tasks if tasks is not None else [{}]
    required_capabilities = _privacy_incompatible_task_capabilities(
        selected_tasks, settings.mode
    )
    if required_capabilities:
        raise SystemExit(_task_capability_exit_message(required_capabilities))
    evaluated_modes = sorted(
        {_effective_task_mode(task, settings.mode) for task in selected_tasks}
    )
    probed_local_modes: list[str] = []
    probed_cloud_modes: list[str] = []
    for mode in evaluated_modes:
        mode_settings = settings.model_copy(
            update={"allow_mock_fallback": False, "mode": mode}
        )
        is_local_route = _should_report_local_provider_failure(mode_settings)
        mode_settings = _local_probe_settings(mode_settings)
        try:
            _validate_real_provider_preflight(mode_settings)
        except ValueError as exc:
            raise SystemExit(_provider_config_exit_message(exc)) from None
        try:
            provider = get_provider(mode_settings, task="planner")
        except LocalBackendUnavailable as exc:
            if is_local_route:
                raise SystemExit(_local_provider_exit_message(exc)) from None
            raise SystemExit(_provider_config_exit_message(exc)) from None
        resolved_provider = provider
        seen_provider_ids: set[int] = set()
        while id(resolved_provider) not in seen_provider_ids:
            seen_provider_ids.add(id(resolved_provider))
            if isinstance(resolved_provider, MockProvider):
                break
            nested_provider = getattr(resolved_provider, "provider", None)
            if nested_provider is None:
                break
            resolved_provider = nested_provider
        if isinstance(resolved_provider, MockProvider):
            raise SystemExit(
                "real-llm-eval resolved MockProvider; configure LENGRVIS_API_KEY / a local backend first."
            )
        try:
            if is_local_route:
                _probe_local_provider(provider)
                probed_local_modes.append(mode)
            else:
                _probe_cloud_provider(provider)
                probed_cloud_modes.append(mode)
        except Exception as exc:  # noqa: BLE001 - fail-fast provider boundary.
            if is_local_route:
                raise SystemExit(_local_provider_exit_message(exc)) from None
            raise SystemExit(_provider_config_exit_message(exc)) from None
    return {
        "provider_name": settings.provider_name,
        "model": settings.model,
        "mode": settings.mode,
        "evaluated_modes": evaluated_modes,
        "probed_local_modes": probed_local_modes,
        "probed_cloud_modes": probed_cloud_modes,
        "wire_api": getattr(settings, "wire_api", ""),
    }
