from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


SPECIFICATION_VERSION = "astra.project-delivery.specification.v1"
PLAN_VERSION = "astra.project-delivery.plan.v1"
PLAN_REVISION_VERSION = "astra.project-delivery.plan-revision.v2"
WORK_UNIT_STATE_VERSION = "astra.project-delivery.work-unit-state.v1"
APPROVAL_GRANT_VERSION = "astra.project-delivery.plan-approval.v2"
VERIFIER_RESULT_VERSION = "astra.project-delivery.verifier-result.v1"
MODEL_SPECIFICATION_VERSION = "astra.project-delivery.model-specification.v1"
MODEL_PLAN_VERSION = "astra.project-delivery.model-plan.v1"
MAX_MODEL_RESPONSE_CHARS = 120_000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerificationMode(StrEnum):
    EXISTING_TEST = "existing_automated_test"
    NEW_TEST = "new_automated_test"
    TYPE_CHECK = "type_checking"
    LINT = "linting"
    BUILD = "production_build"
    APPROVED_COMMAND = "approved_command"
    STRUCTURAL = "structural_code_inspection"
    FILE_PRESENCE = "file_presence"
    CONFIGURATION = "configuration"
    EXACT_ASSERTION = "exact_diff_or_content_assertion"
    MANUAL = "manual_user_verification_required"


class VerificationState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SATISFIED = "satisfied"
    FAILED = "failed"
    BLOCKED = "blocked"
    MANUAL = "manual-user-check"
    WAIVED = "waived-by-user"


class DeliveryStatus(StrEnum):
    UNDERSTANDING = "understanding_request"
    INSPECTING = "inspecting_project"
    CLARIFICATION = "clarification_required"
    SPECIFICATION_READY = "task_specification_ready"
    SPECIFICATION_BLOCKED = "task_specification_blocked"
    PLAN_READY = "execution_plan_ready"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    PLAN_APPROVED = "plan_approved"
    PREPARING = "preparing_work_unit"
    PATCH_READY = "patch_preview_ready"
    PATCH_APPLIED = "patch_applied_not_verified"
    AWAITING_COMMAND = "awaiting_command_approval"
    VERIFYING = "verification_running"
    VERIFICATION_FAILED = "verification_failed"
    DIAGNOSING = "stage8_diagnosis"
    REPLANNING = "replanning_required"
    LIMIT_REACHED = "limit_reached"
    ROLLBACK_READY = "rollback_preview"
    PARTIAL = "partially_completed"
    AWAITING_MANUAL = "awaiting_manual_verification"
    COMPLETED = "delivery_completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class WorkUnitStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    PREPARING = "preparing"
    AWAITING_PATCH = "awaiting_patch_approval"
    APPLIED = "applied_not_verified"
    VERIFYING = "verifying"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    ROLLED_BACK = "rolled_back"


class WorkUnitExecutionStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    ACTIVE = "active"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


class VerifierOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    MANUAL_REQUIRED = "manual_required"


class VerificationRule(StrictModel):
    rule_type: Literal["file_exists", "json_value", "yaml_value", "python_symbol", "text_contains", "patch_scope"]
    relative_path: str | None = Field(default=None, max_length=500)
    selector: str | None = Field(default=None, max_length=500)
    expected_value: str | int | float | bool | None = None


class AcceptanceCriterion(StrictModel):
    criterion_id: str = Field(min_length=1, max_length=80)
    requirement: str = Field(min_length=1, max_length=1000)
    required: bool = True
    verification_mode: VerificationMode
    expected_evidence_type: str = Field(min_length=1, max_length=120)
    verification_state: VerificationState = VerificationState.PENDING
    blocked_reason: str | None = Field(default=None, max_length=1000)
    checker_rule: VerificationRule | None = None


