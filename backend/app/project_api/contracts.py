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
CANONICAL_ARTIFACT_SUMMARY_VERSION = "astra.project-api.artifact-summary.v1"
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
    expected_state_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=200)
    artifact_hash: str | None = Field(default=None, min_length=64, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class CanonicalArtifactSummary(StrictModel):
    schema_version: Literal["astra.project-api.artifact-summary.v1"] = CANONICAL_ARTIFACT_SUMMARY_VERSION
    artifact_id: str
    artifact_type: str
    revision_number: int | None = None
    binding_hash: str = Field(min_length=64, max_length=64)
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: str


class CanonicalProjectResponse(StrictModel):
    schema_version: Literal["astra.project-api.project.v1"] = CANONICAL_PROJECT_RESPONSE_VERSION
    project: ProjectReadModel
    artifacts: tuple[CanonicalArtifactSummary, ...] = ()


class CanonicalProjectCollection(StrictModel):
    schema_version: Literal["astra.project-api.collection.v1"] = CANONICAL_PROJECT_COLLECTION_VERSION
    items: tuple[CanonicalProjectResponse, ...] = ()
    count: int = Field(ge=0)
