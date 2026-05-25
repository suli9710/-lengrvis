"""Optional ONNX Runtime provider detection for local NPU acceleration.

The provider is deliberately optional: environments without DirectML/OpenVINO
continue to use the existing local HTTP backends from `local_provider.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.config import PROJECT_ROOT, AppSettings
from app.llm.base import LLMProvider
from app.llm.prompts import render_prompt


_PREFERRED_EXECUTION_PROVIDERS = [
    ("onnx-directml", "DmlExecutionProvider"),
    ("onnx-openvino", "OpenVINOExecutionProvider"),
    ("onnx-cpu", "CPUExecutionProvider"),
]
_EXECUTION_PROVIDER_ALIASES = {
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
_MODEL_ROOT_ENV_KEYS = ("MARVIS_ONNX_MODELS_DIR", "MAVRIS_ONNX_MODELS_DIR")


@dataclass(slots=True)
class OnnxBackend:
    kind: str
    model_path: str
    execution_provider: str
    available_providers: list[str]
    generation_runtime: str = "onnxruntime_genai"
    model_family: str = ""
    provider_options: dict[str, str] = field(default_factory=dict)


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
        return self._generate_text(
            prompt,
            temperature=self.settings.temperature if temperature is None else temperature,
        )

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        schema_prompt = {
            "role": "system",
            "content": render_prompt("structured_json_schema.md", {"schema": json.dumps(output_schema)}),
        }
        content = await self.chat([schema_prompt, *messages], temperature=0)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                return json.loads(content[start : end + 1])
            raise

    def _generate_text(self, prompt: str, *, temperature: float) -> str:
        from app.llm.local_provider import LocalBackendUnavailable

        try:
            og = _import_genai_runtime()
        except ImportError as exc:
            raise LocalBackendUnavailable(
                "ONNX acceleration is visible, but onnxruntime-genai is not installed or could not be loaded "
                "for text generation."
            ) from exc
        except Exception as exc:  # pragma: no cover - depends on optional native package
            raise LocalBackendUnavailable(f"Unable to import onnxruntime-genai: {exc}") from exc

        model_path = self._genai_model_path()
        try:
            config = og.Config(model_path)
            self._configure_execution_provider(config)
            model = og.Model(config)
            tokenizer = og.Tokenizer(model)
            stream = tokenizer.create_stream()
            input_tokens = tokenizer.encode(prompt)
            params = og.GeneratorParams(model)
            params.set_search_options(
                max_length=max(1, len(input_tokens)) + max(1, self.settings.max_tokens),
                temperature=temperature,
                batch_size=1,
            )
            generator = og.Generator(model, params)
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
        if self.backend.model_family.startswith("qwen"):
            return self._format_qwen_messages(messages)
        lines: list[str] = []
        for message in messages:
            role = str(message.get("role") or "user")
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
            parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
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
    if not _is_genai_runtime_available():
        return None

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
        model_family=_infer_model_family(candidate),
        provider_options=_provider_options(execution_provider),
    )


def _select_execution_provider(providers: list[str], settings: AppSettings | None = None) -> tuple[str, str] | None:
    configured = _configured_execution_provider(settings)
    preferred = _normalize_execution_provider(configured)
    candidates = [(_kind_for_execution_provider(preferred), preferred)] if preferred else []
    candidates.extend(
        (kind, execution_provider)
        for kind, execution_provider in _PREFERRED_EXECUTION_PROVIDERS
        if execution_provider != preferred
    )
    provider_names = set(providers)
    for kind, execution_provider in candidates:
        if execution_provider in provider_names or _genai_reports_provider_available(kind):
            return kind, execution_provider
    return None


def health_snapshot(settings: AppSettings | None = None, *, model_path: str | None = None) -> dict[str, Any]:
    raw_model_path = _configured_model_path(settings, model_path)
    candidate = _resolve_model_path(settings, model_path)
    genai_available = _is_genai_runtime_available()
    providers = _available_execution_providers() if genai_available else []
    backend = detect_onnx_backend(settings, model_path=model_path)
    if backend is not None:
        return {"available": True, **asdict(backend)}

    error = _unavailable_reason(candidate, genai_available, providers, configured_path=raw_model_path)
    return {
        "available": False,
        "kind": "onnx",
        "model_path": str(candidate or raw_model_path or ""),
        "execution_provider": "",
        "available_providers": providers,
        "generation_runtime": "onnxruntime_genai" if genai_available else "",
        "error": error,
    }


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
    return os.environ.get("MARVIS_ONNX_MODEL_PATH") or os.environ.get("MAVRIS_ONNX_MODEL_PATH")


def _configured_execution_provider(settings: AppSettings | None = None) -> str | None:
    if settings is not None:
        raw = str(getattr(settings, "onnx_execution_provider", "") or "").strip()
        if raw:
            return raw
    return os.environ.get("MARVIS_ONNX_EXECUTION_PROVIDER") or os.environ.get("MAVRIS_ONNX_EXECUTION_PROVIDER")


def _resolve_raw_model_path(raw: str) -> Path | None:
    path = Path(raw).expanduser()
    try:
        path = path.resolve(strict=False)
    except OSError:
        pass
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
        raw = os.environ.get(env_key)
        if raw:
            roots.append(Path(raw).expanduser())
    if settings is not None and settings.data_dir:
        data_dir = Path(settings.data_dir).expanduser()
        roots.extend([data_dir / "models", data_dir])
    roots.extend(
        [
            PROJECT_ROOT / ".marvis_data" / "models",
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
        (
            index
            for index, name in enumerate(_PREFERRED_MODEL_DIR_NAMES)
            if name.lower() in lowered
        ),
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
    if stripped.endswith("ExecutionProvider"):
        return stripped
    normalized = stripped.lower().replace("-", "_").replace(" ", "_")
    return _EXECUTION_PROVIDER_ALIASES.get(normalized)


def _kind_for_execution_provider(execution_provider: str) -> str:
    if execution_provider == "DmlExecutionProvider":
        return "onnx-directml"
    if execution_provider == "OpenVINOExecutionProvider":
        return "onnx-openvino"
    if execution_provider == "CPUExecutionProvider":
        return "onnx-cpu"
    return f"onnx-{execution_provider.lower().replace('executionprovider', '')}"


def _provider_options(execution_provider: str) -> dict[str, str]:
    if execution_provider != "DmlExecutionProvider":
        return {}
    device_id = os.environ.get("MARVIS_ONNX_DIRECTML_DEVICE_ID")
    if device_id is None or not device_id.strip():
        return {}
    return {"device_id": device_id.strip()}


def _import_genai_runtime() -> Any:
    import onnxruntime_genai as og

    return og


def _is_genai_runtime_available() -> bool:
    try:
        _import_genai_runtime()
    except ImportError:
        return False
    except Exception:
        return False
    return True


def _available_execution_providers() -> list[str]:
    try:
        import onnxruntime as ort
    except ImportError:
        return []
    except Exception:
        return []
    try:
        return [str(item) for item in ort.get_available_providers()]
    except Exception:
        return []


def _genai_reports_provider_available(kind: str) -> bool:
    try:
        og = _import_genai_runtime()
    except ImportError:
        return False
    except Exception:
        return False
    if kind == "onnx-directml" and hasattr(og, "is_dml_available"):
        try:
            return bool(og.is_dml_available())
        except Exception:
            return False
    if kind == "onnx-openvino" and hasattr(og, "is_openvino_available"):
        try:
            return bool(og.is_openvino_available())
        except Exception:
            return False
    return False


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
            "No ONNX GenAI model path configured. Set MARVIS_ONNX_MODEL_PATH or place a Qwen2.5 ONNX GenAI "
            "bundle under .marvis_data/models."
        )
    if not genai_available:
        return "onnxruntime-genai is not installed. Install onnxruntime-genai-directml or an OpenVINO-capable GenAI runtime."
    if not providers:
        return "onnxruntime-genai is installed, but no ONNX Runtime execution providers were reported."
    wanted = ", ".join(provider for _, provider in _PREFERRED_EXECUTION_PROVIDERS)
    return f"ONNX model is present, but none of the requested execution providers are available: {wanted}."
