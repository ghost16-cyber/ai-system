from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VALIDATION_SCHEMA_VERSION = "astra.project-validation.v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ValidationState(StrEnum):
    CREATED = "created"
    PREPARING_WORKSPACE = "preparing_workspace"
    BASELINE_CAPTURED = "baseline_captured"
    READY = "ready"
    RUNNING = "running"
    EXECUTION_PAUSED = "execution_paused"
    BUDGET_EXCEEDED = "budget_exceeded"
    EVALUATING_ACCEPTANCE = "evaluating_acceptance"
    INSPECTING_DELIVERABLES = "inspecting_deliverables"
    RUNNING_REGRESSION = "running_regression"
    QUALITY_REVIEW = "quality_review"
    REMEDIATION_REQUIRED = "remediation_required"
    AWAITING_HUMAN_REVIEW = "awaiting_human_review"
    DELIVERY_READY = "delivery_ready"
    DELIVERY_REJECTED = "delivery_rejected"
    RECOVERING = "recovering"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AcceptanceResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    PARTIALLY_PASSED = "partially_passed"
    NOT_EVALUATED = "not_evaluated"
    BLOCKED = "blocked"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"
    NOT_APPLICABLE = "not_applicable"


class EvaluationMethod(StrEnum):
    DETERMINISTIC = "deterministically_testable"
    APPROVED_COMMAND = "approved_command"
    STATIC_ANALYSIS = "static_analysis"
    ARTIFACT_METADATA = "artifact_metadata"
    VISUAL_REVIEW = "visual_review"
    HUMAN_FUNCTIONAL_REVIEW = "human_functional_review"
    EXTERNAL_ENVIRONMENT = "external_environment"
    NOT_EVALUABLE = "not_currently_evaluable"


class ArtifactType(StrEnum):
    SOURCE_CODE = "source_code_changes"
    WEBSITE_BUILD = "website_or_frontend_build"
    BACKEND_SERVICE = "backend_service"
    TESTS = "tests"
    DATASET_OUTPUT = "dataset_output"
    CHART = "chart_or_image"
    HTML_REPORT = "html_report"
    MARKDOWN_REPORT = "markdown_report"
    NOTEBOOK = "notebook"
    CONFIGURATION = "configuration"
    DOCUMENTATION = "documentation"
    REPAIR_PATCH = "repair_patch"
    VALIDATION_EVIDENCE = "validation_evidence"
    UNKNOWN = "unknown"


class ReadinessDecision(StrEnum):
    DELIVERY_READY = "delivery_ready"
    DELIVERY_READY_WITH_NOTES = "delivery_ready_with_notes"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    REMEDIATION_REQUIRED = "remediation_required"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class HumanReviewAction(StrEnum):
    APPROVE = "approve_as_delivery_ready"
    APPROVE_WITH_NOTES = "approve_with_notes"
    REQUEST_REMEDIATION = "request_remediation"
    REJECT = "reject_delivery"
    REQUEST_SCOPE_CHANGE = "request_scope_change"
    CANCEL = "cancel_engagement"


class FailureCategory(StrEnum):
    EXECUTION_DEFECT = "execution_defect"
    MISSING_DELIVERABLE = "missing_deliverable"
    REGRESSION = "regression"
    TEST_FAILURE = "test_failure"
    BUILD_FAILURE = "build_failure"
    SECURITY = "security_issue"
    UNMET_REQUIREMENT = "unmet_requirement"
    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    SCOPE_MISMATCH = "scope_mismatch"
    EXTERNAL_DEPENDENCY = "external_dependency"
    HUMAN_REVIEW = "human_review_issue"


class ApprovedScopeReference(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    engagement_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)
    revision_number: int = Field(ge=1)
    scope_hash: str = Field(min_length=64, max_length=64)


