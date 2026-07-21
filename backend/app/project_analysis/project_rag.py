from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from backend.app.project_control.contracts import StrictModel


class ApprovedEvidenceReference(StrictModel):
    artifact_id: str
    content_hash: str = Field(min_length=64, max_length=64)
    provenance: str


class ProjectRagResult(StrictModel):
    schema_version: Literal["astra.project-rag.result.v1"] = "astra.project-rag.result.v1"
    enabled: bool
    references: tuple[ApprovedEvidenceReference, ...] = ()
    excerpts: tuple[dict[str, Any], ...] = ()
    blocked_reason: str | None = None


class ApprovedProjectEvidenceRetriever:
    """Optional evidence-only retrieval; disabled unless explicitly configured."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def retrieve(
        self,
        *,
        approved_references: tuple[ApprovedEvidenceReference, ...],
        query: str,
        max_bytes: int = 16_384,
    ) -> ProjectRagResult:
        del query, max_bytes
        if not self.enabled:
            return ProjectRagResult(enabled=False, blocked_reason="project_rag_disabled")
        if not approved_references:
            return ProjectRagResult(enabled=True, blocked_reason="approved_provenance_required")
        # Retrieval adapters may be added later; this boundary never falls back to generic indexes.
        return ProjectRagResult(enabled=True, references=approved_references, excerpts=())


__all__ = ["ApprovedEvidenceReference", "ApprovedProjectEvidenceRetriever", "ProjectRagResult"]
