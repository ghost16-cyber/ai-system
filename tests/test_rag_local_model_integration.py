from __future__ import annotations

import math
import os

import pytest

from backend.app.project_retrieval.configuration import (
    retrieval_configuration_from_environment,
)
from backend.app.project_retrieval.providers import build_retrieval_providers
from backend.app.project_retrieval.contracts import RetrievalCandidate


pytestmark = pytest.mark.local_model_integration


@pytest.mark.skipif(
    os.getenv("ASTRA_RUN_LOCAL_MODEL_TESTS") != "1",
    reason="set ASTRA_RUN_LOCAL_MODEL_TESTS=1 to load locally cached models",
)
def test_cached_bge_and_cross_encoder_are_offline_and_bounded(monkeypatch) -> None:
    monkeypatch.setenv("ASTRA_RAG_EMBEDDING_DEVICE", "cpu")
    monkeypatch.setenv("ASTRA_RAG_RERANKER_DEVICE", "cpu")
    embedding, reranker = build_retrieval_providers(
        retrieval_configuration_from_environment()
    )
    if not embedding.readiness().ready or not reranker.readiness().ready:
        pytest.skip("the allowlisted learned models are not present in the local cache")
    vectors = embedding.embed_passages(("canonical lifecycle authority", "exact replay"))
    assert len(vectors) == 2
    assert all(len(vector) == 384 for vector in vectors)
    assert all(
        math.isfinite(value) for vector in vectors for value in vector
    )
    assert all(
        math.isclose(
            math.sqrt(sum(value * value for value in vector)),
            1.0,
            abs_tol=1e-3,
        )
        for vector in vectors
    )
    candidates = tuple(
        (
            RetrievalCandidate(
                chunk_id=f"chunk-{index}",
                source_id=f"source-{index}",
                bm25_score=1.0,
                semantic_score=0.5,
                hybrid_score=0.5,
                rank_before_rerank=index + 1,
            ),
            text,
        )
        for index, text in enumerate(("lifecycle authority", "unrelated text"))
    )
    scores = reranker.rerank("project lifecycle writer", candidates)
    assert set(scores) == {"chunk-0", "chunk-1"}
    assert all(math.isfinite(value) for value in scores.values())
