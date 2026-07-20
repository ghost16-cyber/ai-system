from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from backend.app.project_control.contracts import StrictModel


COORDINATOR_INTENT_VERSION = "astra.project-coordinator.intent.v1"


class CoordinatorIntentType(StrEnum):
    PREPARE_WORK_UNIT = "prepare_work_unit"
    RUN_DETERMINISTIC_VERIFICATION = "run_deterministic_verification"
    PREPARE_REPAIR = "prepare_repair"
    PREPARE_HANDOFF = "prepare_handoff"


class CoordinatorIntentStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CoordinatorIntent(StrictModel):
    schema_version: str = COORDINATOR_INTENT_VERSION
    coordinator_intent_id: str
    project_run_id: str
    intent_type: CoordinatorIntentType
    status: CoordinatorIntentStatus
    trigger_event_id: str
    idempotency_key: str = Field(min_length=1, max_length=240)
    plan_revision_id: str
    scope_revision_id: str
    manifest_hash: str = Field(min_length=64, max_length=64)
    expected_project_state_version: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str = Field(min_length=64, max_length=64)
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    result_reference: dict[str, Any] | None = None
    failure_classification: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


__all__ = [
    "COORDINATOR_INTENT_VERSION",
    "CoordinatorIntent",
    "CoordinatorIntentStatus",
    "CoordinatorIntentType",
]
