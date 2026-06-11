import json

import numpy as np

from app.indexer.embedding_storage import (
    cosine_similarity_batch,
    vector_from_storage,
    vector_from_storage_list,
    vector_to_blob,
)
from app.indexer.fts_query import fts_match_query


def test_vector_blob_round_trip() -> None:
    source = [0.1, -0.2, 0.3, 1.0]
    blob = vector_to_blob(source)
    restored = vector_from_storage(blob)
    assert restored is not None
    np.testing.assert_allclose(restored, np.asarray(source, dtype=np.float32))


def test_vector_legacy_json_text() -> None:
    source = [0.5, 0.25, -0.75]
    restored = vector_from_storage(json.dumps(source))
    assert restored is not None
    np.testing.assert_allclose(restored, np.asarray(source, dtype=np.float32))


def test_cosine_similarity_batch_matches_manual() -> None:
    query = [1.0, 0.0]
    matrix = np.asarray([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], dtype=np.float32)
    scores = cosine_similarity_batch(query, matrix)
    assert scores[0] == 1.0
    assert scores[1] == 0.0
    assert 0.0 < scores[2] < 1.0


def test_vector_from_storage_list_empty() -> None:
    assert vector_from_storage_list(None) == []
    assert vector_from_storage_list("") == []


def test_fts_match_query_escapes_quotes() -> None:
    assert fts_match_query('hello "world"') == '"hello" OR """world"""'


def test_fts_match_query_single_token() -> None:
    assert fts_match_query("汽车维修") == '"汽车维修"'
