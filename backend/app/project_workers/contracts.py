from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.project_control.contracts import ExecutionAttemptType


WORKER_REQUEST_VERSION = "astra.project-workers.request.v1"
WORKER_ENQUEUE_VERSION = "astra.project-workers.enqueue.v1"
WORKER_LEASE_VERSION = "astra.project-workers.lease.v1"
WORKER_COMPLETION_VERSION = "astra.project-workers.completion.v1"
WORKER_EVENT_VERSION = "astra.project-workers.event.v1"
WORKER_RECOVERY_VERSION = "astra.project-workers.recovery.v1"
WORKER_DISPATCH_REPORT_VERSION = "astra.project-workers.dispatch-report.v1"
WORKER_RUNTIME_INSTANCE_VERSION = "astra.project-workers.runtime-instance.v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkerRequestStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    TIMED_OUT = "timed_out"


TERMINAL_WORKER_STATUSES = frozenset(
    {
        WorkerRequestStatus.SUCCEEDED,
        WorkerRequestStatus.FAILED,
        WorkerRequestStatus.CANCELLED,
        WorkerRequestStatus.INTERRUPTED,
        WorkerRequestStatus.TIMED_OUT,
    }
)


class WorkerCompletionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class WorkerEventType(StrEnum):
    ENQUEUED = "enqueued"
    LEASED = "leased"
    HEARTBEAT = "heartbeat"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    CANONICAL_RECONCILED = "canonical_reconciled"


class WorkerLimits(StrictModel):
    lease_seconds: int = Field(default=30, ge=5, le=3600)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    max_output_bytes: int = Field(default=1_048_576, ge=1024, le=20_971_520)
    max_deliveries: int = Field(default=1, ge=1, le=5)
    memory_limit_mb: int | None = Field(default=None, ge=64, le=65_536)
    cpu_time_seconds: int | None = Field(default=None, ge=1, le=3600)


class WorkerEnqueueCommand(StrictModel):
    schema_version: str = WORKER_ENQUEUE_VERSION
    project_run_id: str = Field(min_length=1, max_length=160)
    execution_attempt_id: str = Field(min_length=1, max_length=160)
    attempt_type: ExecutionAttemptType
    conversation_id: str = Field(min_length=1, max_length=160)
    workspace_id: str = Field(min_length=1, max_length=160)
    repository_root: str = Field(min_length=1, max_length=4000)
    repository_root_fingerprint: str = Field(min_length=1, max_length=256)
    actor_id: str = Field(min_length=1, max_length=256)
    plan_revision_id: str = Field(min_length=1, max_length=160)
    scope_revision_id: str = Field(min_length=1, max_length=160)
    manifest_hash: str = Field(min_length=64, max_length=64)
    expected_project_state_version: int = Field(ge=1)
    authority: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)
    priority: int = Field(default=0, ge=-100, le=100)
    available_at: datetime | None = None
    limits: WorkerLimits = Field(default_factory=WorkerLimits)


class ProjectWorkerRequest(StrictModel):
    schema_version: str = WORKER_REQUEST_VERSION
    worker_request_id: str
    project_run_id: str
    execution_attempt_id: str
    attempt_type: ExecutionAttemptType
    conversation_id: str
    workspace_id: str
    repository_root: str
    repository_root_fingerprint: str
    actor_id: str
    plan_revision_id: str
    scope_revision_id: str
    manifest_hash: str = Field(min_length=64, max_length=64)
    expected_project_state_version: int = Field(ge=1)
    authority: dict[str, Any]
    payload: dict[str, Any]
    payload_hash: str = Field(min_length=64, max_length=64)
    request_hash: str = Field(min_length=64, max_length=64)
    enqueue_idempotency_key: str
    priority: int
    limits: WorkerLimits
    status: WorkerRequestStatus
    available_at: datetime
    delivery_count: int = Field(ge=0)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    cancellation_requested_at: datetime | None = None
    result_reference: dict[str, Any] | None = None
    resulting_manifest_hash: str | None = None
    failure_classification: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    canonical_reconciled_at: datetime | None = None


class WorkerLease(StrictModel):
    schema_version: str = WORKER_LEASE_VERSION
    request: ProjectWorkerRequest
    lease_token: str = Field(min_length=32, max_length=256)


class WorkerCompletion(StrictModel):
    schema_version: str = WORKER_COMPLETION_VERSION
    worker_request_id: str = Field(min_length=1, max_length=160)
    worker_id: str = Field(min_length=1, max_length=160)
    lease_token: str = Field(min_length=32, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=200)
    outcome: WorkerCompletionOutcome
    result_reference: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    resulting_manifest_hash: str | None = Field(default=None, min_length=64, max_length=64)
    failure_classification: str | None = Field(default=None, max_length=160)


class ProjectWorkerEvent(StrictModel):
    schema_version: str = WORKER_EVENT_VERSION
    event_id: str
    worker_request_id: str
    project_run_id: str
    sequence: int = Field(ge=1)
    event_type: WorkerEventType
    worker_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class WorkerDispatchReport(StrictModel):
    schema_version: str = WORKER_DISPATCH_REPORT_VERSION
    dispatched_request_ids: tuple[str, ...] = ()
    recovered_dispatch_ids: tuple[str, ...] = ()
    deferred_dispatch_ids: tuple[str, ...] = ()


class WorkerRuntimeInstance(StrictModel):
    schema_version: str = WORKER_RUNTIME_INSTANCE_VERSION
    worker_id: str = Field(min_length=1, max_length=160)
    execution_backend: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=40)
    supported_attempt_types: tuple[ExecutionAttemptType, ...] = ()
    supported_toolchains: tuple[str, ...] = ()
    started_at: datetime
    last_heartbeat_at: datetime


class WorkerRecoveryReport(StrictModel):
    schema_version: str = WORKER_RECOVERY_VERSION
    recovered_request_ids: tuple[str, ...] = ()
    canonical_recovery_ids: tuple[str, ...] = ()
    skipped_request_ids: tuple[str, ...] = ()


__all__ = [
    "ProjectWorkerEvent",
    "ProjectWorkerRequest",
    "TERMINAL_WORKER_STATUSES",
    "WorkerCompletion",
    "WorkerCompletionOutcome",
    "WorkerEnqueueCommand",
    "WorkerDispatchReport",
    "WorkerEventType",
    "WorkerLease",
    "WorkerLimits",
    "WorkerRecoveryReport",
    "WorkerRequestStatus",
    "WorkerRuntimeInstance",
]
