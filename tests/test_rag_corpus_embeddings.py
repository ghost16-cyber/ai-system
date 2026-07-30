from __future__ import annotations

import math

import pytest

from backend.app.rag.deterministic_embeddings import (
    DeterministicEmbeddingProvider,
)


def test_deterministic_embeddings_are_stable_and_offline() -> None:
    provider = DeterministicEmbeddingProvider(dimension=32)

    first = provider.embed_texts(["assignment report generation"])[0]
    second = provider.embed_texts(["assignment report generation"])[0]

    assert first == second
    assert len(first) == 32
    assert all(isinstance(value, float) for value in first)
    assert math.sqrt(sum(value * value for value in first)) == pytest.approx(1.0)
    assert provider.configuration()["production_quality"] is False


def test_deterministic_embeddings_have_stable_dimensions() -> None:
    provider = DeterministicEmbeddingProvider(dimension=16)

    vectors = provider.embed_texts(["alpha", "", "alpha beta"])

    assert [len(vector) for vector in vectors] == [16, 16, 16]
    assert vectors[1] == [0.0] * 16


def test_deterministic_embedding_dimension_must_be_positive() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        DeterministicEmbeddingProvider(dimension=0)
