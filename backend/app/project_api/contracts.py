from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.project_control.contracts import (
    ProjectReadModel,
    StrictModel,
    canonical_json,
)


CANONICAL_PROJECT_CREATE_VERSION = "astra.project-api.create.v1"
CANONICAL_PROJECT_RESPONSE_VERSION = "astra.project-api.project.v1"
CANONICAL_PROJECT_COLLECTION_VERSION = "astra.project-api.collection.v1"
CANONICAL_PROJECT_ACTION_VERSION = "astra.project-api.action.v1"
CANONICAL_PROJECT_ACTION_DESCRIPTOR_VERSION = "astra.project-api.action-descriptor.v1"
CANONICAL_COORDINATOR_SUMMARY_VERSION = "astra.project-api.coordinator-summary.v1"
CANONICAL_ARTIFACT_SUMMARY_VERSION = "astra.project-api.artifact-summary.v1"
CANONICAL_SYNTHESIS_PROPOSAL_SUMMARY_VERSION = "astra.project-api.synthesis-proposal-summary.v1"
CANONICAL_SYNTHESIS_PROPOSAL_COLLECTION_VERSION = "astra.project-api.synthesis-proposal-collection.v1"
MANUAL_EVIDENCE_REQUEST_VERSION = "astra.project-api.manual-evidence.v1"
MAX_CANONICAL_CREATE_BYTES = 524_288


class CompletedFolderAuthority(StrictModel):
    status: Literal["completed"] = "completed"
    action_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    repository_root_fingerprint: str = Field(min_length=1, max_length=256)


class CanonicalProjectCreateRequest(StrictModel):
    schema_version: Literal["astra.project-api.create.v1"] = CANONICAL_PROJECT_CREATE_VERSION
    conversation_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    repository_root: str = Field(min_length=1, max_length=4096)
    repository_root_fingerprint: str = Field(min_length=1, max_length=256)
    actor_id: str = Field(default="local-user", min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    folder_authority: CompletedFolderAuthority
    specification: dict[str, Any]
    manifest: dict[str, Any]
    plan: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_bounded_content(self) -> "CanonicalProjectCreateRequest":
        material = {
            "specification": self.specification,
            "manifest": self.manifest,
            "plan": self.plan,
        }
        if len(canonical_json(material).encode("utf-8")) > MAX_CANONICAL_CREATE_BYTES:
            raise ValueError("canonical project creation payload exceeds its byte limit")
        return self


class CanonicalProjectActionRequest(StrictModel):
    schema_version: Literal["astra.project-api.action.v1"] = CANONICAL_PROJECT_ACTION_VERSION
    conversation_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    repository_root_fingerprint: str = Field(min_length=1, max_length=256)
    expected_state_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    plan_revision_id: str | None = Field(default=None, min_length=1, max_length=200)
    scope_revision_id: str | None = Field(default=None, min_length=1, max_length=200)
    manifest_hash: str | None = Field(default=None, min_length=64, max_length=64)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=200)
    artifact_type: str | None = Field(default=None, min_length=1, max_length=80)
    artifact_hash: str | None = Field(default=None, min_length=64, max_length=64)
    artifact_binding_hash: str | None = Field(default=None, min_length=64, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class ManualEvidenceSubmissionRequest(StrictModel):
    schema_version: Literal["astra.project-api.manual-evidence.v1"] = MANUAL_EVIDENCE_REQUEST_VERSION
    conversation_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    repository_root_fingerprint: str = Field(min_length=1, max_length=256)
    expected_state_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    plan_revision_id: str = Field(min_length=1, max_length=200)
    scope_revision_id: str = Field(min_length=1, max_length=200)
    manifest_hash: str = Field(min_length=64, max_length=64)
    work_unit_id: str = Field(min_length=1, max_length=200)
    execution_attempt_id: str = Field(min_length=1, max_length=200)
    criterion_id: str = Field(min_length=1, max_length=200)
    criterion_hash: str = Field(min_length=64, max_length=64)
    verification_artifact_id: str = Field(min_length=1, max_length=200)
    verification_artifact_hash: str = Field(min_length=64, max_length=64)
    authority_binding: dict[str, Any]
    decision: Literal["passed", "failed"]
    evidence_kind: Literal[
        "user_confirmation", "observation_notes", "screenshot_reference",
        "external_url_reference", "uploaded_artifact_reference",
        "checklist_result", "structured_response",
    ]
    evidence: dict[str, Any]
    comments: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_evidence_size(self) -> "ManualEvidenceSubmissionRequest":
        if len(canonical_json(self.evidence).encode("utf-8")) > 65_536:
            raise ValueError("manual evidence exceeds its bounded payload limit")
        return self


class CanonicalProjectActionDescriptor(StrictModel):
    schema_version: Literal["astra.project-api.action-descriptor.v1"] = CANONICAL_PROJECT_ACTION_DESCRIPTOR_VERSION
    action: Literal[
        "approve_plan", "approve_patch", "approve_command", "approve_rollback",
        "cancel_project",
    ]
    label: str = Field(min_length=1, max_length=100)
    requires_approval: bool = True
    expected_state_version: int = Field(ge=1)
    plan_revision_id: str | None = None
    scope_revision_id: str | None = None
    manifest_hash: str | None = None
    artifact_id: str | None = None
    artifact_type: str | None = None
    artifact_hash: str | None = None
    artifact_binding_hash: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CanonicalCoordinatorSummary(StrictModel):
    schema_version: Literal["astra.project-api.coordinator-summary.v1"] = CANONICAL_COORDINATOR_SUMMARY_VERSION
    coordinator_intent_id: str
    intent_type: str
    status: str
    attempt_count: int = Field(ge=0)
    failure_classification: str | None = None
    updated_at: str


class CanonicalArtifactSummary(StrictModel):
    schema_version: Literal["astra.project-api.artifact-summary.v1"] = CANONICAL_ARTIFACT_SUMMARY_VERSION
    artifact_id: str
    artifact_type: str
    revision_number: int | None = None
    binding_hash: str = Field(min_length=64, max_length=64)
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: str
    retrieval_evidence: tuple["CanonicalRetrievalCitation", ...] = ()
    retrieval_mode: str | None = None
    reranker_identity: str | None = None
    reranker_fallback: bool | None = None
    invalidated: bool | None = None
    advisory_only: bool | None = None


class CanonicalRetrievalCitation(StrictModel):
    evidence_id: str
    citation_label: str
    relative_path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=8000)
    trust_class: Literal["untrusted_retrieved_content"]


