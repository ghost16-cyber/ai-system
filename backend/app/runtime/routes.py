from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.app.project_control import ProjectControlError, ProjectControlErrorCode
from backend.app.runtime.manager import RuntimeManager


def create_runtime_router(runtime_manager: RuntimeManager) -> APIRouter:
    """Read-only runtime observability surface. Every handler is a GET that
    delegates straight to RuntimeManager's own read methods -- there are no
    mutation routes here, matching the fact that RuntimeManager itself holds
    no retrieval/execution/mutation/approval authority to expose.
    """
    router = APIRouter(prefix="/runtime", tags=["runtime"])

    @router.get("")
    def snapshot():
        return runtime_manager.snapshot()

    @router.get("/health")
    def health():
        return runtime_manager.health()

    @router.get("/readiness")
    def readiness(project_id: str | None = Query(default=None)):
        return runtime_manager.readiness(project_id=project_id)

    @router.get("/telemetry")
    def telemetry():
        return runtime_manager.telemetry()

    @router.get("/corpus")
    def corpus(project_id: str = Query()):
        try:
            status = runtime_manager.corpus_status(project_id)
        except ProjectControlError as exc:
            status_code = 404 if exc.code == ProjectControlErrorCode.PROJECT_NOT_FOUND else 409
            raise HTTPException(status_code=status_code, detail=exc.as_dict()) from exc
        if status is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "corpus_manager_unavailable", "message": "Corpus management is not configured."},
            )
        return status

    @router.get("/cache")
    def cache():
        return runtime_manager.cache_status()

    @router.get("/jobs")
    def jobs(limit: int = Query(default=20, ge=1, le=200)):
        return runtime_manager.jobs_status(limit=limit)

    return router