class TaskSpecification(StrictModel):
    contract_version: Literal["astra.project-delivery.specification.v1"]
    task_id: str
    delivery_job_id: str
    conversation_id: str
    workspace_id: str
    original_user_request: str = Field(min_length=1, max_length=3000)
    normalized_objective: str = Field(min_length=1, max_length=1000)
    in_scope_requirements: list[str] = Field(min_length=1, max_length=20)
    explicit_exclusions: list[str] = Field(max_length=20)
    assumptions: list[str] = Field(max_length=20)
    constraints: list[str] = Field(max_length=20)
    requested_deliverables: list[str] = Field(min_length=1, max_length=20)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1, max_length=20)
    required_verification_methods: list[VerificationMode] = Field(max_length=20)
    optional_verification_methods: list[VerificationMode] = Field(max_length=20)
    known_risks: list[str] = Field(max_length=20)
    clarification_questions: list[str] = Field(max_length=3)
    specification_source: Literal["deterministic", "model-assisted"]
    provider_status: str = Field(max_length=80)
    evidence_references: list[str] = Field(max_length=20)
    revision: int = Field(ge=1, le=4)
    created_at: datetime
    specification_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def unique_criteria(self) -> "TaskSpecification":
        values = [item.criterion_id for item in self.acceptance_criteria]
        if len(values) != len(set(values)):
            raise ValueError("acceptance criterion IDs must be unique")
        return self


class WorkUnit(StrictModel):
    work_unit_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1000)
    requirement_references: list[str] = Field(min_length=1, max_length=20)
    criterion_references: list[str] = Field(min_length=1, max_length=20)
    dependencies: list[str] = Field(max_length=20)
    expected_files: list[str] = Field(max_length=30)
    expected_symbols: list[str] = Field(max_length=30)
    expected_patch_scope: str = Field(min_length=1, max_length=1000)
    expected_validation_commands: list[str] = Field(max_length=15)
    completion_conditions: list[str] = Field(min_length=1, max_length=20)
    risk_level: Literal["low", "medium", "high"]
    clarification_may_be_required: bool = False
    status: WorkUnitStatus = WorkUnitStatus.PENDING
    scaffold_hint: dict[str, Any] | None = None
    work_unit_hash: str = Field(min_length=64, max_length=64)


class ExecutionPlan(StrictModel):
    contract_version: Literal["astra.project-delivery.plan.v1"]
    delivery_job_id: str
    plan_revision: int = Field(ge=1, le=4)
    specification_hash: str = Field(min_length=64, max_length=64)
    stage6_analysis_hash: str = Field(min_length=64, max_length=64)
    work_units: list[WorkUnit] = Field(min_length=1, max_length=20)
    ordered_work_unit_ids: list[str] = Field(min_length=1, max_length=20)
    dependency_graph: dict[str, list[str]]
    acceptance_criterion_mapping: dict[str, list[str]]
    estimated_patch_count: int = Field(ge=1, le=10)
    estimated_command_count: int = Field(ge=0, le=15)
    explicit_exclusions: list[str] = Field(max_length=20)
    plan_source: Literal["deterministic", "model-assisted"]
    confidence: float = Field(ge=0, le=1)
    confidence_reasons: list[str] = Field(max_length=20)
    created_at: datetime
    plan_hash: str = Field(min_length=64, max_length=64)


class ImmutableWorkUnitDefinition(StrictModel):
    work_unit_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1000)
    requirement_references: tuple[str, ...] = Field(min_length=1, max_length=20)
    acceptance_criteria_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    dependencies: tuple[str, ...] = Field(max_length=20)
    expected_files: tuple[str, ...] = Field(max_length=30)
    expected_symbols: tuple[str, ...] = Field(max_length=30)
    bounded_inputs: tuple[str, ...] = Field(max_length=30)
    bounded_outputs: tuple[str, ...] = Field(max_length=30)
    expected_patch_scope: str = Field(min_length=1, max_length=1000)
    expected_validation_commands: tuple[str, ...] = Field(max_length=15)
    completion_conditions: tuple[str, ...] = Field(min_length=1, max_length=20)
    risk_level: Literal["low", "medium", "high"]
    clarification_may_be_required: bool = False


