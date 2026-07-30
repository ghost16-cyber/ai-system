from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.project_control.contracts import StrictModel, canonical_json, content_hash


CHUNKING_POLICY_VERSION = "astra.rag.chunking.v1"
RETRIEVAL_POLICY_VERSION = "astra.rag.retrieval.v1"
BM25_POLICY_VERSION = "astra.rag.bm25.v1"
EMBEDDING_POLICY_VERSION = "astra.rag.embedding.v1"
RERANK_POLICY_VERSION = "astra.rag.rerank.v1"
MAX_QUERY_CHARS = 4_000
MAX_CANDIDATES = 50
MAX_RERANK = 20
MAX_EVIDENCE = 8
MAX_EVIDENCE_TEXT_CHARS = 8_000
MAX_EVIDENCE_TOTAL_CHARS = 48_000


class SourceKind(StrEnum):
    REPOSITORY_TEXT_FILE = "repository_text_file"
    PROJECT_ARTIFACT = "project_artifact"
    IMPORTED_TEXT_DOCUMENT = "imported_text_document"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"


class BindingStatus(StrEnum):
    EXACT = "exact"
    INVALIDATED = "invalidated"


class TrustClass(StrEnum):
    UNTRUSTED_RETRIEVED_CONTENT = "untrusted_retrieved_content"


class CorpusSource(StrictModel):
    schema_version: Literal["astra.rag.corpus-source.v1"] = "astra.rag.corpus-source.v1"
    source_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    repository_root: str = Field(min_length=1, max_length=4096)
    relative_path: str = Field(min_length=1, max_length=2048)
    source_kind: SourceKind
    content_hash: str = Field(min_length=64, max_length=64)
    byte_size: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=120)
    encoding: Literal["utf-8"] = "utf-8"
    repository_manifest_hash: str = Field(min_length=64, max_length=64)
    repository_state_hash: str = Field(min_length=64, max_length=64)
    scope_revision_id: str = Field(min_length=1, max_length=200)
    scope_hash: str = Field(min_length=64, max_length=64)
    ingested_at: datetime
    freshness_observed_at: datetime
    is_deleted: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_metadata(self) -> "CorpusSource":
        if len(canonical_json(self.metadata).encode("utf-8")) > 16_384:
            raise ValueError("source metadata exceeds its byte limit")
        return self


class DocumentChunk(StrictModel):
    schema_version: Literal["astra.rag.document-chunk.v1"] = "astra.rag.document-chunk.v1"
    chunk_id: str = Field(min_length=1, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    relative_path: str = Field(min_length=1, max_length=2048)
    ordinal: int = Field(ge=0)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    heading_path: tuple[str, ...] = ()
    language: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=16_384)
    normalized_text_hash: str = Field(min_length=64, max_length=64)
    source_content_hash: str = Field(min_length=64, max_length=64)
    chunking_policy_version: Literal["astra.rag.chunking.v1"] = CHUNKING_POLICY_VERSION
    token_estimate: int = Field(ge=1)
    created_at: datetime

    @model_validator(mode="after")
    def valid_bounds(self) -> "DocumentChunk":
        if self.end_offset <= self.start_offset or self.end_line < self.start_line:
            raise ValueError("chunk boundaries are invalid")
        return self


class ProjectRetrievalBinding(StrictModel):
    project_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    repository_root: str = Field(min_length=1, max_length=4096)
    scope_revision_id: str = Field(min_length=1, max_length=200)
    scope_hash: str = Field(min_length=64, max_length=64)
    plan_revision_id: str | None = Field(default=None, max_length=200)
    plan_hash: str | None = Field(default=None, min_length=64, max_length=64)
    repository_manifest_hash: str = Field(min_length=64, max_length=64)
    repository_state_hash: str = Field(min_length=64, max_length=64)
    expected_project_state_version: int = Field(ge=1)
    authority_id: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def plan_pair(self) -> "ProjectRetrievalBinding":
        if (self.plan_revision_id is None) != (self.plan_hash is None):
            raise ValueError("plan revision and plan hash must be supplied together")
        return self


