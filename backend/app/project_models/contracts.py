from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.project_control.contracts import (
    StrictModel,
    canonical_json,
    content_hash,
)

PROJECT_MODEL_INVOCATION_VERSION = "astra.project-models.invocation.v1"
MAX_MODEL_REQUEST_BYTES = 262_144
MAX_MODEL_RESULT_REFERENCE_BYTES = 65_536
MAX_MODEL_ERROR_BYTES = 4_096
MAX_MODEL_USAGE_BYTES = 16_384


class ProjectModelInvocationStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_MODEL_INVOCATION_STATUSES = frozenset(
    {
        ProjectModelInvocationStatus.SUCCEEDED,
        ProjectModelInvocationStatus.FAILED,
        ProjectModelInvocationStatus.CANCELLED,
    }
)


class ProjectModelInvocation(StrictModel):
    schema_version: Literal["astra.project-models.invocation.v1"] = (
        PROJECT_MODEL_INVOCATION_VERSION
    )
    invocation_id: str = Field(min_length=1, max_length=200)
    project_run_id: str = Field(min_length=1, max_length=200)
    coordinator_intent_id: str | None = Field(default=None, max_length=200)
    purpose: str = Field(min_length=1, max_length=120)
    evidence_hash: str = Field(min_length=64, max_length=64)
    provider: str = Field(min_length=1, max_length=120)
    model_profile: str = Field(min_length=1, max_length=200)
    provider_endpoint_identity: str = Field(
        default="unspecified", min_length=1, max_length=2048
    )
    provider_model_id: str = Field(default="unspecified", min_length=1, max_length=300)
    request_payload: dict[str, Any]
    request_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=300)
    status: ProjectModelInvocationStatus = ProjectModelInvocationStatus.PENDING
    lease_owner: str | None = Field(default=None, max_length=200)
    lease_token_hash: str | None = Field(default=None, min_length=64, max_length=64)
    lease_expires_at: datetime | None = None
    result_hash: str | None = Field(default=None, min_length=64, max_length=64)
    result_reference: dict[str, Any] | None = None
    failure_classification: str | None = Field(default=None, max_length=120)
    error_message: str | None = Field(default=None, max_length=MAX_MODEL_ERROR_BYTES)
    usage: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_integrity(self) -> "ProjectModelInvocation":
        if self.request_hash != content_hash(self.request_payload):
            raise ValueError("model invocation request hash does not match its payload")
        if (
            len(canonical_json(self.request_payload).encode("utf-8"))
            > MAX_MODEL_REQUEST_BYTES
        ):
            raise ValueError(
                "model invocation request exceeds the configured byte limit"
            )
        if (
            self.result_reference is not None
            and len(canonical_json(self.result_reference).encode("utf-8"))
            > MAX_MODEL_RESULT_REFERENCE_BYTES
        ):
            raise ValueError(
                "model invocation result reference exceeds the configured byte limit"
            )
        if len(canonical_json(self.usage).encode("utf-8")) > MAX_MODEL_USAGE_BYTES:
            raise ValueError("model invocation usage exceeds the configured byte limit")
        if (
            self.error_message is not None
            and len(self.error_message.encode("utf-8")) > MAX_MODEL_ERROR_BYTES
        ):
            raise ValueError("model invocation error exceeds the configured byte limit")
        leased = self.status == ProjectModelInvocationStatus.CLAIMED
        if leased != bool(
            self.lease_owner and self.lease_token_hash and self.lease_expires_at
        ):
            raise ValueError(
                "claimed model invocations require one complete lease binding"
            )
        if (
            self.status in TERMINAL_MODEL_INVOCATION_STATUSES
            and self.completed_at is None
        ):
            raise ValueError("terminal model invocations require completed_at")
        if (
            self.status not in TERMINAL_MODEL_INVOCATION_STATUSES
            and self.completed_at is not None
        ):
            raise ValueError("non-terminal model invocations cannot have completed_at")
        if (
            self.status == ProjectModelInvocationStatus.SUCCEEDED
            and not self.result_hash
        ):
            raise ValueError("successful model invocations require a result hash")
        if (
            self.status == ProjectModelInvocationStatus.FAILED
            and not self.failure_classification
        ):
            raise ValueError(
                "failed model invocations require a failure classification"
            )
        return self


def build_project_model_invocation(
    *,
    project_run_id: str,
    purpose: str,
    evidence_hash: str,
    provider: str,
    model_profile: str,
    provider_endpoint_identity: str = "unspecified",
    provider_model_id: str | None = None,
    request_payload: dict[str, Any],
    coordinator_intent_id: str | None = None,
    idempotency_key: str | None = None,
    created_at: datetime | None = None,
) -> ProjectModelInvocation:
    request_hash = content_hash(request_payload)
    key = idempotency_key or (
        f"model:{purpose}:{coordinator_intent_id or 'none'}:{evidence_hash}:{provider}:"
        f"{model_profile}"
    )
    invocation_id = f"model-invocation-{content_hash([project_run_id, key])[:24]}"
    now = created_at or datetime.now(timezone.utc)
    return ProjectModelInvocation(
        invocation_id=invocation_id,
        project_run_id=project_run_id,
        coordinator_intent_id=coordinator_intent_id,
        purpose=purpose,
        evidence_hash=evidence_hash,
        provider=provider,
        model_profile=model_profile,
        provider_endpoint_identity=provider_endpoint_identity,
        provider_model_id=provider_model_id or model_profile,
        request_payload=request_payload,
        request_hash=request_hash,
        idempotency_key=key,
        created_at=now,
        updated_at=now,
    )