class ExecutionPlanRevision(StrictModel):
    schema_version: Literal["astra.project-delivery.plan-revision.v2"] = PLAN_REVISION_VERSION
    plan_revision_id: str
    project_run_id: str
    revision_number: int = Field(ge=1, le=4)
    task_specification_revision_id: str
    specification_hash: str = Field(min_length=64, max_length=64)
    stage6_analysis_hash: str = Field(min_length=64, max_length=64)
    work_units: tuple[ImmutableWorkUnitDefinition, ...] = Field(min_length=1, max_length=20)
    ordered_work_unit_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    dependencies: tuple[tuple[str, tuple[str, ...]], ...]
    acceptance_criteria: tuple[tuple[str, tuple[str, ...]], ...]
    estimated_patch_count: int = Field(ge=1, le=10)
    estimated_command_count: int = Field(ge=0, le=15)
    explicit_exclusions: tuple[str, ...] = Field(max_length=20)
    plan_source: Literal["deterministic", "model-assisted"]
    confidence: float = Field(ge=0, le=1)
    confidence_reasons: tuple[str, ...] = Field(max_length=20)
    created_at: datetime
    content_hash: str = Field(min_length=64, max_length=64)
    supersedes_revision_id: str | None = None


class WorkUnitExecutionState(StrictModel):
    schema_version: Literal["astra.project-delivery.work-unit-state.v1"] = WORK_UNIT_STATE_VERSION
    project_run_id: str
    plan_revision_id: str
    work_unit_id: str
    status: WorkUnitExecutionStatus
    attempt_count: int = Field(ge=0)
    active_attempt_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    state_version: int = Field(ge=1)


class PlanApprovalGrant(StrictModel):
    schema_version: Literal["astra.project-delivery.plan-approval.v2"] = APPROVAL_GRANT_VERSION
    approval_id: str
    project_run_id: str
    plan_revision_id: str
    plan_content_hash: str = Field(min_length=64, max_length=64)
    specification_hash: str = Field(min_length=64, max_length=64)
    scope_revision: int = Field(ge=1)
    workspace_id: str
    root_fingerprint: str = Field(min_length=64, max_length=64)
    approved_by: str = Field(min_length=1, max_length=80)
    approved_at: datetime
    expires_at: datetime | None = None
    expected_project_state_version: int = Field(ge=1)
    authority: Literal["prepare_work_units_only"] = "prepare_work_units_only"


class PerformedCheck(StrictModel):
    checker: str
    checker_version: str
    inputs: tuple[str, ...] = Field(max_length=30)
    condition: str = Field(max_length=1000)
    observed_value: str = Field(max_length=2000)
    expected_value_or_rule: str = Field(max_length=2000)
    outcome: Literal["passed", "failed", "inconclusive"]


class VerificationEvidence(StrictModel):
    evidence_type: str = Field(max_length=80)
    source_identifier: str = Field(max_length=500)
    relevant_file_or_artifact: str | None = Field(default=None, max_length=500)
    content_hash: str = Field(min_length=64, max_length=64)
    observation: str = Field(max_length=2000)
    provenance: tuple[str, ...] = Field(max_length=20)
    observed_at: datetime


class VerifierResult(StrictModel):
    schema_version: Literal["astra.project-delivery.verifier-result.v1"] = VERIFIER_RESULT_VERSION
    verifier_result_id: str
    project_run_id: str
    plan_revision_id: str
    work_unit_id: str | None = None
    criterion_id: str
    criterion_definition_hash: str = Field(min_length=64, max_length=64)
    verifier_type: str
    verifier_version: str
    attempt_id: str
    input_manifest_hash: str = Field(min_length=64, max_length=64)
    checked_at: datetime
    performed_checks: tuple[PerformedCheck, ...]
    evidence_items: tuple[VerificationEvidence, ...]
    outcome: VerifierOutcome
    summary: str = Field(max_length=2000)
    failure_reason: str | None = Field(default=None, max_length=2000)
    result_hash: str = Field(min_length=64, max_length=64)


class VerificationRecord(StrictModel):
    verification_id: str
    delivery_job_id: str
    work_unit_id: str
    criterion_id: str
    state: VerificationState
    method: VerificationMode
    evidence_references: list[str] = Field(max_length=30)
    command_run_references: list[str] = Field(max_length=15)
    relevant_file_hashes: dict[str, str]
    relevant_diff_hashes: list[str] = Field(max_length=15)
    structural_analysis_references: list[str] = Field(max_length=10)
    failure_explanation: str | None = Field(default=None, max_length=4000)
    verifier_result_id: str | None = None
    verifier_result_hash: str | None = Field(default=None, min_length=64, max_length=64)
    input_manifest_hash: str | None = Field(default=None, min_length=64, max_length=64)
    plan_revision_id: str | None = None
    verified_at: datetime
    verification_hash: str = Field(min_length=64, max_length=64)


