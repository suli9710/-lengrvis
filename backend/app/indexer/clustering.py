"""Lightweight content clustering helpers used by P1-1 cluster_tools.

This module deliberately avoids heavy scikit-learn dependencies. The clustering
routines work on already-computed embeddings or on tag/keyword vectors.
"""

from __future__ import annotations

import math
import random
from typing import Iterable


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _cosine(a: list[float], b: list[float]) -> float:
    length = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(length))


def kmeans(vectors: list[list[float]], k: int, *, max_iter: int = 25, seed: int = 7) -> list[int]:
    if not vectors:
        return []
    k = max(1, min(k, len(vectors)))
    rng = random.Random(seed)
    centroids = [_normalize(list(vectors[rng.randrange(len(vectors))])) for _ in range(k)]
    assignments = [0] * len(vectors)
    normalized = [_normalize(list(v)) for v in vectors]
    for _ in range(max_iter):
        changed = False
        for index, vector in enumerate(normalized):
            best = max(range(k), key=lambda c: _cosine(vector, centroids[c]))
            if assignments[index] != best:
                changed = True
            assignments[index] = best
        # Recompute centroids
        for cluster in range(k):
            members = [vec for idx, vec in enumerate(normalized) if assignments[idx] == cluster]
            if not members:
                continue
            dim = len(members[0])
            avg = [sum(member[i] for member in members) / len(members) for i in range(dim)]
            centroids[cluster] = _normalize(avg)
        if not changed:
            break
    return assignments


def hashing_vectorize(texts: Iterable[str], *, dim: int = 64) -> list[list[float]]:
    """Cheap hashing trick when embedding API is unavailable."""
    result: list[list[float]] = []
    for text in texts:
        vector = [0.0] * dim
        for token in (text or "").lower().split():
            idx = hash(token) % dim
            vector[idx] += 1.0
        result.append(vector)
    return result


def cluster_texts(texts: list[str], *, k: int | None = None) -> dict[int, list[int]]:
    if not texts:
        return {}
    target_k = k or max(2, min(8, len(texts) // 3 or 2))
    vectors = hashing_vectorize(texts)
    assignments = kmeans(vectors, target_k)
    groups: dict[int, list[int]] = {}
    for index, cluster in enumerate(assignments):
        groups.setdefault(cluster, []).append(index)
    return groups
