from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.project_control.contracts import StrictModel, canonical_json


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


__all__ = [
    "GenerationContextItem",
    "GenerationCorrelation",
    "GenerationFailureReason",
    "GenerationParameters",
    "GenerationPurpose",
    "GenerationState",
    "GenerationUsage",
    "LocalGenerationRequest",
    "LocalGenerationResult",
    "MAX_CONTEXT_CHARS",
    "MAX_CONTEXT_ITEM_CHARS",
    "MAX_CONTEXT_ITEMS",
    "MAX_REQUEST_BYTES",
    "MAX_SYSTEM_INSTRUCTION_CHARS",
    "MAX_USER_CONTENT_CHARS",
]
