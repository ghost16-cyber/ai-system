from backend.app.project_retrieval.evaluation.contracts import QueryEvaluationResult
from backend.app.project_retrieval.evaluation.metrics import (
    aggregate_metrics,
    ndcg_at_k,
    percentile_95,
    recall_at_k,
    reciprocal_rank,
)


def test_known_retrieval_metric_formulas() -> None:
    ranked = ("a", "b", "c", "d")
    relevant = {"b", "d"}
    assert recall_at_k(ranked, relevant, 1) == 0.0
    assert recall_at_k(ranked, relevant, 3) == 0.5
    assert reciprocal_rank(ranked, relevant) == 0.5
    assert 0.0 < ndcg_at_k(ranked, relevant, 5) < 1.0
    assert percentile_95((1.0, 2.0, 3.0, 100.0)) == 100.0


def test_aggregate_metrics_handles_zero_results_and_latency() -> None:
    results = (
        QueryEvaluationResult(
            case_id="file",
            ranked_document_ids=("a",),
            relevant_document_ids=("a",),
            latency_ms=1.0,
            reranking_latency_ms=0.5,
        ),
        QueryEvaluationResult(
            case_id="symbol",
            ranked_document_ids=(),
            relevant_document_ids=("b",),
            latency_ms=3.0,
            reranking_latency_ms=0.0,
            fallback_used=True,
        ),
    )
    metrics = aggregate_metrics(
        results,
        query_types={"file": "exact_filename", "symbol": "exact_symbol"},
    )
    assert metrics.mrr == 0.5
    assert metrics.zero_result_rate == 0.5
    assert metrics.mean_latency_ms == 2.0
    assert metrics.provider_fallback_rate == 0.5
    assert metrics.stale_evidence_rejection_rate == 1.0
    assert metrics.prompt_injection_authority_violation_count == 0
