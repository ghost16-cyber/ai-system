from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.project_api.contracts import (
    CanonicalArtifactSummary,
    CanonicalProjectCollection,
    CanonicalProjectCreateRequest,
    CanonicalProjectResponse,
    CanonicalProjectActionRequest,
    CanonicalProjectActionDescriptor,
    CanonicalCoordinatorSummary,
    CanonicalSynthesisProposalCollection,
    CanonicalSynthesisProposalSummary,
    ManualEvidenceSubmissionRequest,
)
from backend.app.project_artifacts import ProjectArtifact
from backend.app.project_control import ProjectControlError, ProjectControlErrorCode
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_coordinator import ProjectCoordinatorService
from backend.app.project_analysis.model_synthesis.proposals import SynthesisProposalStore

if TYPE_CHECKING:
    from backend.app.project_retrieval.service import ProjectRetrievalService


FolderAuthorityResolver = Callable[[str], dict[str, Any]]


def create_project_router(
    service: CanonicalProjectService,
    *,
    folder_authority_resolver: FolderAuthorityResolver | None = None,
    coordinator: ProjectCoordinatorService | None = None,
    synthesis_proposals: SynthesisProposalStore | None = None,
    retrieval: "ProjectRetrievalService | None" = None,
) -> APIRouter:
    router = APIRouter(tags=["canonical-projects"])

    @router.post(
        "/chat/projects",
        response_model=CanonicalProjectResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_project(request: CanonicalProjectCreateRequest) -> CanonicalProjectResponse:
        authority = (
            folder_authority_resolver(request.conversation_id)
            if folder_authority_resolver is not None
            else request.folder_authority.model_dump(mode="json")
        )
        try:
            project = service.create_project(
                conversation_id=request.conversation_id,
                workspace_id=request.workspace_id,
                repository_root=request.repository_root,
                repository_root_fingerprint=request.repository_root_fingerprint,
                actor_id=request.actor_id,
                idempotency_key=request.idempotency_key,
                folder_authority=authority,
                specification=request.specification,
                manifest=request.manifest,
                plan=request.plan,
            )
            return build_canonical_project_response(service, project.project_run_id, coordinator=coordinator, retrieval=retrieval)
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    @router.get(
        "/chat/projects/{project_run_id}",
        response_model=CanonicalProjectResponse,
    )
    def get_project(project_run_id: str) -> CanonicalProjectResponse:
        try:
            return build_canonical_project_response(service, project_run_id, coordinator=coordinator, retrieval=retrieval)
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    @router.get(
        "/chat/projects/{project_run_id}/synthesis-proposals",
        response_model=CanonicalSynthesisProposalCollection,
    )
    def list_synthesis_proposals(
        project_run_id: str,
        limit: int = Query(default=100, ge=1, le=200),
    ) -> CanonicalSynthesisProposalCollection:
        try:
            service.get_project(project_run_id)
        except ProjectControlError as exc:
            raise _http_error(exc) from exc
        if synthesis_proposals is None:
            return CanonicalSynthesisProposalCollection(
                project_run_id=project_run_id, items=(), count=0
            )
        proposals = synthesis_proposals.list_for_project(project_run_id, limit=limit)
        items = tuple(_proposal_summary(item, synthesis_proposals) for item in proposals)
        return CanonicalSynthesisProposalCollection(
            project_run_id=project_run_id, items=items, count=len(items)
        )

    @router.get(
        "/chat/conversations/{conversation_id}/projects",
        response_model=CanonicalProjectCollection,
    )
    def list_projects(conversation_id: str) -> CanonicalProjectCollection:
        try:
            items = tuple(
                build_canonical_project_response(service, project.project_run_id, coordinator=coordinator, retrieval=retrieval)
                for project in service.list_projects(conversation_id)
            )
            return CanonicalProjectCollection(items=items, count=len(items))
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    @router.get(
        "/chat/projects/{project_run_id}/artifacts",
        response_model=tuple[CanonicalArtifactSummary, ...],
    )
    def list_artifacts(
        project_run_id: str,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> tuple[CanonicalArtifactSummary, ...]:
        try:
            return tuple(_summary(item) for item in service.list_artifacts(project_run_id, limit=limit))
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    @router.post(
        "/chat/projects/{project_run_id}/actions/{action}",
        response_model=CanonicalProjectResponse,
    )
    def perform_action(
        project_run_id: str,
        action: str,
        request: CanonicalProjectActionRequest,
    ) -> CanonicalProjectResponse:
        try:
            service.execute_action(project_run_id, action=action, request=request)
            return build_canonical_project_response(service, project_run_id, coordinator=coordinator, retrieval=retrieval)
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    @router.get("/chat/projects/{project_run_id}/verification/criteria")
    def verification_criteria(project_run_id: str) -> dict[str, Any]:
        try:
            project = service.get_project(project_run_id)
            return {
                "schema_version": "astra.project-api.verification-criteria.v1",
                "project_run_id": project_run_id,
                "state_version": project.state_version,
                "handoff_eligible": project.handoff_eligible,
                "criteria": project.criterion_states,
            }
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    @router.post(
        "/chat/projects/{project_run_id}/verification/manual-evidence",
        response_model=CanonicalProjectResponse,
    )
    def submit_manual_evidence(
        project_run_id: str,
        request: ManualEvidenceSubmissionRequest,
    ) -> CanonicalProjectResponse:
        try:
            service.submit_manual_evidence(project_run_id, request)
            return build_canonical_project_response(service, project_run_id, coordinator=coordinator, retrieval=retrieval)
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    @router.get("/chat/projects/{project_run_id}/verification/manual-evidence")
    def manual_evidence(project_run_id: str) -> dict[str, Any]:
        try:
            return {
                "schema_version": "astra.project-api.manual-evidence-collection.v1",
                "project_run_id": project_run_id,
                "items": service.manual_evidence(project_run_id),
            }
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    return router


def build_canonical_project_response(
    service: CanonicalProjectService,
    project_run_id: str,
    *,
    coordinator: ProjectCoordinatorService | None = None,
    retrieval: "ProjectRetrievalService | None" = None,
) -> CanonicalProjectResponse:
    project = service.get_project(project_run_id)
    raw_artifacts = tuple(service.list_artifacts(project_run_id))
    artifacts = tuple(_summary(item, retrieval=retrieval) for item in raw_artifacts)
    coordinator_summary = None
    if coordinator is not None:
        intents = coordinator.list_for_project(project_run_id)
        if intents:
            latest = intents[-1]
            coordinator_summary = CanonicalCoordinatorSummary(
                coordinator_intent_id=latest.coordinator_intent_id,
                intent_type=latest.intent_type.value,
                status=latest.status.value,
                attempt_count=latest.attempt_count,
                failure_classification=latest.failure_classification,
                updated_at=latest.updated_at.isoformat(),
            )
    return CanonicalProjectResponse(
        project=project,
        artifacts=artifacts,
        coordinator=coordinator_summary,
        next_permitted_actions=_next_actions(project, raw_artifacts),
    )


def _next_actions(project, artifacts) -> tuple[CanonicalProjectActionDescriptor, ...]:
    if project.terminal:
        return ()
    pending = str(project.next_permitted_action or "")
    base = pending.split(":", 1)[0]
    labels = {
        "approve_plan": "Approve exact plan",
        "approve_patch": "Approve exact patch",
        "approve_command": "Approve exact command",
        "approve_rollback": "Approve exact rollback",
    }
    artifact_types = {
        "approve_plan": ("plan",),
        "approve_patch": ("patch_preview", "repair_preview"),
        "approve_command": ("command_preview",),
        "approve_rollback": ("rollback_preview",),
    }
    values: list[CanonicalProjectActionDescriptor] = []
    if base in labels:
        artifact = next((
            item for item in reversed(artifacts)
            if item.artifact_type in artifact_types[base]
            and project.artifact_references.get(item.artifact_type) == item.artifact_id
        ), None)
        object_key = {
            "approve_patch": "patch_id",
            "approve_command": "command_id",
            "approve_rollback": "rollback_id",
        }.get(base)
        payload = {} if base == "approve_plan" else {
            object_key or "pending_id": pending.split(":", 1)[1] if ":" in pending else base
        }
        if artifact is not None:
            for field in ("work_unit_id", "criterion_id", "execution_attempt_id"):
                if artifact.payload.get(field) is not None:
                    payload[field] = artifact.payload[field]
        values.append(CanonicalProjectActionDescriptor(
            action=base,
            label=labels[base],
            expected_state_version=project.state_version,
            plan_revision_id=project.plan_revision_id,
            scope_revision_id=project.scope_revision_id,
            manifest_hash=project.manifest_hash,
            artifact_id=artifact.artifact_id if artifact else None,
            artifact_type=artifact.artifact_type if artifact else None,
            artifact_hash=artifact.content_hash if artifact else None,
            artifact_binding_hash=artifact.binding_hash if artifact else None,
            payload=payload,
        ))
    if project.next_permitted_action != "cancelling":
        values.append(CanonicalProjectActionDescriptor(
            action="cancel_project",
            label="Cancel project",
            expected_state_version=project.state_version,
            plan_revision_id=project.plan_revision_id,
            scope_revision_id=project.scope_revision_id,
            manifest_hash=project.manifest_hash,
            payload={"reason": "Cancelled by the user."},
        ))
    return tuple(values)


def _summary(
    artifact: ProjectArtifact,
    *,
    retrieval: "ProjectRetrievalService | None" = None,
) -> CanonicalArtifactSummary:
    values: dict[str, Any] = {}
    if artifact.artifact_type.value == "retrieval_evidence":
        payload = artifact.payload
        evidence = payload.get("evidence") if isinstance(payload, dict) else None
        if isinstance(evidence, list):
            values["retrieval_evidence"] = tuple({
                "evidence_id": str(item.get("evidence_id") or ""),
                "citation_label": str(item.get("citation_label") or ""),
                "relative_path": str(item.get("relative_path") or ""),
                "line_start": int(item.get("line_start") or 1),
                "line_end": int(item.get("line_end") or 1),
                "excerpt": str(item.get("text") or "")[:8000],
                "trust_class": "untrusted_retrieved_content",
            } for item in evidence if isinstance(item, dict) and item.get("text"))
        reranker = payload.get("reranker") if isinstance(payload, dict) else None
        if isinstance(reranker, dict):
            values["retrieval_mode"] = "hybrid_learned_rerank" if not reranker.get("fallback") else "hybrid_deterministic_fallback"
            values["reranker_identity"] = str(reranker.get("identity") or "")
            values["reranker_fallback"] = bool(reranker.get("fallback"))
        values["advisory_only"] = True
        if retrieval is not None:
            try:
                values["invalidated"] = retrieval.get_retrieval_artifact(
                    artifact.binding.project_run_id, artifact.artifact_id
                ).invalidated
            except Exception:
                values["invalidated"] = True
    return CanonicalArtifactSummary(
        artifact_id=artifact.artifact_id,
        artifact_type=artifact.artifact_type.value,
        revision_number=artifact.revision_number,
        binding_hash=artifact.binding_hash,
        content_hash=artifact.content_hash,
        created_at=artifact.created_at.isoformat(),
        **values,
    )


def _proposal_summary(proposal, store: SynthesisProposalStore) -> CanonicalSynthesisProposalSummary:
    content = proposal.content
    operations = content.get("validated_operations") or content.get("operations") or ()
    affected = tuple(dict.fromkeys(
        str(item.get("path") or item.get("relative_path") or "")
        for item in operations
        if isinstance(item, dict) and (item.get("path") or item.get("relative_path"))
    ))[:40]
    return CanonicalSynthesisProposalSummary(
        proposal_id=proposal.proposal_id,
        proposal_type=proposal.proposal_type.value,
        proposal_fingerprint=proposal.proposal_fingerprint,
        evidence_hash=proposal.evidence_hash,
        exact_model_tag=proposal.exact_model_tag,
        semantic_validation_status=proposal.semantic_validation_status.value,
        lifecycle_state=store.current_lifecycle(proposal.proposal_id).value,
        summary=" ".join(str(content.get("summary") or proposal.proposal_type.value).split())[:1000],
        affected_paths=affected,
        created_at=proposal.created_at.isoformat(),
    )


def _http_error(error: ProjectControlError) -> HTTPException:
    status_code = 409
    if error.code == ProjectControlErrorCode.PROJECT_NOT_FOUND:
        status_code = 404
    elif error.code == ProjectControlErrorCode.INVALID_COMMAND:
        status_code = 422
    return HTTPException(status_code=status_code, detail=error.as_dict())
