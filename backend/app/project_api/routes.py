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
    CanonicalProjectEventSummary,
    CanonicalProjectEventsResponse,
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

    @router.get(
        "/chat/projects/{project_run_id}/events",
        response_model=CanonicalProjectEventsResponse,
    )
    def list_events(
        project_run_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> CanonicalProjectEventsResponse:
        try:
            service.get_project(project_run_id)
            events = [
                event for event in service.control.list_events(project_run_id)
                if event.sequence > after_sequence
            ]
        except ProjectControlError as exc:
            raise _http_error(exc) from exc
        page = events[:limit]
        items = tuple(
            CanonicalProjectEventSummary(
                sequence=event.sequence,
                event_type=event.event_type,
                label=_event_label(event.event_type),
                occurred_at=event.created_at.isoformat(),
            )
            for event in page
        )
        next_after_sequence = page[-1].sequence if len(events) > len(page) else None
        return CanonicalProjectEventsResponse(
            project_run_id=project_run_id, items=items, next_after_sequence=next_after_sequence,
        )

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
    next_actions = _next_actions(project, raw_artifacts)
    reviewable_patch_artifact_ids = {
        str(action.artifact_id)
        for action in next_actions
        if action.action == "approve_patch" and action.artifact_id
    }
    artifacts = tuple(
        _summary(
            item,
            retrieval=retrieval,
            include_patch_review=(
                item.artifact_id in reviewable_patch_artifact_ids
            ),
        )
        for item in raw_artifacts
    )
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
        next_permitted_actions=next_actions,
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
    include_patch_review: bool = False,
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
    elif (
        include_patch_review
        and artifact.artifact_type.value in {"patch_preview", "repair_preview"}
    ):
        values["patch_review"] = _patch_review(artifact)
    return CanonicalArtifactSummary(
        artifact_id=artifact.artifact_id,
        artifact_type=artifact.artifact_type.value,
        revision_number=artifact.revision_number,
        binding_hash=artifact.binding_hash,
        content_hash=artifact.content_hash,
        created_at=artifact.created_at.isoformat(),
        **values,
    )


def _patch_review(artifact: ProjectArtifact) -> dict[str, Any]:
    payload = artifact.payload
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, (list, tuple)) or not raw_operations:
        return {
            "summary": _optional_text(payload.get("summary"), 2000),
            "operation_count": 1,
            "operations": ({
                "operation": "unavailable",
                "path": "[review unavailable]",
            },),
            "requires_exact_approval": True,
            "review_complete": False,
            "advisory_only": bool(payload.get("proposal_id")),
        }
    operations: list[dict[str, Any]] = []
    review_complete = bool(payload.get("requires_exact_approval") is True)
    known_fields = {
        "operation",
        "path",
        "relative_path",
        "expected_sha256",
        "strategy",
        "rationale",
        "affected_symbols",
        "evidence_references",
        "content",
        "replacements",
    }
    for raw in raw_operations[:100]:
        if not isinstance(raw, dict):
            review_complete = False
            continue
        operation, operation_exact = _review_text(raw.get("operation"), 40)
        path, path_exact = _review_text(
            raw.get("path") or raw.get("relative_path"),
            4096,
        )
        review_complete = review_complete and operation_exact and path_exact
        if not operation or not path:
            review_complete = False
            continue
        replacements: list[dict[str, Any]] = []
        raw_replacements = raw.get("replacements", ())
        if not isinstance(raw_replacements, (list, tuple)):
            raw_replacements = ()
            review_complete = False
        for replacement in raw_replacements:
            if not isinstance(replacement, dict):
                review_complete = False
                continue
            start = replacement.get("start_line")
            end = replacement.get("end_line")
            expected = replacement.get("expected_text")
            proposed = replacement.get("replacement_text")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 1
                or end < start
                or not isinstance(expected, str)
                or not isinstance(proposed, str)
            ):
                review_complete = False
                continue
            replacements.append({
                "start_line": start,
                "end_line": end,
                "expected_text": expected,
                "replacement_text": proposed,
            })
        content = raw.get("content")
        if content is not None and not isinstance(content, str):
            content = None
            review_complete = False
        affected_symbols = raw.get("affected_symbols", ())
        evidence_references = raw.get("evidence_references", ())
        if not isinstance(affected_symbols, (list, tuple)):
            affected_symbols = ()
            review_complete = False
        if not isinstance(evidence_references, (list, tuple)):
            evidence_references = ()
            review_complete = False
        expected_sha256, expected_sha_exact = _review_text(
            raw.get("expected_sha256"),
            64,
            optional=True,
        )
        strategy, strategy_exact = _review_text(
            raw.get("strategy"),
            80,
            optional=True,
        )
        rationale, rationale_exact = _review_text(
            raw.get("rationale"),
            2000,
            optional=True,
        )
        review_complete = (
            review_complete
            and expected_sha_exact
            and strategy_exact
            and rationale_exact
        )
        normalized_symbols = tuple(
            item[:500]
            for item in affected_symbols
            if isinstance(item, str) and item
        )[:100]
        normalized_references = tuple(
            item[:4096]
            for item in evidence_references
            if isinstance(item, str) and item
        )[:100]
        if (
            len(normalized_symbols) != len(affected_symbols)
            or any(
                not isinstance(item, str) or len(item) > 500
                for item in affected_symbols
            )
            or len(normalized_references) != len(evidence_references)
            or any(
                not isinstance(item, str) or len(item) > 4096
                for item in evidence_references
            )
        ):
            review_complete = False
        if operation == "modify":
            if expected_sha256 is None or len(expected_sha256) != 64:
                review_complete = False
            if strategy == "complete_content" and content is None:
                review_complete = False
            elif strategy == "exact_replacements" and not replacements:
                review_complete = False
            elif strategy not in {"complete_content", "exact_replacements"}:
                review_complete = False
        elif operation == "create":
            if expected_sha256 != "missing" or content is None:
                review_complete = False
        elif operation == "delete":
            if expected_sha256 is None or len(expected_sha256) != 64:
                review_complete = False
        else:
            review_complete = False
        operations.append({
            "operation": operation,
            "path": path,
            "expected_sha256": expected_sha256,
            "strategy": strategy,
            "rationale": rationale,
            "affected_symbols": normalized_symbols,
            "evidence_references": normalized_references,
            "content": content,
            "replacements": tuple(replacements),
            "additional_details": {
                str(key): value
                for key, value in raw.items()
                if str(key) not in known_fields
            },
        })
    if len(raw_operations) != len(operations) or len(raw_operations) > 100:
        review_complete = False
    if not operations:
        operations.append({
            "operation": "unavailable",
            "path": "[review unavailable]",
        })
        review_complete = False
    return {
        "summary": _optional_text(payload.get("summary"), 2000),
        "operation_count": len(operations),
        "operations": tuple(operations),
        "requires_exact_approval": True,
        "review_complete": review_complete,
        "advisory_only": bool(payload.get("proposal_id")),
    }


