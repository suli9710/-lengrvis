from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.indexer.clustering import hashing_vectorize
from app.indexer.local_embedding_provider import get_local_embedding_provider
from app.llm.registry import get_effective_settings, get_provider

Embedder = Callable[[list[str]], Awaitable[list[list[float]]] | list[list[float]]]
logger = logging.getLogger(__name__)
DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 45.0


async def _with_timeout(coro: Awaitable[Any], timeout_seconds: float | None) -> Any:
    if timeout_seconds is None or timeout_seconds <= 0:
        return await coro
    return await asyncio.wait_for(coro, timeout=timeout_seconds)


async def embed_texts(
    texts: list[str],
    *,
    embedder: Embedder | None = None,
    timeout_seconds: float | None = DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
) -> list[list[float]]:
    normalized = [str(text or "") for text in texts]
    if not normalized:
        return []
    if embedder is not None:
        vectors = embedder(normalized)
        if hasattr(vectors, "__await__"):
            vectors = await _with_timeout(vectors, timeout_seconds)  # type: ignore[arg-type,assignment]
        return [_coerce_vector(vector) for vector in vectors]  # type: ignore[arg-type]
    settings = get_effective_settings()
    local_provider = get_local_embedding_provider(settings)
    if local_provider is not None:
        try:
            vectors = await _with_timeout(
                local_provider.embed(normalized, model=settings.embedding_model),
                timeout_seconds,
            )
            return [_coerce_vector(vector) for vector in vectors]
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: local embeddings should degrade to the configured provider.
            logger.debug("Local embedding provider failed; falling back to configured provider: %s", exc, exc_info=True)
    try:
        vectors = await _with_timeout(
            get_provider(settings, task="embed").embed(normalized, model=settings.embedding_model),
            timeout_seconds,
        )
        return [_coerce_vector(vector) for vector in vectors]
    except Exception:  # noqa: BLE001 - broad-exception-boundary
        return hashing_vectorize(normalized, dim=64)


def embed_texts_sync(
    texts: list[str],
    *,
    embedder: Embedder | None = None,
    timeout_seconds: float | None = DEFAULT_EMBEDDING_TIMEOUT_SECONDS,
) -> list[list[float]]:
    return run_async(
        embed_texts(texts, embedder=embedder, timeout_seconds=timeout_seconds),
        timeout_seconds=timeout_seconds,
    )


def run_async(coro: Awaitable[Any], *, timeout_seconds: float | None = DEFAULT_EMBEDDING_TIMEOUT_SECONDS) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(asyncio.run, coro)
    try:
        guard_timeout = None if timeout_seconds is None or timeout_seconds <= 0 else timeout_seconds + 1
        return future.result(timeout=guard_timeout)
    finally:
        if not future.done():
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)


def _coerce_vector(vector: list[float] | tuple[float, ...]) -> list[float]:
    return [float(value) for value in vector]
