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

PROJECT_ARTIFACT_VERSION = "astra.project-artifacts.artifact.v1"
MAX_ARTIFACT_PAYLOAD_BYTES = 262_144
MAX_ARTIFACT_REFERENCES_BYTES = 65_536


class ProjectArtifactType(StrEnum):
    SPECIFICATION = "specification"
    MANIFEST = "manifest"
    PLAN = "plan"
    PATCH_PREVIEW = "patch_preview"
    COMMAND_PREVIEW = "command_preview"
    VERIFIER_RESULT = "verifier_result"
    FAILURE_EVIDENCE = "failure_evidence"
    DIAGNOSIS = "diagnosis"
    REPAIR_PREVIEW = "repair_preview"
    EXECUTION_RESULT = "execution_result"
    HANDOFF = "handoff"


class ProjectArtifactBinding(StrictModel):
    project_run_id: str = Field(min_length=1, max_length=200)
    plan_revision_id: str | None = Field(default=None, max_length=200)
    scope_revision_id: str | None = Field(default=None, max_length=200)
    manifest_hash: str | None = Field(default=None, min_length=64, max_length=64)
    execution_attempt_id: str | None = Field(default=None, max_length=200)
    coordinator_intent_id: str | None = Field(default=None, max_length=200)
    authority_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ProjectArtifact(StrictModel):
    schema_version: Literal["astra.project-artifacts.artifact.v1"] = (
        PROJECT_ARTIFACT_VERSION
    )
    artifact_id: str = Field(min_length=1, max_length=200)
    artifact_type: ProjectArtifactType
    binding: ProjectArtifactBinding
    binding_hash: str = Field(min_length=64, max_length=64)
    revision_number: int | None = Field(default=None, ge=1)
    payload: dict[str, Any]
    evidence_references: tuple[dict[str, Any], ...] = ()
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime

    @model_validator(mode="after")
    def validate_integrity(self) -> "ProjectArtifact":
        expected_binding = content_hash(self.binding.model_dump(mode="json"))
        if self.binding_hash != expected_binding:
            raise ValueError("artifact binding hash does not match its exact binding")
        if (
            len(canonical_json(self.payload).encode("utf-8"))
            > MAX_ARTIFACT_PAYLOAD_BYTES
        ):
            raise ValueError("artifact payload exceeds the configured byte limit")
        if (
            len(canonical_json(self.evidence_references).encode("utf-8"))
            > MAX_ARTIFACT_REFERENCES_BYTES
        ):
            raise ValueError(
                "artifact evidence references exceed the configured byte limit"
            )
        expected_content = artifact_content_hash(
            self.artifact_type,
            self.binding,
            self.payload,
            self.evidence_references,
            revision_number=self.revision_number,
        )
        if self.content_hash != expected_content:
            raise ValueError(
                "artifact content hash does not match its immutable content"
            )
        return self


def artifact_content_hash(
    artifact_type: ProjectArtifactType | str,
    binding: ProjectArtifactBinding,
    payload: dict[str, Any],
    evidence_references: tuple[dict[str, Any], ...] = (),
    *,
    revision_number: int | None = None,
) -> str:
    material: dict[str, Any] = {
        "artifact_type": ProjectArtifactType(artifact_type).value,
        "binding": binding.model_dump(mode="json"),
        "payload": payload,
        "evidence_references": evidence_references,
    }
    # Keep v1 artifacts readable: Stage 3A hashes did not contain this key.
    if revision_number is not None:
        material["revision_number"] = revision_number
    return content_hash(material)


def build_project_artifact(
    *,
    artifact_type: ProjectArtifactType | str,
    binding: ProjectArtifactBinding,
    payload: dict[str, Any],
    evidence_references: tuple[dict[str, Any], ...] = (),
    revision_number: int | None = None,
    created_at: datetime | None = None,
) -> ProjectArtifact:
    kind = ProjectArtifactType(artifact_type)
    binding_digest = content_hash(binding.model_dump(mode="json"))
    artifact_digest = artifact_content_hash(
        kind,
        binding,
        payload,
        evidence_references,
        revision_number=revision_number,
    )
    artifact_id = f"artifact-{content_hash([binding.project_run_id, kind.value, binding_digest, artifact_digest])[:24]}"
    return ProjectArtifact(
        artifact_id=artifact_id,
        artifact_type=kind,
        binding=binding,
        binding_hash=binding_digest,
        revision_number=revision_number,
        payload=payload,
        evidence_references=evidence_references,
        content_hash=artifact_digest,
        created_at=created_at or datetime.now(timezone.utc),
    )
