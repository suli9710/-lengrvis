from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Awaitable, Callable
from typing import Any

from app.indexer.clustering import hashing_vectorize
from app.llm.registry import get_effective_settings, get_provider


Embedder = Callable[[list[str]], Awaitable[list[list[float]]] | list[list[float]]]


async def embed_texts(texts: list[str], *, embedder: Embedder | None = None) -> list[list[float]]:
    normalized = [str(text or "") for text in texts]
    if not normalized:
        return []
    if embedder is not None:
        vectors = embedder(normalized)
        if hasattr(vectors, "__await__"):
            vectors = await vectors  # type: ignore[assignment]
        return [_coerce_vector(vector) for vector in vectors]  # type: ignore[arg-type]
    try:
        vectors = await get_provider(task="embed").embed(normalized, model=get_effective_settings().embedding_model)
        return [_coerce_vector(vector) for vector in vectors]
    except Exception:
        return hashing_vectorize(normalized, dim=64)


def embed_texts_sync(texts: list[str], *, embedder: Embedder | None = None) -> list[list[float]]:
    return run_async(embed_texts(texts, embedder=embedder))


def run_async(coro: Awaitable[Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


def _coerce_vector(vector: list[float] | tuple[float, ...]) -> list[float]:
    return [float(value) for value in vector]
