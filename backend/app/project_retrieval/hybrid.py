from __future__ import annotations

from backend.app.project_retrieval.contracts import RetrievalCandidate


def hybrid_candidates(
    *,
    chunks: dict[str, tuple[str, str]],
    bm25_scores: dict[str, float],
    semantic_scores: dict[str, float],
    limit: int,
) -> tuple[RetrievalCandidate, ...]:
    ids = set(bm25_scores) | set(semantic_scores)
    max_bm25 = max(bm25_scores.values(), default=1.0)
    combined: list[tuple[str, float, float, float]] = []
    for chunk_id in ids:
        bm25 = bm25_scores.get(chunk_id, 0.0)
        semantic = semantic_scores.get(chunk_id, 0.0)
        normalized_bm25 = bm25 / max_bm25 if max_bm25 else 0.0
        hybrid = max(0.0, min(1.0, 0.55 * normalized_bm25 + 0.45 * semantic))
        combined.append((chunk_id, bm25, semantic, hybrid))
    combined.sort(key=lambda item: (-item[3], -item[2], -item[1], chunks[item[0]][0], item[0]))
    return tuple(
        RetrievalCandidate(
            chunk_id=chunk_id,
            source_id=chunks[chunk_id][1],
            bm25_score=bm25,
            semantic_score=semantic,
            hybrid_score=hybrid,
            rank_before_rerank=index + 1,
            retrieval_reasons=tuple(
                reason for reason, score in (("bm25", bm25), ("semantic", semantic)) if score > 0
            ),
        )
        for index, (chunk_id, bm25, semantic, hybrid) in enumerate(combined[:limit])
    )

