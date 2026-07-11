from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from backend.app.rag.corpus_vector_store import (
    DEFAULT_VECTOR_ROOT,
    load_corpus_vectors,
)
from backend.app.rag.embedding_provider import EmbeddingProvider


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    left_magnitude = math.sqrt(sum(value * value for value in left))
    right_magnitude = math.sqrt(sum(value * value for value in right))
    if not left_magnitude or not right_magnitude:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_magnitude * right_magnitude
    )


def search_corpus_vectors(
    query: str,
    provider: EmbeddingProvider,
    *,
    vector_root: str | Path = DEFAULT_VECTOR_ROOT,
    top_k: int = 5,
    source_path: str | None = None,
    minimum_score: float | None = None,
) -> dict[str, Any]:
    if not query.strip():
        raise ValueError("query must not be empty")
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero")

    manifest, records = load_corpus_vectors(vector_root, provider)
    query_vectors = provider.embed_texts([query])
    if len(query_vectors) != 1 or len(query_vectors[0]) != provider.dimension:
        raise ValueError("Embedding provider returned an invalid query vector.")

    query_vector = query_vectors[0]
    results: list[dict[str, Any]] = []
    for record in records:
        if source_path is not None and record["source_path"] != source_path:
            continue
        score = _cosine_similarity(query_vector, record["vector"])
        if minimum_score is not None and score < minimum_score:
            continue
        results.append(
            {
                "chunk_id": record["chunk_id"],
                "source_path": record["source_path"],
                "source_hash": record["source_hash"],
                "chunk_index": record["chunk_index"],
                "chunk_hash": record["chunk_hash"],
                "text": record["text"],
                "start_line": record.get("start_line"),
                "end_line": record.get("end_line"),
                "extension": record.get("extension"),
                "score": score,
            }
        )

    results.sort(key=lambda item: (-item["score"], item["chunk_id"]))
    selected = results[:top_k]
    return {
        "status": "ready",
        "query": query,
        "top_k": top_k,
        "minimum_score": minimum_score,
        "source_path": source_path,
        "embedding": manifest["embedding"],
        "total_vectors": len(records),
        "count": len(selected),
        "results": selected,
    }
