from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.project_control.contracts import content_hash
from backend.app.project_retrieval.contracts import RetrievalCandidate
from backend.app.project_retrieval.learned import (
    CrossEncoderReranker,
    clear_learned_model_cache,
)
from backend.app.project_retrieval.provider_registry import (
    LocalModelResolution,
    ProviderDevice,
    reranker_spec,
)
from backend.app.project_retrieval.reranking import (
    RerankerUnavailable,
    normalize_rerank_scores,
    validate_rerank,
)


@pytest.fixture(autouse=True)
def _clear_model_cache():
    clear_learned_model_cache()
    yield
    clear_learned_model_cache()


class _Array:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class _CrossEncoder:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def predict(self, pairs, **kwargs):
        self.calls.append((tuple(pairs), kwargs))
        return _Array(self.scores)


def _provider(tmp_path: Path, scores):
    spec = reranker_spec("cross-encoder/ms-marco-MiniLM-L6-v2")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    model = _CrossEncoder(scores)
    provider = CrossEncoderReranker(
        spec,
        LocalModelResolution(
            model_id=spec.model_id,
            configured_revision="main",
            locally_cached=True,
            resolved_revision="revision-1",
            snapshot_path=snapshot.as_posix(),
            effective_identity=content_hash([spec.model_id, "revision-1"]),
        ),
        requested_device=ProviderDevice.CPU,
        loader=lambda path, device: model,
    )
    return provider, model


def _candidates(count=2):
    return tuple(
        (
            RetrievalCandidate(
                chunk_id=f"chunk-{index}",
                source_id=f"source-{index}",
                bm25_score=1.0,
                semantic_score=0.5,
                hybrid_score=0.5,
                rank_before_rerank=index + 1,
            ),
            f"candidate {index}",
        )
        for index in range(count)
    )


def test_cross_encoder_accepts_finite_logits_and_preserves_association(tmp_path: Path) -> None:
    provider, model = _provider(tmp_path, [8.5, -3.0])
    candidates = _candidates()
    raw = validate_rerank(
        tuple(item[0] for item in candidates),
        provider.rerank("query", candidates),
    )
    normalized = normalize_rerank_scores(raw)
    assert raw == {"chunk-0": 8.5, "chunk-1": -3.0}
    assert normalized["chunk-0"] > normalized["chunk-1"]
    assert model.calls[0][0] == (
        ("query", "candidate 0"),
        ("query", "candidate 1"),
    )


@pytest.mark.parametrize("scores", [[1.0], [1.0, float("inf")], ["bad", 1.0]])
def test_malformed_cross_encoder_output_is_typed(tmp_path: Path, scores) -> None:
    provider, _ = _provider(tmp_path, scores)
    with pytest.raises(RerankerUnavailable):
        provider.rerank("query", _candidates())


def test_reranker_never_accepts_more_than_twenty_candidates(tmp_path: Path) -> None:
    provider, _ = _provider(tmp_path, [1.0] * 21)
    with pytest.raises(RerankerUnavailable, match="limit"):
        provider.rerank("query", _candidates(21))