class ScopeChange(StrictModel):
    scope_change_id: str
    delivery_job_id: str
    work_unit_id: str | None = None
    reason_code: str = Field(max_length=80)
    explanation: str = Field(max_length=2000)
    affected_paths: list[str] = Field(max_length=30)
    material: bool
    created_at: datetime
    scope_change_hash: str = Field(min_length=64, max_length=64)


class HandoffReport(StrictModel):
    handoff_id: str
    delivery_job_id: str
    original_objective: str
    final_specification_hash: str
    implemented_requirements: list[str]
    excluded_or_deferred_items: list[str]
    changed_files: list[str] = Field(max_length=50)
    created_files: list[str] = Field(max_length=50)
    removed_files: list[str] = Field(max_length=50)
    database_or_configuration_changes: list[str]
    tests_added_or_changed: list[str]
    validation_commands_and_outcomes: list[str]
    acceptance_criterion_results: dict[str, str]
    repair_cycles_used: int = Field(ge=0)
    rollback_available: bool
    setup_or_run_instructions: list[str]
    known_limitations: list[str]
    manual_checks_still_required: list[str]
    relevant_hashes: dict[str, str]
    verifier_result_ids: list[str] = Field(default_factory=list, max_length=30)
    completion_status: Literal["completed", "partially_completed", "blocked", "awaiting_manual_verification"]
    completed_at: datetime
    handoff_hash: str = Field(min_length=64, max_length=64)


class ModelSpecificationResponse(StrictModel):
    contract_version: Literal["astra.project-delivery.model-specification.v1"]
    normalized_objective: str = Field(min_length=1, max_length=1000)
    in_scope_requirements: list[str] = Field(min_length=1, max_length=20)
    explicit_exclusions: list[str] = Field(max_length=20)
    assumptions: list[str] = Field(max_length=20)
    requested_deliverables: list[str] = Field(min_length=1, max_length=20)
    criteria: list[AcceptanceCriterion] = Field(min_length=1, max_length=20)
    evidence_references: list[str] = Field(max_length=20)
    confidence_claim: Literal["high", "medium", "low"]
    clarification_question: str | None = Field(default=None, max_length=500)


class ModelPlanResponse(StrictModel):
    contract_version: Literal["astra.project-delivery.model-plan.v1"]
    work_units: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    confidence_claim: Literal["high", "medium", "low"]
    evidence_references: list[str] = Field(max_length=20)


def parse_model_specification(raw: str) -> ModelSpecificationResponse:
    return _parse_strict(raw, ModelSpecificationResponse, "task specification")


def parse_model_plan(raw: str) -> ModelPlanResponse:
    return _parse_strict(raw, ModelPlanResponse, "execution plan")


def _parse_strict(raw: str, contract: type[StrictModel], label: str):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"The model returned an empty {label} response.")
    if len(raw) > MAX_MODEL_RESPONSE_CHARS:
        raise ValueError(f"The model {label} response exceeded the bounded output limit.")
    if raw != raw.strip() or not raw.startswith("{") or not raw.endswith("}") or "```" in raw:
        raise ValueError(f"The model {label} response must be exactly one JSON object.")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        return contract.model_validate(payload)
    except (json.JSONDecodeError, UnicodeError, ValidationError) as error:
        raise ValueError(f"The model {label} response violated its strict contract.") from error


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


__all__ = [
    "AcceptanceCriterion", "DeliveryStatus", "ExecutionPlan", "ExecutionPlanRevision",
    "ImmutableWorkUnitDefinition", "PlanApprovalGrant", "PerformedCheck", "HandoffReport",
    "MODEL_PLAN_VERSION", "MODEL_SPECIFICATION_VERSION", "ModelPlanResponse",
    "ModelSpecificationResponse", "PLAN_VERSION", "SPECIFICATION_VERSION",
    "ScopeChange", "TaskSpecification", "VerificationEvidence", "VerificationMode", "VerificationRecord",
    "VerificationRule", "VerificationState", "VerifierOutcome", "VerifierResult", "WorkUnit",
    "WorkUnitExecutionState", "WorkUnitExecutionStatus", "WorkUnitStatus", "parse_model_plan",
    "parse_model_specification",
]
