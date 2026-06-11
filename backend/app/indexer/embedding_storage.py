from __future__ import annotations

import json
from typing import Any

import numpy as np


def vector_to_blob(vector: list[float] | np.ndarray) -> bytes:
    array = np.asarray(vector, dtype=np.float32)
    return array.tobytes()


def vector_from_storage(raw: Any) -> np.ndarray | None:
    """Decode embedding from BLOB (preferred) or legacy JSON TEXT."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray, memoryview)):
        array = np.frombuffer(raw, dtype=np.float32)
        return array if array.size else None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.startswith("["):
            try:
                data = json.loads(text)
            except ValueError:
                return None
            if not isinstance(data, list) or not data:
                return None
            return np.asarray(data, dtype=np.float32)
        try:
            array = np.frombuffer(raw.encode("latin-1"), dtype=np.float32)
            return array if array.size else None
        except ValueError:
            return None
    if isinstance(raw, list):
        return np.asarray(raw, dtype=np.float32) if raw else None
    return None


def vector_from_storage_list(raw: Any) -> list[float]:
    array = vector_from_storage(raw)
    return array.astype(float).tolist() if array is not None and array.size else []


def cosine_similarity(left: list[float] | np.ndarray, right: list[float] | np.ndarray) -> float:
    left_vec = np.asarray(left, dtype=np.float32)
    right_vec = np.asarray(right, dtype=np.float32)
    if left_vec.size == 0 or right_vec.size == 0 or left_vec.size != right_vec.size:
        return 0.0
    return float(cosine_similarity_batch(left_vec, right_vec.reshape(1, -1))[0])


def cosine_similarity_batch(query: list[float] | np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity between one query vector and rows of a 2-D matrix."""
    if matrix.size == 0:
        return np.asarray([], dtype=np.float32)
    query_vec = np.asarray(query, dtype=np.float32)
    query_norm = float(np.linalg.norm(query_vec))
    if query_norm == 0.0:
        return np.zeros(matrix.shape[0], dtype=np.float32)
    row_norms = np.linalg.norm(matrix, axis=1)
    dots = matrix @ query_vec
    denom = row_norms * query_norm
    scores = np.zeros(matrix.shape[0], dtype=np.float32)
    np.divide(dots, denom, out=scores, where=denom > 0)
    return scores
