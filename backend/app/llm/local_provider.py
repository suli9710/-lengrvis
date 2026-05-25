"""Local LLM backend detection (Ollama / LM Studio / llama.cpp).

Privacy mode routes to a real local model and fails clearly when none is
available. This module probes the well-known local endpoints and returns a
backend descriptor that the registry can wire into an OpenAI-compatible
provider.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import httpx

from app.config import AppSettings
from app.core.schemas import LocalLLMHealth
from app.llm.onnx_provider import detect_onnx_backend, health_snapshot as onnx_health_snapshot


@dataclass(slots=True)
class LocalBackend:
    kind: str  # "ollama" | "lmstudio" | "llamacpp"
    base_url: str
    models: list[str]


class LocalBackendUnavailable(RuntimeError):
    """Raised when privacy mode is requested but no local backend is reachable."""


_DEFAULT_TIMEOUT = 1.5


_PROBES: list[tuple[str, str, str]] = [
    # (kind, probe_url, openai_compatible_base_url)
    ("ollama", "http://127.0.0.1:11434/api/tags", "http://127.0.0.1:11434/v1"),
    ("lmstudio", "http://127.0.0.1:1234/v1/models", "http://127.0.0.1:1234/v1"),
    ("llamacpp", "http://127.0.0.1:8080/v1/models", "http://127.0.0.1:8080/v1"),
]

PROBE_ORDER = ["onnx", *[kind for kind, _, _ in _PROBES]]


def _extract_models(kind: str, payload: dict) -> list[str]:
    if not isinstance(payload, dict):
        return []
    if kind == "ollama":
        return [str(item.get("name") or item.get("model") or "") for item in payload.get("models", []) if item]
    data = payload.get("data")
    if isinstance(data, list):
        return [str(entry.get("id") or "") for entry in data if isinstance(entry, dict)]
    return []


def detect_local_backend(*, timeout: float = _DEFAULT_TIMEOUT, client_factory=None) -> LocalBackend | None:
    """Probe known local LLM backends. Returns the first one reachable, or None.

    `client_factory` is injected by tests to mock httpx; defaults to httpx.Client.
    """
    factory = client_factory or (lambda: httpx.Client(timeout=timeout))
    for kind, probe_url, base_url in _PROBES:
        try:
            with factory() as client:
                response = client.get(probe_url)
        except Exception:
            continue
        if response.status_code != 200:
            continue
        try:
            payload = response.json()
        except Exception:
            payload = {}
        models = [name for name in _extract_models(kind, payload) if name]
        return LocalBackend(kind=kind, base_url=base_url, models=models)
    return None


def unavailable_message() -> str:
    probes = ", ".join(f"{kind} ({url})" for kind, url, _ in _PROBES)
    return (
        "Privacy mode requires a reachable local LLM backend. "
        f"Tried {probes}. Start Ollama, LM Studio, or a llama.cpp-compatible "
        "OpenAI server, then retry."
    )


def health_snapshot(settings: AppSettings | None = None, *, timeout: float = _DEFAULT_TIMEOUT) -> dict:
    """JSON-serialisable summary for `/api/settings/local-llm/health`."""
    onnx_backend = detect_onnx_backend(settings) if settings is not None else detect_onnx_backend()
    onnx_snapshot = onnx_health_snapshot(settings) if settings is not None else onnx_health_snapshot()
    if onnx_backend is not None:
        selected = dataclasses.asdict(onnx_backend)
        return LocalLLMHealth(
            available=True,
            selected_backend=selected,
            probe_order=PROBE_ORDER,
        ).model_dump() | selected | {"onnx": onnx_snapshot}

    backend = detect_local_backend(timeout=timeout)
    if backend is None:
        return LocalLLMHealth(
            available=False,
            selected_backend=None,
            probe_order=PROBE_ORDER,
            error=unavailable_message(),
        ).model_dump() | {"onnx": onnx_snapshot}
    backend_dict = dataclasses.asdict(backend)
    selected = {
        **backend_dict,
        "model": backend.models[0] if backend.models else "",
    }
    return LocalLLMHealth(
        available=True,
        selected_backend=selected,
        probe_order=PROBE_ORDER,
    ).model_dump() | backend_dict | {"onnx": onnx_snapshot}