class CorpusIngestionRequest(ProjectRetrievalBinding):
    schema_version: Literal["astra.rag.ingestion-request.v1"] = "astra.rag.ingestion-request.v1"
    idempotency_key: str = Field(min_length=1, max_length=200)
    chunking_policy_version: Literal["astra.rag.chunking.v1"] = CHUNKING_POLICY_VERSION


class CorpusGeneration(StrictModel):
    schema_version: Literal["astra.rag.corpus-generation.v1"] = "astra.rag.corpus-generation.v1"
    generation_id: str
    project_id: str
    repository_state_hash: str = Field(min_length=64, max_length=64)
    repository_manifest_hash: str = Field(min_length=64, max_length=64)
    scope_revision_id: str
    scope_hash: str = Field(min_length=64, max_length=64)
    chunking_policy_version: str
    source_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    created_at: datetime
    replayed: bool = False


class RetrievalRequest(ProjectRetrievalBinding):
    schema_version: Literal["astra.rag.retrieval-request.v1"] = "astra.rag.retrieval-request.v1"
    request_id: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    normalized_query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    query_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=200)
    retrieval_policy_version: Literal["astra.rag.retrieval.v1"] = RETRIEVAL_POLICY_VERSION
    max_candidates: int = Field(default=30, ge=1, le=MAX_CANDIDATES)
    max_rerank: int = Field(default=12, ge=1, le=MAX_RERANK)
    max_evidence: int = Field(default=6, ge=1, le=MAX_EVIDENCE)
    created_at: datetime

    @model_validator(mode="after")
    def valid_query_binding(self) -> "RetrievalRequest":
        if self.normalized_query != normalize_query(self.query):
            raise ValueError("normalized query does not match the exact query")
        if self.query_hash != content_hash(self.normalized_query):
            raise ValueError("query hash does not match normalized query")
        if self.max_evidence > self.max_rerank or self.max_rerank > self.max_candidates:
            raise ValueError("retrieval bounds must be monotonically decreasing")
        return self


class RetrievalCandidate(StrictModel):
    chunk_id: str
    source_id: str
    bm25_score: float = Field(ge=0.0)
    semantic_score: float = Field(ge=0.0, le=1.0)
    hybrid_score: float = Field(ge=0.0, le=1.0)
    rank_before_rerank: int = Field(ge=1)
    retrieval_reasons: tuple[str, ...] = ()

    @field_validator("bm25_score", "semantic_score", "hybrid_score")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("retrieval score must be finite")
        return value


class RetrievalEvidenceItem(StrictModel):
    evidence_id: str
    chunk_id: str
    source_id: str
    relative_path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=MAX_EVIDENCE_TEXT_CHARS)
    text_hash: str = Field(min_length=64, max_length=64)
    source_content_hash: str = Field(min_length=64, max_length=64)
    bm25_score: float = Field(ge=0)
    semantic_score: float = Field(ge=0, le=1)
    hybrid_score: float = Field(ge=0, le=1)
    rerank_score: float = Field(ge=0, le=1)
    final_rank: int = Field(ge=1)
    freshness_status: FreshnessStatus = FreshnessStatus.FRESH
    binding_status: BindingStatus = BindingStatus.EXACT
    trust_class: Literal["untrusted_retrieved_content"] = (
        TrustClass.UNTRUSTED_RETRIEVED_CONTENT.value
    )
    citation_label: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def exact_text_hash(self) -> "RetrievalEvidenceItem":
        if self.text_hash != content_hash(self.text):
            raise ValueError("retrieval evidence text hash is invalid")
        return self


