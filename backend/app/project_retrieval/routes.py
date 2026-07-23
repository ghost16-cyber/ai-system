from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.project_control import ProjectControlError
from backend.app.project_retrieval.contracts import (
    CorpusGeneration,
    CorpusIngestionRequest,
    RetrievalArtifactCollection,
    RetrievalEvidenceArtifact,
    RetrievalRequest,
    RetrievalStatus,
)
from backend.app.project_retrieval.service import (
    ProjectRetrievalError,
    ProjectRetrievalService,
)


def create_project_retrieval_router(service: ProjectRetrievalService) -> APIRouter:
    router = APIRouter(prefix="/chat/projects/{project_id}/rag", tags=["project-rag"])

    @router.post(
        "/ingest",
        response_model=CorpusGeneration,
        status_code=status.HTTP_201_CREATED,
    )
    def ingest(project_id: str, request: CorpusIngestionRequest) -> CorpusGeneration:
        _require_project_path(project_id, request.project_id)
        try:
            return service.ingest_project_corpus(request)
        except (ProjectRetrievalError, ProjectControlError) as exc:
            raise _http_error(exc) from exc

    @router.post("/retrieve", response_model=RetrievalEvidenceArtifact)
    def retrieve(project_id: str, request: RetrievalRequest) -> RetrievalEvidenceArtifact:
        _require_project_path(project_id, request.project_id)
        try:
            return service.retrieve(request)
        except (ProjectRetrievalError, ProjectControlError) as exc:
            raise _http_error(exc) from exc

    @router.get("/status", response_model=RetrievalStatus)
    def retrieval_status(project_id: str) -> RetrievalStatus:
        try:
            return service.status(project_id)
        except (ProjectRetrievalError, ProjectControlError) as exc:
            raise _http_error(exc) from exc

    @router.get("/artifacts", response_model=RetrievalArtifactCollection)
    def artifacts(
        project_id: str,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> RetrievalArtifactCollection:
        try:
            return service.list_retrieval_artifacts(project_id, limit=limit)
        except (ProjectRetrievalError, ProjectControlError) as exc:
            raise _http_error(exc) from exc

    @router.get(
        "/artifacts/{artifact_id}",
        response_model=RetrievalEvidenceArtifact,
    )
    def artifact(project_id: str, artifact_id: str) -> RetrievalEvidenceArtifact:
        try:
            return service.get_retrieval_artifact(project_id, artifact_id)
        except (ProjectRetrievalError, ProjectControlError) as exc:
            raise _http_error(exc) from exc

    return router


def _require_project_path(path_project_id: str, body_project_id: str) -> None:
    if path_project_id != body_project_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "project_binding_mismatch"},
        )


def _http_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", str(exc))
    if hasattr(code, "value"):
        code = code.value
    code = str(code)
    if code in {"not_found", "project_not_found", "retrieval_artifact_not_found"}:
        http_status = status.HTTP_404_NOT_FOUND
    elif code in {
        "invalid_path",
        "source_size_limit_exceeded",
        "file_count_limit_exceeded",
        "corpus_byte_limit_exceeded",
        "project_chunk_limit_exceeded",
    }:
        http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        http_status = status.HTTP_409_CONFLICT
    return HTTPException(status_code=http_status, detail={"code": code})
