from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.project_control.contracts import StrictModel, canonical_json
from backend.app.local_ai.contracts import (
    AdmissionOutcome,
    ExecutionProvenance,
    SchedulerJob,
)


MAX_SYSTEM_INSTRUCTION_CHARS = 16_384
MAX_USER_CONTENT_CHARS = 65_536
MAX_CONTEXT_ITEMS = 16
MAX_CONTEXT_ITEM_CHARS = 16_384
MAX_CONTEXT_CHARS = 65_536
MAX_REQUEST_BYTES = 196_608
MAX_CORRELATION_FIELDS = 12


class GenerationPurpose(StrEnum):
    SYNTHESIS = "synthesis"
    CODING = "coding"
    PLANNING = "planning"
    REVIEW = "review"
    CHAT = "chat"


class GenerationState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GenerationFailureReason(StrEnum):
    LOCAL_AI_DISABLED = "local_ai_disabled"
    PROVIDER_UNSUPPORTED = "provider_unsupported"
    PROVIDER_UNREACHABLE = "provider_unreachable"
    EXACT_MODEL_UNAVAILABLE = "exact_model_unavailable"
    INVALID_REQUEST = "invalid_request"
    REQUEST_TOO_LARGE = "request_too_large"
    GENERATION_TIMEOUT = "generation_timeout"
    GENERATION_CANCELLED = "generation_cancelled"
    PROVIDER_REJECTED_REQUEST = "provider_rejected_request"
    MALFORMED_PROVIDER_RESPONSE = "malformed_provider_response"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    TARGET_SCHEMA_VALIDATION_FAILED = "target_schema_validation_failed"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    PERSISTENCE_FAILURE = "persistence_failure"
    INTERNAL_FAILURE = "internal_failure"


class GenerationParameters(StrictModel):
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    maximum_output_tokens: int = Field(default=1024, ge=1, le=65_536)


class GenerationContextItem(StrictModel):
    item_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=MAX_CONTEXT_ITEM_CHARS)


class GenerationCorrelation(StrictModel):
    actor_id: str | None = Field(default=None, min_length=1, max_length=200)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=200)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=200)
    project_run_id: str | None = Field(default=None, min_length=1, max_length=200)
    coordinator_intent_id: str | None = Field(default=None, min_length=1, max_length=200)
    attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def validate_attributes(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_CORRELATION_FIELDS:
            raise ValueError("too_many_correlation_fields")
        if any(
            not key or len(key) > 80 or not item or len(item) > 300
            for key, item in value.items()
        ):
            raise ValueError("invalid_correlation_metadata")
        return value


class LocalGenerationRequest(StrictModel):
    schema_version: Literal["astra.local-ai.generation-request.v1"] = (
        "astra.local-ai.generation-request.v1"
    )
    request_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    purpose: GenerationPurpose
    exact_model_tag: str = Field(min_length=1, max_length=300)
    system_instruction: str = Field(
        min_length=1, max_length=MAX_SYSTEM_INSTRUCTION_CHARS
    )
    user_content: str = Field(min_length=1, max_length=MAX_USER_CONTENT_CHARS)
    context: tuple[GenerationContextItem, ...] = Field(
        default=(), max_length=MAX_CONTEXT_ITEMS
    )
    expected_response_schema_identity: str = Field(min_length=1, max_length=300)
    timeout_seconds: int = Field(ge=1, le=3600)
    parameters: GenerationParameters = Field(default_factory=GenerationParameters)
    correlation: GenerationCorrelation = Field(default_factory=GenerationCorrelation)

    @model_validator(mode="after")
    def enforce_aggregate_bounds(self) -> "LocalGenerationRequest":
        if sum(len(item.content) for item in self.context) > MAX_CONTEXT_CHARS:
            raise ValueError("context_too_large")
        payload_size = len(
            canonical_json(self.model_dump(mode="json")).encode("utf-8")
        )
        if payload_size > MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")
        return self


class GenerationUsage(StrictModel):
    prompt_eval_count: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)
    prompt_eval_duration_ns: int | None = Field(default=None, ge=0)
    eval_duration_ns: int | None = Field(default=None, ge=0)


class LocalGenerationResult(StrictModel):
    schema_version: Literal["astra.local-ai.generation-result.v1"] = (
        "astra.local-ai.generation-result.v1"
    )
    generation_id: str
    request_id: str
    provider_identity: str
    endpoint_identity: str
    exact_model_tag: str
    started_at: datetime
    completed_at: datetime
    duration_ms: int = Field(ge=0)
    state: GenerationState
    raw_response_hash: str | None = None
    structured_output: dict[str, Any] | None = None
    usage: GenerationUsage = Field(default_factory=GenerationUsage)
    failure_reason: GenerationFailureReason | None = None
    user_message: str
    replayed: bool = False
    replay_source_generation_id: str | None = None
    advisory_only: Literal[True] = True
    authority_granted: Literal[False] = False
    input_truncated: Literal[False] = False

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> "LocalGenerationResult":
        if self.state == GenerationState.SUCCEEDED:
            if self.structured_output is None or self.failure_reason is not None:
                raise ValueError("invalid_success_result")
        elif self.failure_reason is None or self.structured_output is not None:
            raise ValueError("invalid_failure_result")
        return self


class LocalAIAdvisoryResponse(StrictModel):
    """The only response shape accepted by the public runtime execution API."""

    schema_version: Literal["astra.local-ai.advisory-response.v1"] = (
        "astra.local-ai.advisory-response.v1"
    )
    response: str = Field(min_length=1, max_length=65_536)


