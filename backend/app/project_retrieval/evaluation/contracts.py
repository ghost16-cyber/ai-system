from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.app.project_control.contracts import StrictModel


class EvaluationDocument(StrictModel):
    document_id: str
    relative_path: str
    symbol: str | None = None
    content: str = Field(min_length=1, max_length=16_000)
    prompt_injection_fixture: bool = False


class EvaluationQueryCase(StrictModel):
    case_id: str
    query: str = Field(min_length=1, max_length=1_000)
    relevant_document_ids: tuple[str, ...] = Field(min_length=1)
    query_type: str


class EvaluationCorpus(StrictModel):
    schema_version: Literal["astra.rag.evaluation-corpus.v1"]
    corpus_id: str
    documents: tuple[EvaluationDocument, ...]
    queries: tuple[EvaluationQueryCase, ...]


class QueryEvaluationResult(StrictModel):
    case_id: str
    ranked_document_ids: tuple[str, ...]
    relevant_document_ids: tuple[str, ...]
    latency_ms: float = Field(ge=0)
    reranking_latency_ms: float = Field(ge=0)
    fallback_used: bool = False
    failure_reason: str | None = None


class EvaluationMetrics(StrictModel):
    recall_at_1: float = Field(ge=0, le=1)
    recall_at_3: float = Field(ge=0, le=1)
    recall_at_5: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    ndcg_at_5: float = Field(ge=0, le=1)
    exact_file_hit_rate: float = Field(ge=0, le=1)
    exact_symbol_hit_rate: float = Field(ge=0, le=1)
    zero_result_rate: float = Field(ge=0, le=1)
    mean_latency_ms: float = Field(ge=0)
    median_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)
    mean_reranking_latency_ms: float = Field(ge=0)
    provider_fallback_rate: float = Field(ge=0, le=1)
    stale_evidence_rejection_rate: Literal[1.0] = 1.0
    prompt_injection_authority_violation_count: Literal[0] = 0


class EvaluationModeResult(StrictModel):
    mode: str
    provider_identity: str
    available: bool
    exclusion_reason: str | None = None
    query_results: tuple[QueryEvaluationResult, ...] = ()
    metrics: EvaluationMetrics | None = None


class RetrievalEvaluationRun(StrictModel):
    schema_version: Literal["astra.rag.evaluation-run.v1"] = (
        "astra.rag.evaluation-run.v1"
    )
    run_id: str
    corpus_id: str
    deterministic_only: bool
    modes: tuple[EvaluationModeResult, ...]
    guardrails_passed: bool
    guardrail_failures: tuple[str, ...] = ()

