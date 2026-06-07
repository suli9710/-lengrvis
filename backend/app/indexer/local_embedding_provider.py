"""Optional ONNX Runtime text embedding provider for local acceleration.

This module is intentionally dependency-soft. If ONNX Runtime, tokenizer
support, or a configured embedding model is missing, callers receive
``None``/unavailable health and can fall back to the existing embedding path.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from app.acceleration.onnx_sessions import (
    OnnxAccelerationUnavailable,
    OnnxSessionBackend,
    available_execution_providers,
    create_inference_session,
    detect_session_backend,
    first_present,
    health_payload,
    run_session,
    session_input_names,
)
from app.config import AppSettings, get_env


_MODEL_ENV_KEYS = (
    "LENGRVIS_EMBEDDING_ONNX_MODEL_PATH",
    "LENGRVIS_EMBEDDING_ONNX_MODEL_PATH",
    "LENGRVIS_EMBEDDING_ONNX_MODEL_PATH",
    "LENGRVIS_LOCAL_EMBEDDING_MODEL_PATH",
    "LENGRVIS_LOCAL_EMBEDDING_MODEL_PATH",
    "LENGRVIS_LOCAL_EMBEDDING_MODEL_PATH",
    "LENGRVIS_ONNX_EMBEDDING_MODEL_PATH",
    "LENGRVIS_ONNX_EMBEDDING_MODEL_PATH",
    "LENGRVIS_ONNX_EMBEDDING_MODEL_PATH",
)
_EXECUTION_PROVIDER_ENV_KEYS = (
    "LENGRVIS_EMBEDDING_ONNX_EXECUTION_PROVIDER",
    "LENGRVIS_EMBEDDING_ONNX_EXECUTION_PROVIDER",
    "LENGRVIS_EMBEDDING_ONNX_EXECUTION_PROVIDER",
    "LENGRVIS_ONNX_EMBEDDING_EXECUTION_PROVIDER",
    "LENGRVIS_ONNX_EMBEDDING_EXECUTION_PROVIDER",
    "LENGRVIS_ONNX_EMBEDDING_EXECUTION_PROVIDER",
    "LENGRVIS_LOCAL_EMBEDDING_EXECUTION_PROVIDER",
    "LENGRVIS_LOCAL_EMBEDDING_EXECUTION_PROVIDER",
    "LENGRVIS_LOCAL_EMBEDDING_EXECUTION_PROVIDER",
    "LENGRVIS_ONNX_EXECUTION_PROVIDER",
    "LENGRVIS_ONNX_EXECUTION_PROVIDER",
    "LENGRVIS_ONNX_EXECUTION_PROVIDER",
)
_MODEL_SUFFIXES = {".onnx", ".ort"}
_TOKENIZER_FILE = "tokenizer.json"
_MAX_LENGTH = 512


class LocalEmbeddingUnavailable(RuntimeError):
    """Raised when the optional local embedding path cannot be used."""


class _Tokenizer(Protocol):
    def encode_batch(self, texts: list[str]) -> dict[str, np.ndarray]:
        raise NotImplementedError


@dataclass(slots=True)
class LocalEmbeddingBackend:
    kind: str
    model_path: str
    execution_provider: str
    available_providers: list[str]
    provider_options: dict[str, Any] = field(default_factory=dict)
    tokenizer_path: str = ""
    runtime_package: str = ""
    model_id: str = ""


class LocalEmbeddingProvider:
    name = "local-onnx-embedding"

    def __init__(self, backend: LocalEmbeddingBackend) -> None:
        self.backend = backend
        self._session: Any | None = None
        self._tokenizer: _Tokenizer | None = None

    def health(self) -> dict[str, Any]:
        try:
            self._ensure_ready()
        except Exception as exc:  # noqa: BLE001 - health must not break fallback.
            return {"available": False, **asdict(self.backend), "error": str(exc)}
        return {"available": True, **asdict(self.backend)}

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return self.embed_sync(texts)

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        normalized = [str(text or "") for text in texts]
        if not normalized:
            return []
        session = self._ensure_session()
        tokenizer = self._ensure_tokenizer()
        encoded = tokenizer.encode_batch(normalized)
        inputs = _session_inputs(session)
        feed: dict[str, np.ndarray] = {}
        for name in inputs:
            if name in encoded:
                feed[name] = encoded[name]
        if "input_ids" not in feed:
            raise LocalEmbeddingUnavailable("ONNX embedding model does not expose an input_ids input.")
        outputs = run_session(session, feed)
        if not outputs:
            raise LocalEmbeddingUnavailable("ONNX embedding model returned no outputs.")
        embeddings = _pool_output(np.asarray(outputs[0]), encoded.get("attention_mask"))
        if _normalize_embeddings():
            embeddings = _l2_normalize(embeddings)
        return [[float(value) for value in row] for row in embeddings.tolist()]

    def _ensure_ready(self) -> None:
        self._ensure_session()
        self._ensure_tokenizer()

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        try:
            self._session = create_inference_session(_session_backend(self.backend))
        except OnnxAccelerationUnavailable as exc:
            raise LocalEmbeddingUnavailable(f"Failed to create ONNX embedding session: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - native runtime/model failures should degrade.
            raise LocalEmbeddingUnavailable(f"Failed to create ONNX embedding session: {exc}") from exc
        return self._session

    def _ensure_tokenizer(self) -> _Tokenizer:
        if self._tokenizer is not None:
            return self._tokenizer
        model_dir = Path(self.backend.model_path).parent
        self._tokenizer = _load_tokenizer(model_dir)
        return self._tokenizer


_CACHED_PROVIDER: tuple[str, LocalEmbeddingProvider | None] | None = None


def get_local_embedding_provider(settings: AppSettings | None = None) -> LocalEmbeddingProvider | None:
    """Return a healthy local ONNX embedding provider, if explicitly configured."""
    global _CACHED_PROVIDER
    backend = detect_local_embedding_backend(settings)
    cache_key = _cache_key(backend)
    if _CACHED_PROVIDER is not None and _CACHED_PROVIDER[0] == cache_key:
        return _CACHED_PROVIDER[1]
    if backend is None:
        _CACHED_PROVIDER = (cache_key, None)
        return None
    provider = LocalEmbeddingProvider(backend)
    if not provider.health().get("available"):
        _CACHED_PROVIDER = (cache_key, None)
        return None
    _CACHED_PROVIDER = (cache_key, provider)
    return provider


def detect_local_embedding_backend(settings: AppSettings | None = None) -> LocalEmbeddingBackend | None:
    model_path = _resolve_embedding_model_path(settings)
    if model_path is None:
        return None
    backend = detect_session_backend(
        model_path=str(model_path),
        configured_provider=_configured_execution_provider(settings) or "",
        settings=settings,
        model_id=str(getattr(settings, "onnx_embedding_model_id", "") or "") if settings is not None else "",
    )
    if backend is None:
        return None
    return LocalEmbeddingBackend(
        kind=backend.kind,
        model_path=backend.model_path,
        execution_provider=backend.execution_provider,
        available_providers=backend.available_providers,
        provider_options=backend.provider_options,
        tokenizer_path=str(model_path.parent / _TOKENIZER_FILE),
        runtime_package=backend.runtime_package,
        model_id=backend.model_id,
    )


def health_snapshot(settings: AppSettings | None = None) -> dict[str, Any]:
    backend = detect_local_embedding_backend(settings)
    if backend is None:
        return health_payload(
            component="text_embedding",
            backend=None,
            configured_model_path=str(_configured_model_path(settings) or ""),
            configured_provider=_configured_execution_provider(settings) or "",
            error=_unavailable_reason(settings),
        )
    return LocalEmbeddingProvider(backend).health()


def test_embedding(settings: AppSettings | None = None, texts: list[str] | None = None) -> dict[str, Any]:
    sample = [str(text or "") for text in (texts or ["Lengrvis local embedding smoke test."])]
    health = health_snapshot(settings)
    provider = get_local_embedding_provider(settings)
    if provider is None:
        return {
            "ok": False,
            "available": False,
            "status": "unavailable",
            "operation": "test_embedding",
            "error": health.get("error") or "Local ONNX embedding is unavailable.",
            "text_embedding": health,
        }
    try:
        vectors = provider.embed_sync(sample)
    except Exception as exc:  # noqa: BLE001 - smoke result should not become a 500.
        error = str(exc) or exc.__class__.__name__
        return {
            "ok": False,
            "available": False,
            "status": "unavailable",
            "operation": "test_embedding",
            "error": error,
            "text_embedding": health,
        }
    dim = len(vectors[0]) if vectors else 0
    return {
        "ok": True,
        "available": True,
        "status": "ready",
        "operation": "test_embedding",
        "count": len(vectors),
        "dim": dim,
        "text_embedding": health_snapshot(settings),
    }


def _resolve_embedding_model_path(settings: AppSettings | None) -> Path | None:
    raw = _configured_model_path(settings)
    if not raw:
        return None
    return _resolve_raw_model_path(raw)


def _configured_model_path(settings: AppSettings | None) -> str | None:
    for env_key in _MODEL_ENV_KEYS:
        value = os.environ.get(env_key)
        if value and value.strip():
            return value.strip()
    if settings is not None:
        dedicated = str(getattr(settings, "onnx_embedding_model_path", "") or "").strip()
        if dedicated:
            return dedicated
        embedding_model = str(getattr(settings, "embedding_model", "") or "").strip()
        if _looks_like_local_model_path(embedding_model):
            return embedding_model
        onnx_model_path = str(getattr(settings, "onnx_model_path", "") or "").strip()
        if "embed" in onnx_model_path.lower():
            return onnx_model_path
    return None


def _resolve_raw_model_path(raw: str) -> Path | None:
    path = Path(raw).expanduser()
    try:
        path = path.resolve(strict=False)
    except OSError:
        return None
    if path.is_file() and path.suffix.lower() in _MODEL_SUFFIXES:
        return path
    if path.is_dir():
        return _find_onnx_model(path)
    return None


def _find_onnx_model(root: Path) -> Path | None:
    preferred = ["model.onnx", "embedding.onnx", "embeddings.onnx"]
    for name in preferred:
        candidate = root / name
        if candidate.is_file():
            return candidate
    candidates = sorted(path for path in root.rglob("*.onnx") if path.is_file())
    return candidates[0] if candidates else None


def _looks_like_local_model_path(value: str) -> bool:
    if not value:
        return False
    lowered = value.lower()
    if lowered.endswith(tuple(_MODEL_SUFFIXES)):
        return True
    path = Path(value).expanduser()
    return path.exists()


def _configured_execution_provider(settings: AppSettings | None) -> str | None:
    for env_key in _EXECUTION_PROVIDER_ENV_KEYS:
        value = os.environ.get(env_key)
        if value and value.strip():
            return value.strip()
    if settings is not None:
        return first_present(
            [
                str(getattr(settings, "onnx_embedding_execution_provider", "") or ""),
                str(getattr(settings, "onnx_execution_provider", "") or ""),
            ]
        ) or None
    return None


def _available_execution_providers() -> list[str]:
    return available_execution_providers()


def _load_tokenizer(model_dir: Path) -> _Tokenizer:
    tokenizer_file = model_dir / _TOKENIZER_FILE
    if tokenizer_file.is_file():
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise LocalEmbeddingUnavailable("tokenizers is not installed for tokenizer.json local embedding.") from exc
        tokenizer = Tokenizer.from_file(str(tokenizer_file))
        return _TokenizersTokenizer(tokenizer)
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise LocalEmbeddingUnavailable(
            "No tokenizer.json found and transformers is not installed for local embedding tokenization."
        ) from exc
    try:
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
    except Exception as exc:  # noqa: BLE001
        raise LocalEmbeddingUnavailable(f"Failed to load local embedding tokenizer: {exc}") from exc
    return _TransformersTokenizer(tokenizer)


class _TokenizersTokenizer:
    def __init__(self, tokenizer: Any) -> None:
        self.tokenizer = tokenizer
        self.tokenizer.enable_truncation(max_length=_MAX_LENGTH)
        self.tokenizer.enable_padding(length=None)

    def encode_batch(self, texts: list[str]) -> dict[str, np.ndarray]:
        encodings = self.tokenizer.encode_batch(texts)
        input_ids = np.asarray([encoding.ids for encoding in encodings], dtype=np.int64)
        attention_mask = np.asarray([encoding.attention_mask for encoding in encodings], dtype=np.int64)
        token_type_ids = np.asarray([encoding.type_ids for encoding in encodings], dtype=np.int64)
        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }


class _TransformersTokenizer:
    def __init__(self, tokenizer: Any) -> None:
        self.tokenizer = tokenizer

    def encode_batch(self, texts: list[str]) -> dict[str, np.ndarray]:
        encoded = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=_MAX_LENGTH,
            return_tensors="np",
        )
        return {str(key): np.asarray(value, dtype=np.int64) for key, value in encoded.items()}


def _session_inputs(session: Any) -> set[str]:
    try:
        return set(session_input_names(session))
    except Exception as exc:  # noqa: BLE001
        raise LocalEmbeddingUnavailable(f"Unable to inspect ONNX embedding inputs: {exc}") from exc


def _pool_output(output: np.ndarray, attention_mask: np.ndarray | None) -> np.ndarray:
    if output.ndim == 2:
        return output.astype(np.float32, copy=False)
    if output.ndim != 3:
        raise LocalEmbeddingUnavailable(f"Unsupported ONNX embedding output rank: {output.ndim}.")
    values = output.astype(np.float32, copy=False)
    if attention_mask is None:
        return values.mean(axis=1)
    mask = attention_mask.astype(np.float32)
    while mask.ndim < values.ndim:
        mask = np.expand_dims(mask, axis=-1)
    masked = values * mask
    denominator = np.maximum(mask.sum(axis=1), 1e-12)
    return masked.sum(axis=1) / denominator


def _l2_normalize(embeddings: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(embeddings, axis=1, keepdims=True)
    return embeddings / np.maximum(norm, 1e-12)


def _normalize_embeddings() -> bool:
    raw = get_env("LENGRVIS_EMBEDDING_NORMALIZE", "true") or "true"
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _unavailable_reason(settings: AppSettings | None) -> str:
    raw_model_path = _configured_model_path(settings)
    if not raw_model_path:
        return (
            "No local ONNX embedding model configured. Set LENGRVIS_EMBEDDING_ONNX_MODEL_PATH "
            "or point embedding_model at a local ONNX embedding bundle."
        )
    if _resolve_raw_model_path(raw_model_path) is None:
        return "Configured local ONNX embedding model path does not exist or has no .onnx model file."
    if not _available_execution_providers():
        return "onnxruntime is not installed or reports no execution providers."
    return "No supported local ONNX embedding execution provider is available: WinML, DirectML, OpenVINO, or CPU."


def _cache_key(backend: LocalEmbeddingBackend | None) -> str:
    if backend is None:
        return "unavailable"
    return "|".join(
        [
            backend.model_path,
            backend.execution_provider,
            ",".join(backend.available_providers),
            str(sorted(backend.provider_options.items())),
        ]
    )


def _session_backend(backend: LocalEmbeddingBackend) -> OnnxSessionBackend:
    return OnnxSessionBackend(
        kind=backend.kind,
        model_path=backend.model_path,
        execution_provider=backend.execution_provider,
        available_providers=backend.available_providers,
        provider_options={str(key): str(value) for key, value in backend.provider_options.items()},
        runtime_package=backend.runtime_package,
        model_id=backend.model_id,
    )