class CanonicalSynthesisProposalSummary(StrictModel):
    schema_version: Literal["astra.project-api.synthesis-proposal-summary.v1"] = CANONICAL_SYNTHESIS_PROPOSAL_SUMMARY_VERSION
    proposal_id: str
    proposal_type: str
    proposal_fingerprint: str = Field(min_length=64, max_length=64)
    evidence_hash: str = Field(min_length=64, max_length=64)
    exact_model_tag: str
    semantic_validation_status: str
    lifecycle_state: str
    summary: str
    affected_paths: tuple[str, ...] = ()
    created_at: str
    advisory_only: Literal[True] = True


class CanonicalSynthesisProposalCollection(StrictModel):
    schema_version: Literal["astra.project-api.synthesis-proposal-collection.v1"] = CANONICAL_SYNTHESIS_PROPOSAL_COLLECTION_VERSION
    project_run_id: str
    items: tuple[CanonicalSynthesisProposalSummary, ...] = ()
    count: int = Field(ge=0)


class CanonicalProjectResponse(StrictModel):
    schema_version: Literal["astra.project-api.project.v1"] = CANONICAL_PROJECT_RESPONSE_VERSION
    project: ProjectReadModel
    artifacts: tuple[CanonicalArtifactSummary, ...] = ()
    coordinator: CanonicalCoordinatorSummary | None = None
    next_permitted_actions: tuple[CanonicalProjectActionDescriptor, ...] = ()


class CanonicalProjectCollection(StrictModel):
    schema_version: Literal["astra.project-api.collection.v1"] = CANONICAL_PROJECT_COLLECTION_VERSION
    items: tuple[CanonicalProjectResponse, ...] = ()
    count: int = Field(ge=0)
