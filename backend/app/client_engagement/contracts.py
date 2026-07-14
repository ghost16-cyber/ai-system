from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ENGAGEMENT_SCHEMA_VERSION = "astra.client-engagement.v1"
MODEL_EXTRACTION_SCHEMA_VERSION = "astra.client-engagement.model-extraction.v1"
MAX_MODEL_RESPONSE_CHARS = 80_000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EngagementState(StrEnum):
    DRAFT = "draft"
    COLLECTING_EVIDENCE = "collecting_evidence"
    EXTRACTING_REQUIREMENTS = "extracting_requirements"
    CLARIFICATION_REQUIRED = "clarification_required"
    SCOPE_PREPARING = "scope_preparing"
    SCOPE_READY = "scope_ready"
    AWAITING_SCOPE_APPROVAL = "awaiting_scope_approval"
    SCOPE_APPROVED = "scope_approved"
    PROJECT_LAUNCHING = "project_launching"
    PROJECT_LAUNCHED = "project_launched"
    SCOPE_CHANGE_REQUESTED = "scope_change_requested"
    SCOPE_CHANGE_REVIEW = "scope_change_review"
    CANCELLED = "cancelled"
    FAILED = "failed"


class EvidenceSourceType(StrEnum):
    ORIGINAL_REQUEST = "original_chat_request"
    CLARIFICATION = "clarification_answer"
    UPLOADED_DOCUMENT = "uploaded_document_metadata"
    AUTHORIZED_FOLDER = "authorized_project_folder"
    STRUCTURAL_SUMMARY = "stage6_structural_summary"
    PROJECT_METADATA = "project_metadata"
    USER_CONSTRAINT = "user_supplied_constraint"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    UNKNOWN = "unknown"


class RequirementClassification(StrEnum):
    OUTCOME = "requested_outcome"
    DELIVERABLE = "deliverable"
    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    TECHNICAL_CONSTRAINT = "technical_constraint"
    PLATFORM_CONSTRAINT = "platform_constraint"
    FILE_REFERENCE = "file_or_folder_reference"
    DEADLINE = "deadline"
    ACCEPTANCE_SIGNAL = "acceptance_signal"
    EXCLUSION = "exclusion"
    UNKNOWN = "unknown_or_ambiguous"


class RequirementSourceKind(StrEnum):
    EXPLICIT_EVIDENCE = "explicit_evidence"
    USER_ANSWER = "user_answer"
    ASSUMPTION = "documented_assumption"


class QuestionPriority(StrEnum):
    BLOCKING = "blocking"
    HIGH = "high"
    MEDIUM = "medium"


class ReviewMode(StrEnum):
    AUTOMATED = "automated"
    HUMAN = "human_review_required"


class RelativeSize(StrEnum):
    XS = "extra_small"
    S = "small"
    M = "medium"
    L = "large"
    XL = "extra_large"


class EstimateConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CreatorType(StrEnum):
    DETERMINISTIC_SYSTEM = "deterministic_system"
    MODEL_ASSISTED = "model_assisted"
    USER_CHANGE = "user_change"


class ScopeChangeClassification(StrEnum):
    CLARIFICATION = "clarification_only"
    NON_MATERIAL = "non_material_correction"
    ADDITION = "material_scope_addition"
    REMOVAL = "material_scope_removal"
    CONSTRAINT = "constraint_change"
    ACCEPTANCE = "acceptance_criteria_change"
    CANCELLATION = "cancellation"


class ClientIdentityMetadata(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    user_id: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=256)
    organization: str | None = Field(default=None, max_length=256)


