from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.project_api.contracts import (
    CanonicalArtifactSummary,
    CanonicalProjectCollection,
    CanonicalProjectCreateRequest,
    CanonicalProjectResponse,
)
from backend.app.project_artifacts import ProjectArtifact
from backend.app.project_control import ProjectControlError, ProjectControlErrorCode
from backend.app.project_control.project_service import CanonicalProjectService


FolderAuthorityResolver = Callable[[str], dict[str, Any]]


def create_project_router(
    service: CanonicalProjectService,
    *,
    folder_authority_resolver: FolderAuthorityResolver | None = None,
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
            return _response(service, project.project_run_id)
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    @router.get(
        "/chat/projects/{project_run_id}",
        response_model=CanonicalProjectResponse,
    )
    def get_project(project_run_id: str) -> CanonicalProjectResponse:
        try:
            return _response(service, project_run_id)
        except ProjectControlError as exc:
            raise _http_error(exc) from exc

    @router.get(
        "/chat/conversations/{conversation_id}/projects",
        response_model=CanonicalProjectCollection,
    )
    def list_projects(conversation_id: str) -> CanonicalProjectCollection:
        try:
            items = tuple(
                _response(service, project.project_run_id)
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

    return router


def _response(service: CanonicalProjectService, project_run_id: str) -> CanonicalProjectResponse:
    project = service.get_project(project_run_id)
    artifacts = tuple(_summary(item) for item in service.list_artifacts(project_run_id))
    return CanonicalProjectResponse(project=project, artifacts=artifacts)


def _summary(artifact: ProjectArtifact) -> CanonicalArtifactSummary:
    return CanonicalArtifactSummary(
        artifact_id=artifact.artifact_id,
        artifact_type=artifact.artifact_type.value,
        revision_number=artifact.revision_number,
        binding_hash=artifact.binding_hash,
        content_hash=artifact.content_hash,
        created_at=artifact.created_at.isoformat(),
    )


def _http_error(error: ProjectControlError) -> HTTPException:
    status_code = 409
    if error.code == ProjectControlErrorCode.PROJECT_NOT_FOUND:
        status_code = 404
    elif error.code == ProjectControlErrorCode.INVALID_COMMAND:
        status_code = 422
    return HTTPException(status_code=status_code, detail=error.as_dict())
