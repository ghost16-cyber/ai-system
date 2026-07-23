from __future__ import annotations

import math
from typing import Protocol

from backend.app.project_retrieval.bm25 import tokens
from backend.app.project_retrieval.contracts import RetrievalCandidate


class RerankerUnavailable(RuntimeError):
    pass


class Reranker(Protocol):
    identity: str
    version: str

    def rerank(
        self,
        query: str,
        candidates: tuple[tuple[RetrievalCandidate, str], ...],
    ) -> dict[str, float]: ...


class DeterministicLexicalReranker:
    identity = "deterministic-lexical-reranker"
    version = "v1"

    def rerank(
        self,
        query: str,
        candidates: tuple[tuple[RetrievalCandidate, str], ...],
    ) -> dict[str, float]:
        query_tokens = set(tokens(query))
        scores: dict[str, float] = {}
        for candidate, text in candidates:
            text_tokens = set(tokens(text))
            overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens))
            score = 0.65 * candidate.hybrid_score + 0.35 * overlap
            scores[candidate.chunk_id] = max(0.0, min(1.0, score))
        return scores


class UnavailableReranker:
    identity = "unavailable"
    version = "v1"

    def rerank(self, query: str, candidates):
        del query, candidates
        raise RerankerUnavailable("reranker_unavailable")


def validate_rerank(
    candidates: tuple[RetrievalCandidate, ...],
    scores: dict[str, float],
) -> dict[str, float]:
    supplied = {item.chunk_id for item in candidates}
    if set(scores) != supplied:
        raise ValueError("reranker_candidate_set_mismatch")
    if any(not math.isfinite(score) for score in scores.values()):
        raise ValueError("invalid_reranker_score")
    return scores


def normalize_rerank_scores(scores: dict[str, float]) -> dict[str, float]:
    """Map arbitrary finite logits to [0, 1] without changing their ordering."""
    if not scores:
        return {}
    if any(not math.isfinite(score) for score in scores.values()):
        raise ValueError("invalid_reranker_score")
    return {
        chunk_id: 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, score))))
        for chunk_id, score in scores.items()
    }