class LocalAIExecutionRequest(StrictModel):
    schema_version: Literal["astra.local-ai.execution-request.v1"] = (
        "astra.local-ai.execution-request.v1"
    )
    request_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=160)
    actor_id: str = Field(min_length=1, max_length=200)
    model_profile_id: str = Field(min_length=1, max_length=200)
    exact_model_tag: str = Field(min_length=1, max_length=300)
    expected_configuration_version: int = Field(ge=1)
    purpose: GenerationPurpose
    expected_response_schema_identity: str = Field(
        default="astra.local-ai.advisory-response.v1", min_length=1, max_length=300
    )
    system_instruction: str = Field(
        min_length=1, max_length=MAX_SYSTEM_INSTRUCTION_CHARS
    )
    user_content: str = Field(min_length=1, max_length=MAX_USER_CONTENT_CHARS)
    context: tuple[GenerationContextItem, ...] = Field(
        default=(), max_length=MAX_CONTEXT_ITEMS
    )
    timeout_seconds: int = Field(ge=1, le=3600)
    parameters: GenerationParameters = Field(default_factory=GenerationParameters)
    allow_cpu_fallback: bool = False
    prefer_gpu: bool = True
    approved_admission_outcome: AdmissionOutcome | None = None
    conversation_id: str | None = Field(default=None, min_length=1, max_length=200)
    project_run_id: str | None = Field(default=None, min_length=1, max_length=200)
    coordinator_intent_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def enforce_aggregate_bounds(self) -> "LocalAIExecutionRequest":
        if sum(len(item.content) for item in self.context) > MAX_CONTEXT_CHARS:
            raise ValueError("context_too_large")
        if len(canonical_json(self.model_dump(mode="json")).encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")
        return self


class LocalAIExecutionState(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    IN_PROGRESS = "in_progress"
    CANCELLED = "cancelled"


class LocalAIExecutionResult(StrictModel):
    schema_version: Literal["astra.local-ai.execution-result.v1"] = (
        "astra.local-ai.execution-result.v1"
    )
    state: LocalAIExecutionState
    scheduler_job: SchedulerJob
    generation_result: LocalGenerationResult | None = None
    provenance: ExecutionProvenance | None = None
    advisory_only: Literal[True] = True
    authority_granted: Literal[False] = False


class ChatGenerationTargetStatus(StrEnum):
    """Outcome of resolving the exact model to use for GenerationPurpose.CHAT.

    Absence at any step (no configured role, no matching enabled profile) is a
    typed result, not an exception -- chat role mapping is never seeded or
    assigned automatically, so callers must be able to turn an unconfigured
    chat role into a deterministic fallback rather than a crash.
    """

    RESOLVED = "resolved"
    CHAT_ROLE_NOT_CONFIGURED = "chat_role_not_configured"
    MODEL_PROFILE_NOT_FOUND = "model_profile_not_found"
    MODEL_PROFILE_DISABLED = "model_profile_disabled"
    ROLE_MAPPING_MISMATCH = "role_mapping_mismatch"


class ChatGenerationTarget(StrictModel):
    """Read-only resolution of the chat generation target.

    Returned by LocalAIService.resolve_chat_generation_target() -- the only
    sanctioned way for chat code to learn which exact model, model profile,
    and configuration version apply to GenerationPurpose.CHAT. Chat code must
    never query local_ai_* tables directly.
    """

    schema_version: Literal["astra.local-ai.chat-generation-target.v1"] = (
        "astra.local-ai.chat-generation-target.v1"
    )
    status: ChatGenerationTargetStatus
    model_profile_id: str | None = None
    exact_model_tag: str | None = None
    configuration_version: int | None = Field(default=None, ge=1)
    estimated_model_bytes: int | None = Field(default=None, ge=0)
    operational_context: int | None = Field(default=None, ge=1)
    maximum_output_tokens: int | None = Field(default=None, ge=1)
    gpu_support: bool | None = None

    @model_validator(mode="after")
    def resolved_fields_present(self) -> "ChatGenerationTarget":
        resolved = self.status == ChatGenerationTargetStatus.RESOLVED
        fields = (
            self.model_profile_id,
            self.exact_model_tag,
            self.configuration_version,
            self.estimated_model_bytes,
            self.operational_context,
            self.maximum_output_tokens,
            self.gpu_support,
        )
        if resolved and any(field is None for field in fields):
            raise ValueError("resolved_chat_generation_target_missing_fields")
        if not resolved and any(field is not None for field in fields):
            raise ValueError("unresolved_chat_generation_target_has_fields")
        return self


__all__ = [
    "ChatGenerationTarget",
    "ChatGenerationTargetStatus",
    "GenerationContextItem",
    "GenerationCorrelation",
    "GenerationFailureReason",
    "GenerationParameters",
    "GenerationPurpose",
    "GenerationState",
    "GenerationUsage",
    "LocalAIAdvisoryResponse",
    "LocalAIExecutionRequest",
    "LocalAIExecutionResult",
    "LocalAIExecutionState",
    "LocalGenerationRequest",
    "LocalGenerationResult",
    "MAX_CONTEXT_CHARS",
    "MAX_CONTEXT_ITEM_CHARS",
    "MAX_CONTEXT_ITEMS",
    "MAX_REQUEST_BYTES",
    "MAX_SYSTEM_INSTRUCTION_CHARS",
    "MAX_USER_CONTENT_CHARS",
]