class Stage9ProjectReference(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    delivery_job_id: str = Field(min_length=1, max_length=128)
    plan_revision: int = Field(default=1, ge=1)
    plan_hash: str | None = Field(default=None, min_length=64, max_length=64)
    status: str = Field(min_length=1, max_length=100)


class ValidationLimits(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    max_duration_seconds: int = Field(default=1800, ge=1, le=86400)
    max_command_executions: int = Field(default=15, ge=0, le=100)
    max_command_runtime_seconds: int = Field(default=120, ge=1, le=3600)
    max_repair_attempts: int = Field(default=3, ge=0, le=20)
    max_plan_revisions: int = Field(default=4, ge=1, le=20)
    max_work_unit_retries: int = Field(default=3, ge=0, le=20)
    max_model_calls: int = Field(default=4, ge=0, le=50)
    max_model_input_chars: int = Field(default=120_000, ge=0, le=2_000_000)
    max_model_output_chars: int = Field(default=80_000, ge=0, le=1_000_000)
    max_evidence_items: int = Field(default=100, ge=1, le=1000)
    max_generated_files: int = Field(default=100, ge=0, le=5000)
    max_modified_files: int = Field(default=100, ge=0, le=5000)
    max_deleted_files: int = Field(default=20, ge=0, le=1000)
    max_total_changed_bytes: int = Field(default=20_000_000, ge=0, le=2_000_000_000)
    max_test_reruns: int = Field(default=5, ge=0, le=50)
    max_snapshot_files: int = Field(default=5000, ge=1, le=100_000)
    max_snapshot_bytes: int = Field(default=100_000_000, ge=1, le=10_000_000_000)
    max_runs_per_campaign: int = Field(default=5, ge=1, le=50)


class BudgetUsage(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    duration_seconds: int = Field(default=0, ge=0)
    command_executions: int = Field(default=0, ge=0)
    command_runtime_seconds: int = Field(default=0, ge=0)
    repair_attempts: int = Field(default=0, ge=0)
    plan_revisions: int = Field(default=0, ge=0)
    work_unit_retries: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    model_input_chars: int = Field(default=0, ge=0)
    model_output_chars: int = Field(default=0, ge=0)
    evidence_items: int = Field(default=0, ge=0)
    generated_files: int = Field(default=0, ge=0)
    modified_files: int = Field(default=0, ge=0)
    deleted_files: int = Field(default=0, ge=0)
    total_changed_bytes: int = Field(default=0, ge=0)
    test_reruns: int = Field(default=0, ge=0)
    snapshot_files: int = Field(default=0, ge=0)
    snapshot_bytes: int = Field(default=0, ge=0)


class WorkspaceReference(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    workspace_id: str
    authorization_id: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=1, max_length=128)
    root_fingerprint: str = Field(min_length=64, max_length=64)
    display_name: str = Field(min_length=1, max_length=300)
    source_root: str = Field(min_length=1, max_length=4000)
    validation_root: str = Field(min_length=1, max_length=4000)
    isolated: bool
    prepared_at: datetime


class SnapshotFile(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    relative_path: str = Field(min_length=1, max_length=4000)
    size_bytes: int = Field(ge=0)
    content_hash: str = Field(min_length=64, max_length=64)
    modified_ns: int = Field(ge=0)


class BaselineSnapshot(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    snapshot_id: str
    campaign_id: str
    workspace_id: str
    file_manifest: list[SnapshotFile]
    directory_hash: str = Field(min_length=64, max_length=64)
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    git_branch: str | None = Field(default=None, max_length=500)
    git_commit: str | None = Field(default=None, max_length=64)
    dirty_worktree: bool | None = None
    exclusions: list[str] = Field(default_factory=list, max_length=50)
    captured_at: datetime
    stale: bool = False
    restorable: bool = False
    backup_root: str | None = Field(default=None, max_length=4000)


class AcceptanceEvidence(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    evidence_id: str
    evidence_type: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=2000)
    source_reference: str = Field(min_length=1, max_length=500)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    deterministic: bool = True
    sensitive: bool = False


class AcceptanceEvaluation(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    evaluation_id: str
    criterion_id: str = Field(min_length=1, max_length=128)
    criterion_text: str = Field(min_length=1, max_length=2000)
    method: EvaluationMethod
    result: AcceptanceResult
    evidence: list[AcceptanceEvidence] = Field(default_factory=list, max_length=100)
    confidence: float = Field(ge=0, le=1)
    blocking: bool
    human_review_required: bool
    failure_explanation: str | None = Field(default=None, max_length=4000)
    suggested_remediation: str | None = Field(default=None, max_length=2000)
    evaluated_at: datetime


class DeliverableArtifact(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    artifact_id: str
    deliverable_id: str = Field(min_length=1, max_length=128)
    client_name: str = Field(min_length=1, max_length=300)
    artifact_type: ArtifactType
    logical_location: str = Field(min_length=1, max_length=1000)
    relative_path: str | None = Field(default=None, max_length=4000)
    exists: bool
    size_bytes: int = Field(default=0, ge=0)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    associated_criterion_ids: list[str] = Field(default_factory=list, max_length=50)
    inspection_methods: list[str] = Field(default_factory=list, max_length=30)
    human_review_required: bool = False
    warning: str | None = Field(default=None, max_length=2000)


class DeliverableManifest(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    manifest_id: str
    run_id: str
    artifacts: list[DeliverableArtifact]
    complete: bool
    missing_deliverable_ids: list[str] = Field(default_factory=list, max_length=100)
    generated_at: datetime
    manifest_hash: str = Field(min_length=64, max_length=64)


class RegressionResult(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    regression_id: str
    run_id: str
    changed_files: list[str] = Field(default_factory=list, max_length=1000)
    created_files: list[str] = Field(default_factory=list, max_length=1000)
    deleted_files: list[str] = Field(default_factory=list, max_length=1000)
    unexpected_changes: list[str] = Field(default_factory=list, max_length=1000)
    tests_regressed: list[str] = Field(default_factory=list, max_length=200)
    blocking: bool
    summary: str = Field(min_length=1, max_length=3000)
    evaluated_at: datetime


class QualityDimension(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=100)
    score: float = Field(ge=0, le=100)
    weight: float = Field(gt=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    blocking_findings: list[str] = Field(default_factory=list, max_length=100)
    explanation: str = Field(min_length=1, max_length=2000)


class QualityAssessment(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    assessment_id: str
    run_id: str
    dimensions: list[QualityDimension] = Field(min_length=1, max_length=30)
    overall_score: float = Field(ge=0, le=100)
    minimum_score: float = Field(default=75, ge=0, le=100)
    blocking_findings: list[str] = Field(default_factory=list, max_length=200)
    uncertainty: float = Field(ge=0, le=1)
    automated_decision: ReadinessDecision
    assessed_at: datetime
    assessment_hash: str = Field(min_length=64, max_length=64)


class ReliabilityFinding(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    finding_id: str
    category: FailureCategory
    severity: Literal["info", "warning", "error", "critical"]
    summary: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    blocking: bool
    recommended_route: Literal["stage8_repair", "stage9_replan", "stage10_scope_change", "manual_action", "external_verification", "cancel"]


class HumanReviewDecision(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    review_id: str
    campaign_id: str
    run_id: str
    scope_revision_id: str
    scope_hash: str = Field(min_length=64, max_length=64)
    validation_result_hash: str = Field(min_length=64, max_length=64)
    reviewer_id: str = Field(min_length=1, max_length=256)
    action: HumanReviewAction
    notes: str = Field(default="", max_length=4000)
    reviewed_at: datetime


class RemediationRequest(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    remediation_id: str
    campaign_id: str
    run_id: str
    findings: list[ReliabilityFinding] = Field(min_length=1, max_length=200)
    requested_route: Literal["stage8_repair", "stage9_replan", "stage10_scope_change", "manual_action", "external_verification", "cancel"]
    requested_by: str = Field(min_length=1, max_length=256)
    requested_at: datetime


class ValidationRun(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    run_id: str
    campaign_id: str
    run_number: int = Field(ge=1)
    state: ValidationState
    state_version: int = Field(ge=1)
    scope_reference: ApprovedScopeReference
    project_reference: Stage9ProjectReference
    baseline_snapshot_id: str
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    acceptance_evaluations: list[AcceptanceEvaluation] = Field(default_factory=list, max_length=500)
    deliverable_manifest: DeliverableManifest | None = None
    regression_result: RegressionResult | None = None
    quality_assessment: QualityAssessment | None = None
    findings: list[ReliabilityFinding] = Field(default_factory=list, max_length=500)
    automated_decision: ReadinessDecision | None = None
    human_review: HumanReviewDecision | None = None
    result_hash: str | None = Field(default=None, min_length=64, max_length=64)
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ValidationCriterion(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    criterion_id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=2000)
    required: bool = True
    review_mode: str = Field(default="automated", max_length=120)


class ValidationDeliverable(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    deliverable_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=2000)
    acceptance_criteria: list[ValidationCriterion] = Field(min_length=1, max_length=50)


class ValidationScopeSnapshot(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    revision_id: str = Field(min_length=1, max_length=128)
    revision_number: int = Field(ge=1)
    scope_hash: str = Field(min_length=64, max_length=64)
    objective: str = Field(min_length=1, max_length=3000)
    deliverables: list[ValidationDeliverable] = Field(min_length=1, max_length=100)
    exclusions: list[str] = Field(default_factory=list, max_length=100)
    canonical_scope: str = Field(min_length=2, max_length=500000)


class ValidationCampaign(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    campaign_id: str
    conversation_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=256)
    scope_reference: ApprovedScopeReference
    project_reference: Stage9ProjectReference
    scope_snapshot: ValidationScopeSnapshot
    workspace: WorkspaceReference | None = None
    baseline_snapshot: BaselineSnapshot | None = None
    limits: ValidationLimits = Field(default_factory=ValidationLimits)
    state: ValidationState = ValidationState.CREATED
    state_version: int = Field(default=1, ge=1)
    run_ids: list[str] = Field(default_factory=list, max_length=50)
    active_run_id: str | None = None
    created_at: datetime
    updated_at: datetime
    cancelled_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def active_run_is_known(self) -> "ValidationCampaign":
        if self.active_run_id and self.active_run_id not in self.run_ids:
            raise ValueError("active run must be listed in run_ids")
        return self


class ValidationAuditEvent(StrictModel):
    schema_version: Literal["astra.project-validation.v1"] = VALIDATION_SCHEMA_VERSION
    event_id: str
    campaign_id: str
    run_id: str | None = None
    event_type: str = Field(min_length=1, max_length=120)
    actor_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


def canonical_json(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    if isinstance(value, BaseModel):
        payload: Any = value.model_dump(mode="json", exclude_none=False)
    else:
        payload = value
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: BaseModel | dict[str, Any] | list[Any] | str | bytes) -> str:
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raw = canonical_json(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


__all__ = [name for name in globals() if not name.startswith("_")]
