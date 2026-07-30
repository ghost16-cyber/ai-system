from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.project_control.contracts import StrictModel, canonical_json


REPAIR_CYCLE_VERSION = "astra.project-repair.cycle.v1"
MAX_FAILURE_SUMMARY_BYTES = 32_768
MAX_DIAGNOSIS_BYTES = 65_536
MAX_REPAIR_PREVIEW_BYTES = 131_072


class RepairCycleStatus(StrEnum):
    DIAGNOSIS_PENDING = "diagnosis_pending"
    DIAGNOSED = "diagnosed"
    PREVIEW_READY = "preview_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class FailureEvidenceArtifact(StrictModel):
    project_run_id: str
    execution_attempt_id: str
    failure_classification: str = Field(min_length=1, max_length=120)
    domain_failure: bool
    result_reference: dict[str, Any] = Field(default_factory=dict)
    authority: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self) -> "FailureEvidenceArtifact":
        if len(canonical_json(self.model_dump(mode="json")).encode("utf-8")) > MAX_FAILURE_SUMMARY_BYTES:
            raise ValueError("failure evidence exceeds the byte limit")
        return self


class DiagnosisArtifact(StrictModel):
    repair_cycle_id: str
    failure_artifact_id: str
    strategy: Literal["deterministic", "model_assisted"]
    root_causes: tuple[dict[str, Any], ...]
    repair_scope: tuple[str, ...]
    confidence: str = Field(min_length=1, max_length=40)
    uncertainty_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def bounded(self) -> "DiagnosisArtifact":
        if len(canonical_json(self.model_dump(mode="json")).encode("utf-8")) > MAX_DIAGNOSIS_BYTES:
            raise ValueError("diagnosis evidence exceeds the byte limit")
        return self


class RepairPreviewArtifact(StrictModel):
    repair_cycle_id: str
    diagnosis_artifact_id: str
    patch_id: str
    operations: tuple[dict[str, Any], ...]
    requires_exact_approval: Literal[True] = True

    @model_validator(mode="after")
    def bounded(self) -> "RepairPreviewArtifact":
        if len(canonical_json(self.model_dump(mode="json")).encode("utf-8")) > MAX_REPAIR_PREVIEW_BYTES:
            raise ValueError("repair preview exceeds the byte limit")
        return self


class RepairCycle(StrictModel):
    schema_version: Literal["astra.project-repair.cycle.v1"] = REPAIR_CYCLE_VERSION
    repair_cycle_id: str
    project_run_id: str
    cycle_number: int = Field(ge=1, le=1)
    plan_revision_id: str
    scope_revision_id: str
    manifest_hash: str = Field(min_length=64, max_length=64)
    work_unit_id: str
    failure_artifact_id: str
    diagnosis_artifact_id: str | None = None
    repair_preview_artifact_id: str | None = None
    status: RepairCycleStatus
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


__all__ = [
    "DiagnosisArtifact",
    "FailureEvidenceArtifact",
    "REPAIR_CYCLE_VERSION",
    "RepairCycle",
    "RepairCycleStatus",
    "RepairPreviewArtifact",
]
