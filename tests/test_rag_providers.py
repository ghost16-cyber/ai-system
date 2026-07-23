from __future__ import annotations

import math

import pytest

from backend.app.project_retrieval.reranking import validate_rerank
from backend.app.project_retrieval.semantic import (
    DeterministicEmbeddingProvider,
    UnavailableEmbeddingProvider,
)
from backend.app.project_retrieval.bm25 import rank_bm25
from backend.app.project_retrieval.hybrid import hybrid_candidates


def _candidates():
    bm25 = rank_bm25("parser", {"a": "parser", "b": "parser"}, limit=2)
    return hybrid_candidates(
        chunks={"a": ("a.py", "sa"), "b": ("b.py", "sb")},
        bm25_scores=bm25,
        semantic_scores={"a": 0.5, "b": 0.4},
        limit=2,
    )


def test_fake_embeddings_are_deterministic_and_bounded() -> None:
    provider = DeterministicEmbeddingProvider()
    assert provider.embed(("same",)) == provider.embed(("same",))
    assert len(provider.embed(("same",))[0]) == provider.dimensions


def test_unavailable_embedding_provider_is_typed() -> None:
    provider = UnavailableEmbeddingProvider()
    with pytest.raises(Exception, match="unavailable"):
        provider.embed(("query",))


@pytest.mark.parametrize(
    "output",
    [
        (("unknown", 0.5),),
        (("a", 0.5), ("a", 0.4)),
        (("a", math.nan), ("b", 0.2)),
        (("a", math.inf), ("b", 0.2)),
    ],
)
def test_reranker_validation_rejects_unknown_duplicate_and_nonfinite_scores(output) -> None:
    with pytest.raises(ValueError):
        validate_rerank(_candidates(), output)