def _optional_text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:maximum]


def _review_text(
    value: Any,
    maximum: int,
    *,
    optional: bool = False,
) -> tuple[str | None, bool]:
    if value is None and optional:
        return None, True
    if not isinstance(value, str) or not value:
        return None, False
    return value[:maximum], len(value) <= maximum


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


_EVENT_LABELS: dict[str, str] = {
    "initialize_project": "Project created",
    "attach_specification": "Specification attached",
    "register_manifest": "Repository manifest registered",
    "propose_plan_revision": "Plan proposed",
    "approve_plan": "Plan approved",
    "begin_work_unit": "Work unit started",
    "record_patch_preview": "Patch preview ready",
    "approve_patch": "Patch approved",
    "begin_patch_application": "Patch application started",
    "record_patch_result": "Patch applied",
    "record_rollback_preview": "Rollback preview ready",
    "approve_rollback": "Rollback approved",
    "begin_rollback": "Rollback started",
    "record_command_preview": "Command preview ready",
    "approve_command": "Command approved",
    "begin_command_execution": "Command execution started",
    "record_command_result": "Command result recorded",
    "request_verification": "Verification requested",
    "record_verifier_result": "Verification recorded",
    "submit_manual_evidence": "Manual evidence submitted",
    "request_clarification": "Clarification requested",
    "mark_blocked": "Project paused",
    "revise_scope": "Scope revised",
    "initiate_repair": "Repair initiated",
    "record_rollback": "Rollback recorded",
    "complete_work_unit": "Work unit completed",
    "request_handoff": "Handoff requested",
    "finalize_project": "Project finished",
    "cancel_project": "Project cancelled",
    "acknowledge_execution_cancellation": "Execution cancellation acknowledged",
    "reconcile_legacy": "Legacy record reconciled",
    "recover_attempt": "Execution attempt recovered",
}


def _event_label(event_type: str) -> str:
    return _EVENT_LABELS.get(event_type, event_type.replace("_", " ").capitalize())


def _http_error(error: ProjectControlError) -> HTTPException:
    status_code = 409
    if error.code == ProjectControlErrorCode.PROJECT_NOT_FOUND:
        status_code = 404
    elif error.code == ProjectControlErrorCode.INVALID_COMMAND:
        status_code = 422
    return HTTPException(status_code=status_code, detail=error.as_dict())
