from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from backend.app.project_control.contracts import StrictModel, content_hash

EXECUTION_CANCELLATION_VERSION = "astra.project-control.execution-cancellation.v1"


class ExecutionCancellationStatus(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"


class ExecutionCancellation(StrictModel):
    """Delivery state for cancellation; never canonical project lifecycle authority."""

    schema_version: Literal["astra.project-control.execution-cancellation.v1"] = (
        EXECUTION_CANCELLATION_VERSION
    )
    cancellation_id: str = Field(min_length=1, max_length=200)
    project_run_id: str = Field(min_length=1, max_length=200)
    execution_attempt_id: str = Field(min_length=1, max_length=200)
    worker_request_id: str | None = Field(default=None, max_length=200)
    requested_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=600)
    request_hash: str = Field(min_length=64, max_length=64)
    status: ExecutionCancellationStatus = ExecutionCancellationStatus.PENDING
    failure_classification: str | None = Field(default=None, max_length=120)
    created_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "ExecutionCancellation":
        if self.request_hash != cancellation_request_hash(
            project_run_id=self.project_run_id,
            execution_attempt_id=self.execution_attempt_id,
            worker_request_id=self.worker_request_id,
            requested_by=self.requested_by,
            reason=self.reason,
        ):
            raise ValueError("cancellation request hash does not match its authority")
        if (self.status == ExecutionCancellationStatus.ACKNOWLEDGED) != bool(
            self.acknowledged_at
        ):
            raise ValueError("only acknowledged cancellations carry acknowledged_at")
        if (
            self.status == ExecutionCancellationStatus.FAILED
            and not self.failure_classification
        ):
            raise ValueError(
                "failed cancellation delivery requires a failure classification"
            )
        return self


def cancellation_request_hash(
    *,
    project_run_id: str,
    execution_attempt_id: str,
    worker_request_id: str | None,
    requested_by: str,
    reason: str,
) -> str:
    return content_hash(
        {
            "project_run_id": project_run_id,
            "execution_attempt_id": execution_attempt_id,
            "worker_request_id": worker_request_id,
            "requested_by": requested_by,
            "reason": reason,
        }
    )


def build_execution_cancellation(
    *,
    project_run_id: str,
    execution_attempt_id: str,
    worker_request_id: str | None,
    requested_by: str,
    reason: str,
    created_at: datetime | None = None,
) -> ExecutionCancellation:
    now = created_at or datetime.now(timezone.utc)
    request_hash = cancellation_request_hash(
        project_run_id=project_run_id,
        execution_attempt_id=execution_attempt_id,
        worker_request_id=worker_request_id,
        requested_by=requested_by,
        reason=reason,
    )
    return ExecutionCancellation(
        cancellation_id=f"cancellation-{content_hash([project_run_id, execution_attempt_id, request_hash])[:24]}",
        project_run_id=project_run_id,
        execution_attempt_id=execution_attempt_id,
        worker_request_id=worker_request_id,
        requested_by=requested_by,
        reason=reason,
        request_hash=request_hash,
        created_at=now,
        updated_at=now,
    )
