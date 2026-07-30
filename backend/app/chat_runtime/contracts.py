from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.local_ai.generation_contracts import GenerationFailureReason
from backend.app.project_control.contracts import StrictModel, canonical_json


MAX_LINEAGE_JSON_BYTES = 32_768


class ChatResponseMode(StrEnum):
    """How a chat turn's assistant_response was produced.

    Chat never has a third "legacy gateway" mode -- once Phase 9 is wired in,
    every terminal chat turn is either an advisory local-AI generation or a
    deterministic fallback that never touched a model.
    """

    LOCAL_AI = "local_ai"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class ChatRuntimeFailureReason(StrEnum):
    """Reasons a chat turn falls back to a deterministic response.

    Includes every GenerationFailureReason (a local-AI generation attempt was
    made and failed) plus chat-runtime-specific absence reasons that mean a
    generation was never attempted at all.
    """

    LOCAL_AI_DISABLED = GenerationFailureReason.LOCAL_AI_DISABLED.value
    PROVIDER_UNSUPPORTED = GenerationFailureReason.PROVIDER_UNSUPPORTED.value
    PROVIDER_UNREACHABLE = GenerationFailureReason.PROVIDER_UNREACHABLE.value
    EXACT_MODEL_UNAVAILABLE = GenerationFailureReason.EXACT_MODEL_UNAVAILABLE.value
    INVALID_REQUEST = GenerationFailureReason.INVALID_REQUEST.value
    REQUEST_TOO_LARGE = GenerationFailureReason.REQUEST_TOO_LARGE.value
    GENERATION_TIMEOUT = GenerationFailureReason.GENERATION_TIMEOUT.value
    GENERATION_CANCELLED = GenerationFailureReason.GENERATION_CANCELLED.value
    PROVIDER_REJECTED_REQUEST = GenerationFailureReason.PROVIDER_REJECTED_REQUEST.value
    MALFORMED_PROVIDER_RESPONSE = GenerationFailureReason.MALFORMED_PROVIDER_RESPONSE.value
    INVALID_STRUCTURED_OUTPUT = GenerationFailureReason.INVALID_STRUCTURED_OUTPUT.value
    TARGET_SCHEMA_VALIDATION_FAILED = GenerationFailureReason.TARGET_SCHEMA_VALIDATION_FAILED.value
    IDEMPOTENCY_CONFLICT = GenerationFailureReason.IDEMPOTENCY_CONFLICT.value
    PERSISTENCE_FAILURE = GenerationFailureReason.PERSISTENCE_FAILURE.value
    INTERNAL_FAILURE = GenerationFailureReason.INTERNAL_FAILURE.value
    RUNTIME_NOT_READY = "runtime_not_ready"
    CHAT_ROLE_NOT_CONFIGURED = "chat_role_not_configured"
    MODEL_PROFILE_NOT_FOUND = "model_profile_not_found"
    MODEL_PROFILE_DISABLED = "model_profile_disabled"
    ROLE_MAPPING_MISMATCH = "role_mapping_mismatch"
    ADMISSION_BLOCKED = "admission_blocked"
    GENERATION_IN_PROGRESS = "generation_in_progress"
    GENERATION_CANCELLED_BY_SCHEDULER = "generation_cancelled_by_scheduler"
    STALE_PROJECT_BINDING = "stale_project_binding"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"


class ChatRuntimeGenerationSummary(StrictModel):
    """Bounded, advisory-only summary of one local-AI generation attempt.

    Reduces LocalAIExecutionResult/LocalGenerationResult/ExecutionProvenance
    into exactly what chat lineage needs to persist and replay -- never the
    full structured output or raw response.
    """

    schema_version: Literal["astra.chat-runtime.generation-summary.v1"] = (
        "astra.chat-runtime.generation-summary.v1"
    )
    generation_id: str = Field(min_length=1, max_length=200)
    scheduler_job_id: str = Field(min_length=1, max_length=200)
    provenance_execution_id: str | None = Field(default=None, max_length=200)
    model_profile_id: str = Field(min_length=1, max_length=200)
    exact_model_tag: str = Field(min_length=1, max_length=300)
    configuration_version: int = Field(ge=1)
    provider_identity: str = Field(min_length=1, max_length=300)
    duration_ms: int = Field(ge=0)
    replayed: bool = False
    response_hash: str | None = Field(default=None, min_length=64, max_length=64)
    advisory_only: Literal[True] = True
    authority_granted: Literal[False] = False