class ClientEngagementRequest(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    engagement_id: str
    conversation_id: str = Field(min_length=1, max_length=128)
    original_request: str = Field(min_length=1, max_length=6000)
    client: ClientIdentityMetadata
    folder_access_id: str | None = None
    constraints: list[str] = Field(default_factory=list, max_length=30)
    created_at: datetime


class EngagementEvidenceReference(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    evidence_id: str
    engagement_id: str
    source_type: EvidenceSourceType
    source_identifier: str = Field(min_length=1, max_length=500)
    excerpt: str | None = Field(default=None, max_length=4000)
    structured_summary: dict[str, Any] = Field(default_factory=dict)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    collected_at: datetime
    authorization_context: str = Field(min_length=1, max_length=500)
    stale_after: datetime | None = None
    sensitivity: Sensitivity = Sensitivity.UNKNOWN
    is_stale: bool = False


class RequirementSource(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    kind: RequirementSourceKind
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    answer_id: str | None = None
    assumption_id: str | None = None


class ExtractedRequirement(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    requirement_id: str
    text: str = Field(min_length=1, max_length=1200)
    classification: RequirementClassification
    source: RequirementSource
    explicit: bool = True
    material: bool = True
    confidence: float = Field(ge=0, le=1)


class ClarificationQuestion(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    question_id: str
    engagement_id: str
    semantic_key: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=800)
    rationale: str = Field(min_length=1, max_length=800)
    priority: QuestionPriority
    blocking: bool
    round_number: int = Field(ge=1)
    status: Literal["pending", "answered", "assumption_accepted", "withdrawn"] = "pending"
    created_at: datetime


class ClarificationAnswer(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    answer_id: str
    question_id: str
    engagement_id: str
    answer: str = Field(min_length=1, max_length=3000)
    use_reasonable_assumption: bool = False
    answered_by: str = Field(min_length=1, max_length=256)
    created_at: datetime


class Assumption(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    assumption_id: str
    text: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    accepted_by_user: bool
    materially_reduces_confidence: bool = False
    created_at: datetime


class Constraint(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    constraint_id: str
    text: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class Dependency(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    dependency_id: str
    text: str = Field(min_length=1, max_length=1200)
    owner: Literal["client", "astra", "third_party", "unknown"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class Exclusion(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    exclusion_id: str
    text: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class AcceptanceCriterion(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    criterion_id: str
    deliverable_id: str
    statement: str = Field(min_length=1, max_length=1200)
    review_mode: ReviewMode
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class Deliverable(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    deliverable_id: str
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def criteria_belong_to_deliverable(self) -> "Deliverable":
        if any(item.deliverable_id != self.deliverable_id for item in self.acceptance_criteria):
            raise ValueError("acceptance criteria must reference their deliverable")
        return self


class Milestone(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    milestone_id: str
    title: str = Field(min_length=1, max_length=300)
    deliverable_ids: list[str] = Field(min_length=1, max_length=20)
    completion_signal: str = Field(min_length=1, max_length=1000)


class Risk(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    risk_id: str
    description: str = Field(min_length=1, max_length=1200)
    likelihood: Literal["low", "medium", "high"]
    impact: Literal["low", "medium", "high"]
    mitigation: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class EffortRange(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)
    unit: Literal["work_units"] = "work_units"

    @model_validator(mode="after")
    def ordered(self) -> "EffortRange":
        if self.maximum < self.minimum:
            raise ValueError("effort maximum must not be less than minimum")
        return self


class EffortEstimate(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    relative_size: RelativeSize
    estimated_work_unit_count: int = Field(ge=1)
    optimistic: EffortRange
    expected: EffortRange
    pessimistic: EffortRange
    confidence: EstimateConfidence
    uncertainty_drivers: list[str] = Field(default_factory=list, max_length=20)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    guaranteed: Literal[False] = False


class ScopeProposal(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    engagement_title: str = Field(min_length=1, max_length=300)
    problem_statement: str = Field(min_length=1, max_length=1500)
    desired_outcome: str = Field(min_length=1, max_length=1500)
    deliverables: list[Deliverable] = Field(min_length=1, max_length=20)
    functional_requirements: list[ExtractedRequirement] = Field(default_factory=list, max_length=100)
    non_functional_requirements: list[ExtractedRequirement] = Field(default_factory=list, max_length=100)
    constraints: list[Constraint] = Field(default_factory=list, max_length=30)
    dependencies: list[Dependency] = Field(default_factory=list, max_length=30)
    client_responsibilities: list[str] = Field(default_factory=list, max_length=30)
    astra_responsibilities: list[str] = Field(default_factory=list, max_length=30)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=30)
    exclusions: list[Exclusion] = Field(default_factory=list, max_length=30)
    milestones: list[Milestone] = Field(min_length=1, max_length=20)
    risks: list[Risk] = Field(default_factory=list, max_length=30)
    open_questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=20)
    effort_estimate: EffortEstimate
    recommended_delivery_configuration: dict[str, Any]
    evidence_traceability: dict[str, list[str]]


class ScopeRevision(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    revision_id: str
    engagement_id: str
    revision_number: int = Field(ge=1)
    scope: ScopeProposal
    canonical_scope: str = Field(min_length=2)
    scope_hash: str = Field(min_length=64, max_length=64)
    source_evidence_hashes: dict[str, str]
    parent_revision_id: str | None = None
    reason: str = Field(min_length=1, max_length=1000)
    creator_type: CreatorType
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime


class ScopeApproval(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    approval_id: str
    engagement_id: str
    revision_id: str
    revision_number: int
    scope_hash: str = Field(min_length=64, max_length=64)
    approving_user: str = Field(min_length=1, max_length=256)
    engagement_state_version: int = Field(ge=1)
    approved_at: datetime


class ScopeRejection(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    rejection_id: str
    engagement_id: str
    revision_id: str
    reason: str = Field(min_length=1, max_length=2000)
    rejecting_user: str = Field(min_length=1, max_length=256)
    created_at: datetime


class ScopeChangeRequest(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    change_id: str
    engagement_id: str
    requested_change: str = Field(min_length=1, max_length=4000)
    classification: ScopeChangeClassification
    affected_deliverable_ids: list[str] = Field(default_factory=list, max_length=20)
    affected_milestone_ids: list[str] = Field(default_factory=list, max_length=20)
    estimate_impact: str = Field(max_length=1200)
    risk_impact: str = Field(max_length=1200)
    acceptance_criteria_impact: str = Field(max_length=1200)
    resulting_revision_id: str | None = None
    requested_by: str = Field(min_length=1, max_length=256)
    created_at: datetime


class ProjectLaunchResult(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    launch_id: str
    engagement_id: str
    scope_revision_id: str
    scope_hash: str
    delivery_job_id: str
    stage9_task_specification_hash: str
    launched_at: datetime
    idempotent_replay: bool = False


class EngagementHandoffMetadata(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    engagement_id: str
    scope_revision_id: str
    delivery_job_id: str
    acceptance_criterion_map: dict[str, str]
    evidence_ids: list[str]


class ModelRequirement(StrictModel):
    text: str = Field(min_length=1, max_length=1200)
    classification: RequirementClassification
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    explicit: bool


class ModelExtractionResponse(StrictModel):
    schema_version: Literal["astra.client-engagement.model-extraction.v1"]
    requirements: list[ModelRequirement] = Field(max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=30)


def parse_strict_model_extraction(raw: str) -> ModelExtractionResponse:
    if not isinstance(raw, str) or not raw or raw != raw.strip() or len(raw) > MAX_MODEL_RESPONSE_CHARS:
        raise ValueError("The model extraction response violated its bounded JSON contract.")
    if not raw.startswith("{") or not raw.endswith("}") or "```" in raw:
        raise ValueError("The model extraction response must be exactly one JSON object.")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicates)
        return ModelExtractionResponse.model_validate(payload)
    except Exception as error:
        raise ValueError("The model extraction response violated its strict schema.") from error


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate key: {key}")
        value[key] = item
    return value


class EngagementPublicResponse(StrictModel):
    schema_version: Literal["astra.client-engagement.v1"] = ENGAGEMENT_SCHEMA_VERSION
    engagement_id: str
    conversation_id: str
    state: EngagementState
    state_version: int
    understood_outcome: str
    authorized_evidence: list[dict[str, Any]]
    missing_information: list[str]
    pending_questions: list[ClarificationQuestion]
    current_scope_revision: ScopeRevision | None = None
    approved_scope_revision_id: str | None = None
    project_launch: ProjectLaunchResult | None = None
    scope_changes: list[ScopeChangeRequest] = Field(default_factory=list)
    limitation: str | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [name for name in globals() if not name.startswith("_")]
