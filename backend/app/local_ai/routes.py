from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from backend.app.local_ai.contracts import (
    HardwareAdmissionRequest,
    SchedulerStatus,
)
from backend.app.local_ai.service import LocalAIService
from backend.app.project_control.contracts import StrictModel


class RefreshCapabilitiesRequest(StrictModel):
    schema_version: Literal["astra.local-ai.refresh.v1"] = "astra.local-ai.refresh.v1"
    actor_id: str = Field(min_length=1, max_length=200)
    expected_snapshot_id: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ModelEnableRequest(StrictModel):
    schema_version: Literal["astra.local-ai.model-enable.v1"] = "astra.local-ai.model-enable.v1"
    actor_id: str = Field(min_length=1, max_length=200)
    enabled: bool
    expected_configuration_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class CancelModelJobRequest(StrictModel):
    schema_version: Literal["astra.local-ai.cancel-job.v1"] = "astra.local-ai.cancel-job.v1"
    actor_id: str = Field(min_length=1, max_length=200)
    expected_status: SchedulerStatus
    idempotency_key: str = Field(min_length=1, max_length=200)


class RoleMappingRequest(StrictModel):
    schema_version: Literal["astra.local-ai.role-mapping-request.v1"] = "astra.local-ai.role-mapping-request.v1"
    actor_id: str = Field(min_length=1, max_length=200)
    model_profile_id: str = Field(min_length=1, max_length=200)
    expected_configuration_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


def create_local_ai_router(service: LocalAIService) -> APIRouter:
    router = APIRouter(prefix="/runtime/local-ai", tags=["local-ai"])

    @router.get("/doctor")
    def doctor(refresh: bool = Query(default=False)):
        return service.capability_report(refresh=refresh)

    @router.get("/capabilities")
    def capabilities():
        return service.capability_report()

    @router.post("/capabilities/refresh")
    def refresh_capabilities(request: RefreshCapabilitiesRequest):
        try:
            return service.refresh_capabilities(
                actor_id=request.actor_id,
                expected_snapshot_id=request.expected_snapshot_id,
                idempotency_key=request.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc

    @router.get("/providers")
    def providers():
        return {"schema_version": "astra.local-ai.providers.v1", "items": service.providers()}

    @router.get("/models")
    def models():
        return {"schema_version": "astra.local-ai.models.v1", "items": service.models()}

    @router.post("/models/{model_profile_id}/enabled")
    def set_model_enabled(model_profile_id: str, request: ModelEnableRequest):
        try:
            return service.set_model_enabled(
                model_profile_id, enabled=request.enabled, actor_id=request.actor_id,
                expected_version=request.expected_configuration_version,
                idempotency_key=request.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc

    @router.post("/roles/{role_id}")
    def set_role_mapping(role_id: str, request: RoleMappingRequest):
        try:
            return service.set_role_mapping(
                role_id, request.model_profile_id, actor_id=request.actor_id,
                expected_version=request.expected_configuration_version,
                idempotency_key=request.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc

    @router.post("/admission/preview")
    def admission_preview(request: HardwareAdmissionRequest):
        return service.admission_preview(request)

    @router.get("/scheduler")
    def scheduler():
        return {"schema_version": "astra.local-ai.scheduler.v1", "items": service.scheduler_jobs()}

    @router.post("/scheduler/{job_id}/cancel")
    def cancel_job(job_id: str, request: CancelModelJobRequest):
        try:
            return service.cancel_job(
                job_id, actor_id=request.actor_id, expected_status=request.expected_status,
                idempotency_key=request.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc

    @router.get("/provenance")
    def provenance(limit: int = Query(default=100, ge=1, le=500)):
        return {"schema_version": "astra.local-ai.provenance-collection.v1", "items": service.provenance(limit)}

    @router.get("/future-status")
    def future_status():
        return service.disabled_status()

    return router


__all__ = ["create_local_ai_router"]