class RetrievalProviderTrace(StrictModel):
    schema_version: Literal["astra.rag.provider-trace.v1"] = (
        "astra.rag.provider-trace.v1"
    )
    requested_embedding_identity: str
    effective_embedding_identity: str
    embedding_model_id: str
    embedding_resolved_revision: str
    embedding_dimensions: int = Field(ge=1)
    embedding_policy_version: str
    transformed_query_hash: str = Field(min_length=64, max_length=64)
    embedding_device: str | None = None
    requested_reranker_identity: str
    effective_reranker_identity: str
    reranker_model_id: str
    reranker_resolved_revision: str
    reranking_policy_version: str
    reranker_device: str | None = None
    fallback_used: bool
    fallback_reason: str | None = Field(default=None, max_length=300)
    query_embedding_latency_ms: float = Field(ge=0)
    reranking_latency_ms: float = Field(ge=0)


class RetrievalEvidenceArtifact(StrictModel):
    schema_version: Literal["astra.rag.retrieval-evidence.v1"] = "astra.rag.retrieval-evidence.v1"
    artifact_id: str
    artifact_hash: str = Field(min_length=64, max_length=64)
    project_id: str
    conversation_id: str
    actor_id: str
    workspace_id: str
    repository_root: str
    request_id: str
    query_hash: str = Field(min_length=64, max_length=64)
    scope_revision_id: str
    scope_hash: str = Field(min_length=64, max_length=64)
    plan_revision_id: str | None
    plan_hash: str | None = Field(default=None, min_length=64, max_length=64)
    repository_manifest_hash: str = Field(min_length=64, max_length=64)
    repository_state_hash: str = Field(min_length=64, max_length=64)
    project_state_version: int = Field(ge=1)
    authority_id: str = Field(min_length=64, max_length=64)
    retrieval_policy_version: str
    chunking_policy_version: str
    embedding_model_identity: str
    embedding_model_version: str
    reranker_identity: str
    reranker_version: str
    provider_trace: RetrievalProviderTrace | None = None
    candidate_count: int = Field(ge=0)
    reranked_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0, le=MAX_EVIDENCE)
    evidence: tuple[RetrievalEvidenceItem, ...] = ()
    created_at: datetime
    freshness_checked_at: datetime
    replayed: bool = False
    invalidated: bool = False
    advisory_only: Literal[True] = True
    has_execution_authority: Literal[False] = False
    has_approval_authority: Literal[False] = False
    has_mutation_authority: Literal[False] = False

    @model_validator(mode="after")
    def evidence_bounds(self) -> "RetrievalEvidenceArtifact":
        if self.evidence_count != len(self.evidence):
            raise ValueError("retrieval evidence count is invalid")
        if sum(len(item.text) for item in self.evidence) > MAX_EVIDENCE_TOTAL_CHARS:
            raise ValueError("retrieval evidence exceeds its aggregate text limit")
        return self


class RetrievalStatus(StrictModel):
    schema_version: Literal["astra.rag.status.v1"] = "astra.rag.status.v1"
    project_id: str
    current_generation_id: str | None = None
    active_source_count: int = Field(ge=0)
    active_chunk_count: int = Field(ge=0)
    embedding_ready: bool
    embedding_identity: str
    reranker_identity: str
    last_ingestion_at: datetime | None = None
    invalidated: bool
    invalidation_reasons: tuple[str, ...] = ()
    advisory_only: Literal[True] = True
    has_execution_authority: Literal[False] = False
    has_approval_authority: Literal[False] = False
    has_mutation_authority: Literal[False] = False


class RetrievalProviderReadiness(StrictModel):
    schema_version: Literal["astra.rag.provider-readiness.v1"] = (
        "astra.rag.provider-readiness.v1"
    )
    provider_kind: Literal["embedding", "reranker"]
    provider_type: str
    configured_model_id: str
    resolved_revision: str
    effective_identity: str = Field(min_length=1, max_length=300)
    package_installed: bool
    model_present_locally: bool
    ready: bool
    reason: str | None = None
    requested_device: Literal["cpu", "cuda", "auto"]
    admitted_device: Literal["cpu", "cuda"] | None = None
    dimensions: int | None = Field(default=None, ge=1)
    maximum_sequence_length: int | None = Field(default=None, ge=1)
    batch_size: int = Field(ge=1, le=128)
    maximum_batch_size: int = Field(ge=1, le=128)
    normalization_required: bool
    local_files_only: Literal[True] = True
    deterministic_fallback_available: Literal[True] = True
    estimated_ram_bytes: int = Field(ge=0)
    estimated_vram_bytes: int = Field(ge=0)
    last_failure: str | None = Field(default=None, max_length=300)
    advisory_only: Literal[True] = True
    has_execution_authority: Literal[False] = False
    has_approval_authority: Literal[False] = False
    has_mutation_authority: Literal[False] = False


