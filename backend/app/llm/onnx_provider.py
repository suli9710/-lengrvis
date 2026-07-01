"""Optional ONNX Runtime provider detection for local NPU acceleration.

The provider is deliberately optional: environments without DirectML/OpenVINO
continue to use the existing local HTTP backends from `local_provider.py`.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, AppSettings, get_env
from app.llm.base import LLMProvider
from app.llm.prompts import render_prompt
from app.llm.structured_output import (
    LLMStructuredOutputError,
    parse_and_validate_structured_content,
    safe_structured_excerpt,
)
from app.llm.structured_output import (
    check_output_schema as _check_structured_output_schema,
)
from app.llm.types import LLMResponse
from app.llm.usage import estimate_usage
from app.policy.redaction import redact_public_text, redact_value

_PREFERRED_EXECUTION_PROVIDERS = [
    ("onnx-winml", "WindowsMLExecutionProvider"),
    ("onnx-directml", "DmlExecutionProvider"),
    ("onnx-openvino", "OpenVINOExecutionProvider"),
    ("onnx-cpu", "CPUExecutionProvider"),
]
_EXECUTION_PROVIDER_ALIASES = {
    "winml": "WindowsMLExecutionProvider",
    "windowsml": "WindowsMLExecutionProvider",
    "windows_ml": "WindowsMLExecutionProvider",
    "winml_execution_provider": "WindowsMLExecutionProvider",
    "windowsml_execution_provider": "WindowsMLExecutionProvider",
    "windows_ml_execution_provider": "WindowsMLExecutionProvider",
    "winmlexecutionprovider": "WindowsMLExecutionProvider",
    "windowsmlexecutionprovider": "WindowsMLExecutionProvider",
    "directml": "DmlExecutionProvider",
    "dml": "DmlExecutionProvider",
    "dml_execution_provider": "DmlExecutionProvider",
    "dmlprovider": "DmlExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
    "openvino_execution_provider": "OpenVINOExecutionProvider",
    "cpu": "CPUExecutionProvider",
    "cpu_execution_provider": "CPUExecutionProvider",
}
_CONFIG_FILE_NAMES = ("genai_config.json", "config.json")
_MODEL_FILE_SUFFIXES = {".onnx", ".ort"}
_PREFERRED_MODEL_DIR_NAMES = (
    "Qwen2.5-3B-Instruct-ONNX",
    "qwen2.5-3b-instruct-onnx",
    "qwen2.5-3b-onnx",
    "qwen2.5-3b",
)
_MODEL_ROOT_ENV_KEYS = ("LENGRVIS_ONNX_MODELS_DIR", "LENGRVIS_ONNX_MODELS_DIR", "LENGRVIS_ONNX_MODELS_DIR")
_GENAI_RUNTIME_MODULES = ("onnxruntime_genai_winml", "onnxruntime_genai")
_RUNTIME_PACKAGE_MODULES = (
    "onnxruntime_genai_winml",
    "onnxruntime_windowsml",
    "onnxruntime_genai",
    "onnxruntime",
)
_WINML_PROVIDER = "WindowsMLExecutionProvider"
_QWEN_IM_START = "<|im_start|>"
_QWEN_IM_END = "<|im_" + "end|>"
_QWEN_REDACTED_IM_START = "<|redacted_im_start|>"
_QWEN_REDACTED_IM_END = "<|im_end|>"
_QWEN_TEMPLATE_DELIMITERS: tuple[tuple[str, str], ...] = (
    (_QWEN_REDACTED_IM_START, "<|redacted_redacted_im_start|>"),
    (_QWEN_REDACTED_IM_END, "<|redacted_redacted_im_end|>"),
    (_QWEN_IM_START, _QWEN_REDACTED_IM_START),
    (_QWEN_IM_END, _QWEN_REDACTED_IM_END),
)


def _sanitize_qwen_message_content(content: str) -> str:
    sanitized = content
    for raw, safe in _QWEN_TEMPLATE_DELIMITERS:
        sanitized = sanitized.replace(raw, safe)
    return sanitized


@dataclass(slots=True)
class OnnxBackend:
    kind: str
    model_path: str
    execution_provider: str
    available_providers: list[str]
    generation_runtime: str = "onnxruntime_genai"
    runtime_package: str = "onnxruntime_genai"
    model_family: str = ""
    provider_options: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _GenaiModelState:
    runtime: Any
    config: Any
    model: Any
    tokenizer: Any
    lock: threading.RLock = field(default_factory=threading.RLock)


_GENAI_MODEL_CACHE: dict[str, _GenaiModelState] = {}
_GENAI_MODEL_LOCKS: dict[str, threading.Lock] = {}
_GENAI_GLOBAL_LOCK = threading.Lock()


class OnnxProvider(LLMProvider):
    name = "onnx"

    def __init__(self, settings: AppSettings, backend: OnnxBackend) -> None:
        self.settings = settings
        self.backend = backend

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        prompt = self._format_messages(messages)
        # GenAI token generation is CPU/GPU-bound and holds state.lock for the
        # whole generation; run it off the event loop so the API stays live.
        return await asyncio.to_thread(
            self._generate_text,
            prompt,
            temperature=self.settings.temperature if temperature is None else temperature,
        )

    async def chat_result(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        content = await self.chat(messages, model=model, temperature=temperature, tools=tools)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=model or self.settings.model or self.backend.model_family or self.backend.kind,
            usage=estimate_usage(messages, content),
            metadata={
                "backend": self.backend.kind,
                "execution_provider": self.backend.execution_provider,
                "runtime_package": self.backend.runtime_package,
            },
        )

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        _check_structured_output_schema(output_schema)
        schema_prompt = {
            "role": "system",
            "content": render_prompt("structured_json_schema.md", {"schema": json.dumps(output_schema)}),
        }
        content = await self.chat([schema_prompt, *messages], temperature=0)
        return await self._parse_structured_content_with_repair(content, output_schema)

    async def _parse_structured_content_with_repair(
        self, content: str, output_schema: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return parse_and_validate_structured_content(content, output_schema)
        except LLMStructuredOutputError as exc:
            last_error = exc

        repair_content = content
        retries = max(0, int(getattr(self.settings, "structured_output_repair_retries", 1) or 0))
        if retries == 0:
            raise last_error
        for _attempt in range(retries):
            repair_prompt = {
                "role": "system",
                "content": render_prompt(
                    "structured_json_repair.md",
                    {
                        "schema": json.dumps(output_schema, ensure_ascii=False),
                        "failure_kind": last_error.failure_kind,
                        "output_excerpt": safe_structured_excerpt(repair_content),
                    },
                ),
            }
            repair_content = await self.chat([repair_prompt], temperature=0)
            try:
                return parse_and_validate_structured_content(repair_content, output_schema)
            except LLMStructuredOutputError as exc:
                last_error = exc

        raise LLMStructuredOutputError(
            f"LLM structured response could not be repaired ({last_error.failure_kind}).",
            last_error.failure_kind,
        ) from last_error

    def _generate_text(self, prompt: str, *, temperature: float) -> str:
        from app.llm.local_provider import LocalBackendUnavailable

        try:
            state = self._ensure_genai_model()
        except ImportError as exc:
            raise LocalBackendUnavailable(
                "ONNX acceleration is visible, but no ONNX Runtime GenAI package could be loaded for text generation. "
                "Install onnxruntime-genai-winml, onnxruntime-genai-directml, or an OpenVINO-capable GenAI runtime."
            ) from exc
        except Exception as exc:  # pragma: no cover - depends on optional native package
            raise LocalBackendUnavailable(f"Unable to load ONNX Runtime GenAI model: {exc}") from exc

        try:
            with state.lock:
                stream = state.tokenizer.create_stream()
                input_tokens = state.tokenizer.encode(prompt)
                params = state.runtime.GeneratorParams(state.model)
                params.set_search_options(
                    max_length=max(1, len(input_tokens)) + max(1, self.settings.max_tokens),
                    temperature=temperature,
                    batch_size=1,
                )
                generator = state.runtime.Generator(state.model, params)
                parts: list[str] = []
                generated = 0
                generator.append_tokens(input_tokens)
                while not generator.is_done() and generated < self.settings.max_tokens:
                    generator.generate_next_token()
                    token = generator.get_next_tokens()[0]
                    parts.append(stream.decode(token))
                    generated += 1
                return "".join(parts)
        except LocalBackendUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - depends on optional native package/model
            raise LocalBackendUnavailable(f"ONNX text generation failed: {exc}") from exc

    def _ensure_genai_model(self) -> _GenaiModelState:
        key = self._genai_cache_key()
        if key in _GENAI_MODEL_CACHE:
            return _GENAI_MODEL_CACHE[key]
        lock = _genai_model_lock(key)
        with lock:
            if key in _GENAI_MODEL_CACHE:
                return _GENAI_MODEL_CACHE[key]
            runtime = _import_genai_runtime(self.settings)
            config = runtime.Config(self._genai_model_path())
            self._configure_execution_provider(config)
            model = runtime.Model(config)
            tokenizer = runtime.Tokenizer(model)
            state = _GenaiModelState(runtime=runtime, config=config, model=model, tokenizer=tokenizer)
            _GENAI_MODEL_CACHE[key] = state
            return state

    def _genai_cache_key(self) -> str:
        return "|".join(
            [
                self._genai_model_path(),
                self.backend.execution_provider,
                self.backend.runtime_package,
                str(sorted(self.backend.provider_options.items())),
            ]
        )

    def _genai_model_path(self) -> str:
        from app.llm.local_provider import LocalBackendUnavailable

        path = _resolve_genai_model_path(Path(self.backend.model_path))
        if path is not None:
            return str(path)
        raise LocalBackendUnavailable(
            "ONNX GenAI text generation requires a model directory or config file with GenAI config and ONNX weights."
        )

    def _configure_execution_provider(self, config: Any) -> None:
        if hasattr(config, "clear_providers"):
            config.clear_providers()
        config.append_provider(self.backend.execution_provider)
        if not hasattr(config, "set_provider_option"):
            return
        for key, value in self.backend.provider_options.items():
            config.set_provider_option(key, value)

    def _format_messages(self, messages: list[dict[str, str]]) -> str:
        model_family = self.backend.model_family or _infer_model_family(Path(self.backend.model_path))
        if model_family.startswith("qwen"):
            return self._format_qwen_messages(messages)
        lines: list[str] = []
        for message in messages:
            role = str(message.get("role") or "user").strip().lower()
            if role not in {"system", "user", "assistant"}:
                role = "user"
            content = str(message.get("content") or "")
            if content:
                lines.append(f"{role}: {content}")
        lines.append("assistant:")
        return "\n".join(lines)

    def _format_qwen_messages(self, messages: list[dict[str, str]]) -> str:
        parts: list[str] = []
        for message in messages:
            role = str(message.get("role") or "user").strip().lower()
            content = str(message.get("content") or "")
            if not content:
                continue
            if role not in {"system", "user", "assistant"}:
                role = "user"
            content = _sanitize_qwen_message_content(content)
            parts.append(f"<|im_start|>{role}\n{content}{_QWEN_IM_END}")
        parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)


def detect_onnx_backend(
    settings: AppSettings | None = None,
    *,
    model_path: str | None = None,
) -> OnnxBackend | None:
    """Detect an ONNX Runtime backend using the configured or preferred EP."""
    candidate = _resolve_model_path(settings, model_path)
    if candidate is None:
        return None
    if not _genai_runtime_available(settings):
        return None
    runtime_package = _available_genai_runtime_package(settings) or "onnxruntime_genai"

    providers = _available_execution_providers()
    selected = _select_execution_provider(providers, settings)
    if selected is None:
        return None
    kind, execution_provider = selected
    visible_providers = providers if execution_provider in providers else [*providers, execution_provider]
    return OnnxBackend(
        kind=kind,
        model_path=str(candidate),
        execution_provider=execution_provider,
        available_providers=visible_providers,
        generation_runtime=runtime_package,
        runtime_package=runtime_package,
        model_family=_infer_model_family(candidate),
        provider_options=_provider_options(execution_provider, settings),
    )


def _select_execution_provider(providers: list[str], settings: AppSettings | None = None) -> tuple[str, str] | None:
    configured = _configured_execution_provider(settings)
    preferred = _normalize_execution_provider(configured)
    candidates = [(_kind_for_execution_provider(preferred), preferred)] if preferred else []
    candidates.extend(
        (kind, execution_provider)
        for kind, execution_provider in _preferred_execution_providers(settings)
        if execution_provider != preferred
    )
    provider_names = set(providers)
    for kind, execution_provider in candidates:
        if execution_provider in provider_names or _genai_reports_provider_available(kind):
            return kind, execution_provider
    return None


def _preferred_execution_providers(settings: AppSettings | None = None) -> list[tuple[str, str]]:
    raw = ""
    if settings is not None:
        raw = str(getattr(settings, "onnx_provider_preference", "") or "").strip()
    if not raw:
        raw = get_env("LENGRVIS_ONNX_PROVIDER_PREFERENCE", "") or ""
    if not raw:
        return list(_PREFERRED_EXECUTION_PROVIDERS)

    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token in raw.replace(";", ",").split(","):
        provider = _normalize_execution_provider(token)
        if provider is None or provider in seen:
            continue
        seen.add(provider)
        candidates.append((_kind_for_execution_provider(provider), provider))
    for kind, provider in _PREFERRED_EXECUTION_PROVIDERS:
        if provider not in seen:
            candidates.append((kind, provider))
    return candidates


def health_snapshot(settings: AppSettings | None = None, *, model_path: str | None = None) -> dict[str, Any]:
    raw_model_path = _configured_model_path(settings, model_path)
    candidate = _resolve_model_path(settings, model_path)
    runtime_packages = _runtime_packages_snapshot()
    genai_runtime = _available_genai_runtime_package(settings, runtime_packages=runtime_packages)
    genai_available = _genai_runtime_available(settings)
    if not genai_available:
        genai_runtime = ""
    if genai_available and not genai_runtime:
        genai_runtime = "onnxruntime_genai"
    providers = _available_execution_providers() if genai_available else []
    configured_provider = _configured_execution_provider(settings) or ""
    backend = detect_onnx_backend(settings, model_path=model_path)
    if backend is not None:
        payload = {"available": True, **asdict(backend)}
        return _with_status_details(
            payload,
            runtime_packages=runtime_packages,
            providers=providers,
            configured_provider=configured_provider,
            selected_provider=backend.execution_provider,
            errors=[],
        )

    error = _unavailable_reason(candidate, genai_available, providers, configured_path=raw_model_path)
    payload = {
        "available": False,
        "kind": "onnx",
        "model_path": str(candidate or raw_model_path or ""),
        "execution_provider": "",
        "available_providers": providers,
        "generation_runtime": genai_runtime or "",
        "runtime_package": genai_runtime or "",
        "error": error,
    }
    return _with_status_details(
        payload,
        runtime_packages=runtime_packages,
        providers=providers,
        configured_provider=configured_provider,
        selected_provider="",
        errors=[error],
    )


def warmup(settings: AppSettings | None = None, *, model_path: str | None = None) -> dict[str, Any]:
    """Load the configured ONNX GenAI model once and report structured smoke status."""
    settings = settings or AppSettings()
    snapshot = health_snapshot(settings, model_path=model_path)
    if not snapshot.get("available"):
        return _smoke_unavailable("warmup", snapshot)

    backend = detect_onnx_backend(settings, model_path=model_path)
    if backend is None:
        return _smoke_unavailable("warmup", snapshot)

    provider = OnnxProvider(settings, backend)
    try:
        provider._ensure_genai_model()
    except Exception as exc:  # noqa: BLE001 - native/runtime errors must be returned to callers.
        error = _safe_onnx_error(exc)
        return {
            "ok": False,
            "available": False,
            "status": "unavailable",
            "operation": "warmup",
            "error": error,
            "errors": [error],
            "llm": snapshot.get("llm", {}),
        }

    return {
        "ok": True,
        "available": True,
        "status": "ready",
        "operation": "warmup",
        "backend": asdict(backend),
        "llm": snapshot.get("llm", {}),
    }


async def test_generate(
    settings: AppSettings | None = None,
    *,
    prompt: str = "Say hello from ONNX.",
    max_tokens: int = 16,
    model_path: str | None = None,
) -> dict[str, Any]:
    """Run a tiny ONNX generation smoke test without surfacing dependency failures as 500s."""
    settings = settings or AppSettings()
    snapshot = health_snapshot(settings, model_path=model_path)
    if not snapshot.get("available"):
        return _smoke_unavailable("test_generate", snapshot)

    backend = detect_onnx_backend(settings, model_path=model_path)
    if backend is None:
        return _smoke_unavailable("test_generate", snapshot)

    smoke_settings = settings.model_copy(update={"max_tokens": max(1, min(int(max_tokens or 1), 64))})
    provider = OnnxProvider(smoke_settings, backend)
    try:
        text = await provider.chat([{"role": "user", "content": prompt or "Say hello from ONNX."}])
    except Exception as exc:  # noqa: BLE001 - route helper returns structured smoke status.
        error = _safe_onnx_error(exc)
        return {
            "ok": False,
            "available": False,
            "status": "unavailable",
            "operation": "test_generate",
            "error": error,
            "errors": [error],
            "llm": snapshot.get("llm", {}),
        }

    return {
        "ok": True,
        "available": True,
        "status": "ready",
        "operation": "test_generate",
        "message": text,
        "backend": asdict(backend),
        "llm": snapshot.get("llm", {}),
    }


def _with_status_details(
    payload: dict[str, Any],
    *,
    runtime_packages: dict[str, dict[str, Any]],
    providers: list[str],
    configured_provider: str,
    selected_provider: str,
    errors: list[str],
) -> dict[str, Any]:
    winml = _winml_snapshot(runtime_packages, providers)
    details = {
        "runtime": "onnx",
        "available": bool(payload.get("available")),
        "model_path": payload.get("model_path", ""),
        "configured_provider": configured_provider,
        "selected_provider": selected_provider,
        "runtime_packages": runtime_packages,
        "winml": winml,
        "errors": errors,
    }
    return {
        **payload,
        "configured_provider": configured_provider,
        "selected_provider": selected_provider,
        "runtime_packages": runtime_packages,
        "winml": winml,
        "errors": errors,
        "llm": details,
    }


def _smoke_unavailable(operation: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    errors = snapshot.get("errors") or ([snapshot["error"]] if snapshot.get("error") else [])
    return {
        "ok": False,
        "available": False,
        "status": "unavailable",
        "operation": operation,
        "error": errors[0] if errors else "ONNX runtime is unavailable.",
        "errors": errors,
        "llm": snapshot.get("llm", {}),
    }


def _genai_model_lock(key: str) -> threading.Lock:
    with _GENAI_GLOBAL_LOCK:
        if key not in _GENAI_MODEL_LOCKS:
            _GENAI_MODEL_LOCKS[key] = threading.Lock()
        return _GENAI_MODEL_LOCKS[key]


def _resolve_model_path(settings: AppSettings | None, model_path: str | None) -> Path | None:
    raw = _configured_model_path(settings, model_path)
    if not raw and settings is not None and _looks_like_onnx_model_reference(settings.model):
        raw = settings.model
    if not raw:
        return _discover_model_path(settings)
    return _resolve_raw_model_path(raw)


def _configured_model_path(settings: AppSettings | None, model_path: str | None = None) -> str | None:
    if model_path:
        return model_path
    if settings is not None:
        raw = str(getattr(settings, "onnx_model_path", "") or "").strip()
        if raw:
            return raw
    return get_env("LENGRVIS_ONNX_MODEL_PATH")


def _configured_execution_provider(settings: AppSettings | None = None) -> str | None:
    if settings is not None:
        raw = str(getattr(settings, "onnx_execution_provider", "") or "").strip()
        if raw:
            return raw
    return get_env("LENGRVIS_ONNX_EXECUTION_PROVIDER")


def _resolve_raw_model_path(raw: str) -> Path | None:
    path = Path(raw).expanduser()
    try:
        path = path.resolve(strict=False)
    except OSError:
        return None
    return _resolve_genai_model_path(path)


def _looks_like_onnx_model_reference(value: str | None) -> bool:
    if not value:
        return False
    lowered = str(value).lower()
    return lowered.endswith((".onnx", ".ort", "config.json")) or "onnx" in lowered


def _discover_model_path(settings: AppSettings | None) -> Path | None:
    for root in _candidate_model_roots(settings):
        found = _find_genai_model_dir(root)
        if found is not None:
            return found
    return None


def _candidate_model_roots(settings: AppSettings | None) -> list[Path]:
    roots: list[Path] = []
    for env_key in _MODEL_ROOT_ENV_KEYS:
        raw = get_env(env_key)
        if raw:
            roots.append(Path(raw).expanduser())
    if settings is not None and settings.data_dir:
        data_dir = Path(settings.data_dir).expanduser()
        roots.extend([data_dir / "models", data_dir])
    roots.extend(
        [
            PROJECT_ROOT / ".lengrvis_data" / "models",
            PROJECT_ROOT / ".lengrvis_data" / "models",
            PROJECT_ROOT / "models",
            PROJECT_ROOT / "backend" / "models",
        ]
    )
    return _unique_existing_paths(roots)


def _unique_existing_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            resolved = path
        key = str(resolved).lower()
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        result.append(resolved)
    return result


def _resolve_genai_model_path(path: Path) -> Path | None:
    if path.is_dir():
        if _is_genai_model_dir(path):
            return path
        return _find_genai_model_dir(path)
    if path.exists() and path.name in _CONFIG_FILE_NAMES and _directory_has_model_weights(path.parent):
        return path
    if path.exists() and path.suffix.lower() in _MODEL_FILE_SUFFIXES and _has_config_file(path.parent):
        return path.parent
    return None


def _find_genai_model_dir(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = [candidate for candidate in _iter_dirs(root, max_depth=4) if _is_genai_model_dir(candidate)]
    if not candidates:
        return None
    return sorted(candidates, key=_model_dir_sort_key)[0]


def _iter_dirs(root: Path, *, max_depth: int) -> Iterable[Path]:
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if not current.is_dir():
            continue
        yield current
        if depth >= max_depth:
            continue
        try:
            children = [child for child in current.iterdir() if child.is_dir()]
        except OSError:
            continue
        stack.extend((child, depth + 1) for child in children)


def _is_genai_model_dir(path: Path) -> bool:
    return _has_config_file(path) and _directory_has_model_weights(path)


def _has_config_file(path: Path) -> bool:
    return any((path / name).is_file() for name in _CONFIG_FILE_NAMES)


def _directory_has_model_weights(path: Path) -> bool:
    for candidate in _iter_files(path, max_depth=3):
        if candidate.suffix.lower() in _MODEL_FILE_SUFFIXES:
            return True
    return False


def _iter_files(root: Path, *, max_depth: int) -> Iterable[Path]:
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if not current.is_dir():
            continue
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_file():
                yield child
            elif child.is_dir() and depth < max_depth:
                stack.append((child, depth + 1))


def _model_dir_sort_key(path: Path) -> tuple[int, int, int, str]:
    lowered = str(path).lower()
    preferred_rank = next(
        (index for index, name in enumerate(_PREFERRED_MODEL_DIR_NAMES) if name.lower() in lowered),
        len(_PREFERRED_MODEL_DIR_NAMES),
    )
    if "qwen2.5" in lowered and "3b" in lowered:
        family_rank = 0
    elif "qwen" in lowered:
        family_rank = 1
    else:
        family_rank = 2
    quant_rank = 0 if any(token in lowered for token in ("int4", "q4", "quant")) else 1
    return family_rank, quant_rank, preferred_rank, str(path).lower()


def _infer_model_family(path: Path) -> str:
    lowered = str(path).lower()
    if "qwen2.5" in lowered:
        return "qwen2.5"
    if "qwen" in lowered:
        return "qwen"
    return ""


def _normalize_execution_provider(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    normalized = stripped.lower().replace("-", "_").replace(" ", "_")
    alias = _EXECUTION_PROVIDER_ALIASES.get(normalized)
    if alias:
        return alias
    if stripped.endswith("ExecutionProvider"):
        return stripped
    return None


def _kind_for_execution_provider(execution_provider: str) -> str:
    if execution_provider == _WINML_PROVIDER:
        return "onnx-winml"
    if execution_provider == "DmlExecutionProvider":
        return "onnx-directml"
    if execution_provider == "OpenVINOExecutionProvider":
        return "onnx-openvino"
    if execution_provider == "CPUExecutionProvider":
        return "onnx-cpu"
    return f"onnx-{execution_provider.lower().replace('executionprovider', '')}"


def _provider_options(execution_provider: str, settings: AppSettings | None = None) -> dict[str, str]:
    if execution_provider == "OpenVINOExecutionProvider":
        options: dict[str, str] = {}
        device = str(getattr(settings, "onnx_openvino_device", "") or "").strip() if settings is not None else ""
        cache_dir = str(getattr(settings, "onnx_openvino_cache_dir", "") or "").strip() if settings is not None else ""
        if not device:
            device = get_env("LENGRVIS_ONNX_OPENVINO_DEVICE", "") or ""
        if not cache_dir:
            cache_dir = get_env("LENGRVIS_ONNX_OPENVINO_CACHE_DIR", "") or ""
        if device and device.strip():
            options["device_type"] = device.strip()
        if cache_dir and cache_dir.strip():
            options["cache_dir"] = cache_dir.strip()
        return options
    if execution_provider != "DmlExecutionProvider":
        return {}
    device_id = str(getattr(settings, "onnx_directml_device_id", "") or "").strip() if settings is not None else ""
    if not device_id:
        device_id = get_env("LENGRVIS_ONNX_DIRECTML_DEVICE_ID", "") or ""
    if device_id is None or not device_id.strip():
        return {}
    return {"device_id": device_id.strip()}


def _import_genai_runtime(settings: AppSettings | None = None) -> Any:
    errors: list[str] = []
    for module_name in _genai_runtime_import_order(settings):
        try:
            return importlib.import_module(module_name)
        except ImportError as exc:
            errors.append(f"{module_name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - optional native package probe.
            errors.append(f"{module_name}: {exc}")
    raise ImportError("; ".join(errors) or "No ONNX Runtime GenAI package is importable.")


def _is_genai_runtime_available(settings: AppSettings | None = None) -> bool:
    try:
        _import_genai_runtime(settings)
    except ImportError:
        return False
    except Exception:  # noqa: BLE001 - optional native package probe.
        return False
    return True


def _genai_runtime_available(settings: AppSettings | None = None) -> bool:
    try:
        return _is_genai_runtime_available(settings)
    except TypeError:
        return _is_genai_runtime_available()


def _available_genai_runtime_package(
    settings: AppSettings | None = None,
    *,
    runtime_packages: dict[str, dict[str, Any]] | None = None,
) -> str:
    packages = runtime_packages or _runtime_packages_snapshot()
    for module_name in _genai_runtime_import_order(settings):
        if packages.get(module_name, {}).get("available"):
            return module_name
    return ""


def _genai_runtime_import_order(settings: AppSettings | None = None) -> tuple[str, ...]:
    configured = _configured_runtime(settings)
    if configured in {"winml", "windowsml"}:
        return ("onnxruntime_genai_winml", "onnxruntime_genai")
    if configured in {"genai", "default", "standard"}:
        return ("onnxruntime_genai", "onnxruntime_genai_winml")
    return _GENAI_RUNTIME_MODULES


def _configured_runtime(settings: AppSettings | None = None) -> str:
    raw = ""
    if settings is not None:
        raw = str(getattr(settings, "onnx_runtime", "") or "").strip()
    if not raw:
        raw = get_env("LENGRVIS_ONNX_RUNTIME", "") or ""
    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    return normalized if normalized else "auto"


def _runtime_packages_snapshot() -> dict[str, dict[str, Any]]:
    return {module_name: _runtime_package_snapshot(module_name) for module_name in _RUNTIME_PACKAGE_MODULES}


def _runtime_package_snapshot(module_name: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        return {"available": False, "module": module_name, "error": _safe_onnx_error(exc)}
    except Exception as exc:  # noqa: BLE001 - optional native package probe.
        return {"available": False, "module": module_name, "error": _safe_onnx_error(exc)}
    version = str(getattr(module, "__version__", "") or "")
    return {"available": True, "module": module_name, "version": version, "error": ""}


def _safe_onnx_error(value: Any) -> str:
    return redact_public_text(str(redact_value(str(value or "")) or "")) or value.__class__.__name__


def _winml_snapshot(runtime_packages: dict[str, dict[str, Any]], providers: list[str]) -> dict[str, Any]:
    package_names = ("onnxruntime_genai_winml", "onnxruntime_windowsml")
    available_packages = [name for name in package_names if runtime_packages.get(name, {}).get("available")]
    package_errors = {
        name: runtime_packages.get(name, {}).get("error", "")
        for name in package_names
        if runtime_packages.get(name, {}).get("error")
    }
    return {
        "available": bool(available_packages or _WINML_PROVIDER in providers),
        "provider": _WINML_PROVIDER,
        "provider_available": _WINML_PROVIDER in providers or bool(available_packages),
        "packages": available_packages,
        "errors": package_errors,
    }


def _available_execution_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return []
    except Exception:  # noqa: BLE001 - optional native package probe.
        return []
    try:
        return [str(item) for item in ort.get_available_providers()]
    except Exception:  # noqa: BLE001 - optional native package probe.
        return []


def _genai_reports_provider_available(kind: str) -> bool:
    if kind == "onnx-winml":
        return _winml_runtime_available()
    try:
        og = _import_genai_runtime()
    except ImportError:
        return False
    except Exception:  # noqa: BLE001 - optional native package probe.
        return False
    if kind == "onnx-directml" and hasattr(og, "is_dml_available"):
        try:
            return bool(og.is_dml_available())
        except Exception:  # noqa: BLE001 - optional native package probe.
            return False
    if kind == "onnx-openvino" and hasattr(og, "is_openvino_available"):
        try:
            return bool(og.is_openvino_available())
        except Exception:  # noqa: BLE001 - optional native package probe.
            return False
    return False


def _winml_runtime_available() -> bool:
    packages = _runtime_packages_snapshot()
    return any(packages.get(name, {}).get("available") for name in ("onnxruntime_genai_winml", "onnxruntime_windowsml"))


def _unavailable_reason(
    candidate: Path | None,
    genai_available: bool,
    providers: list[str],
    *,
    configured_path: str | None = None,
) -> str:
    if candidate is None:
        if configured_path:
            return (
                "Configured ONNX model path is not a usable GenAI model bundle. "
                "Use a directory or config file with genai_config.json/config.json and ONNX weights."
            )
        return (
            "No ONNX GenAI model path configured. Set LENGRVIS_ONNX_MODEL_PATH or place a Qwen2.5 ONNX GenAI "
            "bundle under .lengrvis_data/models."
        )
    if not genai_available:
        return (
            "ONNX Runtime GenAI is not installed. Install onnxruntime-genai-winml, "
            "onnxruntime-genai-directml, or an OpenVINO-capable GenAI runtime."
        )
    if not providers:
        return "onnxruntime-genai is installed, but no ONNX Runtime execution providers were reported."
    wanted = ", ".join(provider for _, provider in _PREFERRED_EXECUTION_PROVIDERS)
    return f"ONNX model is present, but none of the requested execution providers are available: {wanted}."
