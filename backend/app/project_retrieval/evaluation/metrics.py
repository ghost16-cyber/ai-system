from __future__ import annotations

import math
import statistics

from backend.app.project_retrieval.evaluation.contracts import (
    EvaluationMetrics,
    QueryEvaluationResult,
)


def recall_at_k(ranked: tuple[str, ...], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank(ranked: tuple[str, ...], relevant: set[str]) -> float:
    for index, item in enumerate(ranked, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked: tuple[str, ...], relevant: set[str], k: int) -> float:
    dcg = sum(
        1.0 / math.log2(index + 2)
        for index, item in enumerate(ranked[:k])
        if item in relevant
    )
    ideal = sum(
        1.0 / math.log2(index + 2)
        for index in range(min(k, len(relevant)))
    )
    return dcg / ideal if ideal else 0.0


def percentile_95(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def aggregate_metrics(
    results: tuple[QueryEvaluationResult, ...],
    *,
    query_types: dict[str, str],
) -> EvaluationMetrics:
    count = max(1, len(results))
    recalls_1 = []
    recalls_3 = []
    recalls_5 = []
    reciprocal = []
    ndcg = []
    file_hits = []
    symbol_hits = []
    for result in results:
        relevant = set(result.relevant_document_ids)
        recalls_1.append(recall_at_k(result.ranked_document_ids, relevant, 1))
        recalls_3.append(recall_at_k(result.ranked_document_ids, relevant, 3))
        recalls_5.append(recall_at_k(result.ranked_document_ids, relevant, 5))
        reciprocal.append(reciprocal_rank(result.ranked_document_ids, relevant))
        ndcg.append(ndcg_at_k(result.ranked_document_ids, relevant, 5))
        hit = bool(set(result.ranked_document_ids[:1]) & relevant)
        if query_types.get(result.case_id) == "exact_filename":
            file_hits.append(float(hit))
        if query_types.get(result.case_id) == "exact_symbol":
            symbol_hits.append(float(hit))
    latency = tuple(item.latency_ms for item in results)
    reranking = tuple(item.reranking_latency_ms for item in results)
    return EvaluationMetrics(
        recall_at_1=sum(recalls_1) / count,
        recall_at_3=sum(recalls_3) / count,
        recall_at_5=sum(recalls_5) / count,
        mrr=sum(reciprocal) / count,
        ndcg_at_5=sum(ndcg) / count,
        exact_file_hit_rate=sum(file_hits) / len(file_hits) if file_hits else 0.0,
        exact_symbol_hit_rate=(
            sum(symbol_hits) / len(symbol_hits) if symbol_hits else 0.0
        ),
        zero_result_rate=(
            sum(not item.ranked_document_ids for item in results) / count
        ),
        mean_latency_ms=statistics.fmean(latency) if latency else 0.0,
        median_latency_ms=statistics.median(latency) if latency else 0.0,
        p95_latency_ms=percentile_95(latency),
        mean_reranking_latency_ms=(
            statistics.fmean(reranking) if reranking else 0.0
        ),
        provider_fallback_rate=(
            sum(item.fallback_used for item in results) / count
        ),
    )