class ChatEvidenceCitation(StrictModel):
    """One bounded, untrusted citation surfaced to the chat UI.

    Reduced from a RetrievalEvidenceItem -- carries enough to render and
    re-verify a citation, never the full retrieval evidence contract.
    """

    schema_version: Literal["astra.chat-runtime.evidence-citation.v1"] = (
        "astra.chat-runtime.evidence-citation.v1"
    )
    citation_label: str = Field(min_length=1, max_length=100)
    evidence_id: str = Field(min_length=1, max_length=200)
    relative_path: str = Field(min_length=1, max_length=2048)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    text_hash: str = Field(min_length=64, max_length=64)
    trust_class: Literal["untrusted_retrieved_content"] = "untrusted_retrieved_content"


class ChatRuntimeRetrievalSummary(StrictModel):
    """Bounded summary of one project-bound retrieval used by a chat turn.

    Reduced from RetrievalEvidenceArtifact. Only ever present when the chat
    request carried a canonical project id -- generic chat never retrieves.
    """

    schema_version: Literal["astra.chat-runtime.retrieval-summary.v1"] = (
        "astra.chat-runtime.retrieval-summary.v1"
    )
    project_run_id: str = Field(min_length=1, max_length=200)
    retrieval_artifact_id: str = Field(min_length=1, max_length=200)
    retrieval_artifact_hash: str = Field(min_length=64, max_length=64)
    evidence_count: int = Field(ge=0)
    citations: tuple[ChatEvidenceCitation, ...] = ()
    replayed: bool = False
    invalidated: bool = False
    advisory_only: Literal[True] = True
    has_execution_authority: Literal[False] = False
    has_approval_authority: Literal[False] = False
    has_mutation_authority: Literal[False] = False

    @model_validator(mode="after")
    def citation_count_matches(self) -> "ChatRuntimeRetrievalSummary":
        if len(self.citations) != self.evidence_count:
            raise ValueError("citation_count_does_not_match_evidence_count")
        return self


class ChatRuntimeFailure(StrictModel):
    """A typed, deterministic reason a chat turn did not use local-AI generation."""

    schema_version: Literal["astra.chat-runtime.failure.v1"] = (
        "astra.chat-runtime.failure.v1"
    )
    reason: ChatRuntimeFailureReason
    detail: str = Field(min_length=1, max_length=500)
    occurred_at: datetime


class ChatRuntimeLineage(StrictModel):
    """The full, bounded lineage record persisted to chat_runtime_links.

    This is the exclusive shape of chat_runtime_links.lineage_json -- it ties
    one chat request/run pair to the exact canonical authorities (retrieval,
    generation, provenance) that produced the response, or to a typed failure
    if none were used.
    """

    schema_version: Literal["astra.chat-runtime.lineage.v1"] = (
        "astra.chat-runtime.lineage.v1"
    )
    chat_request_id: str = Field(min_length=1, max_length=200)
    chat_run_id: str = Field(min_length=1, max_length=200)
    request_fingerprint: str = Field(min_length=64, max_length=64)
    response_mode: ChatResponseMode
    retrieval: ChatRuntimeRetrievalSummary | None = None
    generation: ChatRuntimeGenerationSummary | None = None
    failure: ChatRuntimeFailure | None = None
    created_at: datetime

    @model_validator(mode="after")
    def mode_matches_content(self) -> "ChatRuntimeLineage":
        if self.response_mode == ChatResponseMode.LOCAL_AI:
            if self.generation is None or self.failure is not None:
                raise ValueError("local_ai_response_mode_requires_a_generation_summary")
        else:
            if self.generation is not None or self.failure is None:
                raise ValueError("deterministic_fallback_response_mode_requires_a_failure")
        return self

    @model_validator(mode="after")
    def bounded_size(self) -> "ChatRuntimeLineage":
        encoded = canonical_json(self.model_dump(mode="json")).encode("utf-8")
        if len(encoded) > MAX_LINEAGE_JSON_BYTES:
            raise ValueError("chat_runtime_lineage_exceeds_its_byte_limit")
        return self


def citation_from_evidence(evidence: Any, *, citation_label: str | None = None) -> ChatEvidenceCitation:
    """Build a bounded ChatEvidenceCitation from a project_retrieval RetrievalEvidenceItem."""

    return ChatEvidenceCitation(
        citation_label=citation_label or evidence.citation_label,
        evidence_id=evidence.evidence_id,
        relative_path=evidence.relative_path,
        line_start=evidence.line_start,
        line_end=evidence.line_end,
        text_hash=evidence.text_hash,
    )


__all__ = [
    "MAX_LINEAGE_JSON_BYTES",
    "ChatEvidenceCitation",
    "ChatResponseMode",
    "ChatRuntimeFailure",
    "ChatRuntimeFailureReason",
    "ChatRuntimeGenerationSummary",
    "ChatRuntimeLineage",
    "ChatRuntimeRetrievalSummary",
    "citation_from_evidence",
]