class RetrievalProviderCollection(StrictModel):
    schema_version: Literal["astra.rag.provider-collection.v1"] = (
        "astra.rag.provider-collection.v1"
    )
    project_id: str
    items: tuple[RetrievalProviderReadiness, ...]
    advisory_only: Literal[True] = True


class RetrievalArtifactCollection(StrictModel):
    schema_version: Literal["astra.rag.artifact-collection.v1"] = "astra.rag.artifact-collection.v1"
    project_id: str
    items: tuple[RetrievalEvidenceArtifact, ...]
    count: int = Field(ge=0)


class RetrievalPhase5BEvidence(StrictModel):
    schema_version: Literal["astra.rag.phase5b-evidence.v1"] = (
        "astra.rag.phase5b-evidence.v1"
    )
    retrieval_artifact_id: str = Field(min_length=1, max_length=200)
    retrieval_artifact_hash: str = Field(min_length=64, max_length=64)
    project_id: str = Field(min_length=1, max_length=200)
    scope_revision_id: str = Field(min_length=1, max_length=200)
    scope_hash: str = Field(min_length=64, max_length=64)
    plan_revision_id: str | None = Field(default=None, max_length=200)
    plan_hash: str | None = Field(default=None, min_length=64, max_length=64)
    repository_manifest_hash: str = Field(min_length=64, max_length=64)
    repository_state_hash: str = Field(min_length=64, max_length=64)
    project_state_version: int = Field(ge=1)
    authority_id: str = Field(min_length=64, max_length=64)
    evidence: tuple[RetrievalEvidenceItem, ...] = Field(max_length=MAX_EVIDENCE)
    provider_trace: RetrievalProviderTrace | None = None
    advisory_only: Literal[True] = True
    untrusted_content: Literal[True] = True
    has_execution_authority: Literal[False] = False
    has_approval_authority: Literal[False] = False
    has_mutation_authority: Literal[False] = False

    @model_validator(mode="after")
    def bounded_evidence(self) -> "RetrievalPhase5BEvidence":
        if sum(len(item.text) for item in self.evidence) > MAX_EVIDENCE_TOTAL_CHARS:
            raise ValueError("Phase 5B retrieval evidence exceeds its aggregate limit")
        return self


def normalize_query(value: str) -> str:
    normalized = " ".join(value.casefold().split())
    if not normalized:
        raise ValueError("retrieval query is effectively empty")
    return normalized


__all__ = [
    "BM25_POLICY_VERSION", "BindingStatus", "CHUNKING_POLICY_VERSION",
    "CorpusGeneration", "CorpusIngestionRequest", "CorpusSource", "DocumentChunk",
    "EMBEDDING_POLICY_VERSION", "FreshnessStatus", "MAX_CANDIDATES", "MAX_EVIDENCE",
    "MAX_RERANK", "ProjectRetrievalBinding", "RERANK_POLICY_VERSION",
    "RETRIEVAL_POLICY_VERSION", "RetrievalArtifactCollection", "RetrievalCandidate",
    "RetrievalEvidenceArtifact", "RetrievalEvidenceItem", "RetrievalRequest",
    "RetrievalPhase5BEvidence", "RetrievalProviderCollection",
    "RetrievalProviderReadiness", "RetrievalProviderTrace", "RetrievalStatus",
    "SourceKind", "TrustClass", "normalize_query",
]
