from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.app.folders import completed_folder_access, validate_root_identity
from backend.app.project_validation.contracts import HumanReviewAction, ValidationLimits
from backend.app.project_validation.presentation import build_validation_chat_run, public_campaign
from backend.app.project_validation.service import ProjectValidationError, ProjectValidationService

router = APIRouter(prefix="/project-validation", tags=["project-validation"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CampaignCreateRequest(StrictRequest):
    conversation_id: str = Field(min_length=1, max_length=128)
    engagement_id: str = Field(min_length=1, max_length=128)
    delivery_job_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(default="local-user", min_length=1, max_length=256)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    limits: ValidationLimits | None = None


class CampaignActionRequest(StrictRequest):
    conversation_id: str = Field(min_length=1, max_length=128)
    expected_state_version: int = Field(ge=1)
    actor_id: str = Field(default="local-user", min_length=1, max_length=256)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)


class ActiveRunActionRequest(CampaignActionRequest):
    expected_run_version: int = Field(ge=1)


class EvaluateRunRequest(ActiveRunActionRequest):
    pass


class CancelCampaignRequest(CampaignActionRequest):
    expected_run_version: int | None = Field(default=None, ge=1)
    reason: str = Field(default="Cancelled by the user.", max_length=2000)


class HumanReviewRequest(EvaluateRunRequest):
    scope_revision_id: str = Field(min_length=1, max_length=128)
    scope_hash: str = Field(min_length=64, max_length=64)
    validation_result_hash: str = Field(min_length=64, max_length=64)
    action: HumanReviewAction
    notes: str = Field(default="", max_length=4000)


class RemediationRequestBody(StrictRequest):
    conversation_id: str = Field(min_length=1, max_length=128)
    requested_by: str = Field(default="local-user", min_length=1, max_length=256)
    route: str = Field(pattern="^(stage8_repair|stage9_replan|stage10_scope_change|manual_action|external_verification|cancel)$")


def _repository(request: Request):
    repository = getattr(request.app.state, "analysis_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail={"code": "repository_unavailable", "message": "Astra storage is unavailable."})
    return repository


def _service(request: Request) -> ProjectValidationService:
    service = getattr(request.app.state, "project_validation_service", None)
    if service is None:
        repository = _repository(request)
        service = ProjectValidationService(repository.database_path)
        request.app.state.project_validation_service = service
    return service


def _access(repository, conversation_id: str) -> dict[str, Any]:
    access = completed_folder_access(repository.list_chat_runs_for_conversation(conversation_id))
    if access is None:
        raise HTTPException(status_code=409, detail={"code": "folder_not_authorized", "message": "This conversation does not have an approved project folder."})
    root = str(access.get("approved_root") or "")
    fingerprint = str(access.get("root_fingerprint") or "")
    try:
        validate_root_identity(root, fingerprint)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail={"code": "workspace_missing", "message": "The approved project folder no longer exists."}) from error
    except Exception as error:
        raise HTTPException(status_code=409, detail={"code": "workspace_changed", "message": "The approved project folder identity could not be verified."}) from error
    return access


def _engagement(repository, engagement_id: str, conversation_id: str) -> dict[str, Any]:
    try:
        value = repository.get_client_engagement(engagement_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Client engagement not found."}) from error
    if str(value.get("conversation_id") or "") != conversation_id:
        raise HTTPException(status_code=409, detail={"code": "ownership_mismatch", "message": "The engagement belongs to a different conversation."})
    return value


def _delivery(repository, delivery_job_id: str, conversation_id: str) -> dict[str, Any]:
    try:
        value = repository.get_project_delivery_job(delivery_job_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Project delivery job not found."}) from error
    if str(value.get("conversation_id") or "") != conversation_id:
        raise HTTPException(status_code=409, detail={"code": "ownership_mismatch", "message": "The project belongs to a different conversation."})
    return value


def _persist_run(repository, campaign, run, message: str):
    chat_run = build_validation_chat_run(campaign, run, message=message)
    repository.store_chat_run(chat_run)
    return chat_run


def _http_error(error: ProjectValidationError) -> HTTPException:
    status = 404 if error.code == "not_found" else 400 if error.code in {"invalid_scope", "tampered_scope"} else 409
    return HTTPException(status_code=status, detail={"code": error.code, "message": error.message})


def _delivery_evidence(delivery: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = {}
    for record in delivery.get("verification_records") or []:
        if not isinstance(record, dict):
            continue
        criterion_id = str(record.get("criterion_id") or "")
        if not criterion_id:
            continue
        state = str(record.get("state") or "").lower()
        result = {
            "satisfied": "passed", "passed": "passed", "failed": "failed",
            "blocked": "blocked", "manual-user-check": "requires_human_review",
            "waived-by-user": "requires_human_review",
        }.get(state, state or "blocked")
        evidence.setdefault(criterion_id, []).append({
            "evidence_id": str(record.get("verification_id") or f"verification:{criterion_id}"),
            "evidence_type": str(record.get("method") or "stage9_verification"),
            "summary": str(record.get("failure_explanation") or f"Stage 9 verification state: {state}"),
            "source_reference": str(record.get("verification_id") or f"criterion:{criterion_id}"),
            "content_hash": record.get("verification_hash"),
            "result": result,
            "deterministic": str(record.get("method") or "") != "manual_user_verification_required",
        })
    handoff = delivery.get("handoff") or {}
    for criterion_id, result in (handoff.get("acceptance_criterion_results") or {}).items():
        if criterion_id in evidence:
            continue
        evidence.setdefault(str(criterion_id), []).append({
            "evidence_id": f"handoff:{criterion_id}",
            "evidence_type": "stage9_handoff",
            "summary": f"Stage 9 handoff recorded: {result}",
            "source_reference": str(handoff.get("handoff_id") or "stage9-handoff"),
            "result": str(result),
            "deterministic": False,
        })
    return evidence


def _allowed_paths(delivery: dict[str, Any]) -> list[str]:
    paths: set[str] = set()
    for unit in ((delivery.get("plan") or {}).get("work_units") or []):
        if isinstance(unit, dict):
            paths.update(str(item) for item in unit.get("expected_files") or [] if isinstance(item, str))
    handoff = delivery.get("handoff") or {}
    for key in ("changed_files", "created_files", "removed_files"):
        paths.update(str(item) for item in handoff.get(key) or [] if isinstance(item, str))
    return sorted(paths)


def _artifact_hints(delivery: dict[str, Any], engagement: dict[str, Any]) -> dict[str, str]:
    handoff = delivery.get("handoff") or {}
    available = [
        str(item) for key in ("created_files", "changed_files")
        for item in handoff.get(key) or [] if isinstance(item, str)
    ]
    revision = engagement.get("current_scope_revision") or engagement.get("current_scope") or {}
    hints: dict[str, str] = {}
    for deliverable in (revision.get("scope") or {}).get("deliverables") or []:
        if not isinstance(deliverable, dict):
            continue
        did = str(deliverable.get("deliverable_id") or "")
        words = [word.lower() for word in str(deliverable.get("title") or "").split() if len(word) >= 4]
        match = next((path for path in available if any(word in path.lower() for word in words)), None)
        if did and match:
            hints[did] = match
    return hints


@router.post("/campaigns")
def create_validation_campaign(body: CampaignCreateRequest, request: Request):
    repository = _repository(request)
    access = _access(repository, body.conversation_id)
    engagement = _engagement(repository, body.engagement_id, body.conversation_id)
    delivery = _delivery(repository, body.delivery_job_id, body.conversation_id)
    if engagement.get("folder_access_id") and engagement.get("folder_access_id") != access.get("action_id"):
        raise HTTPException(status_code=409, detail={"code": "workspace_mismatch", "message": "The engagement belongs to a different folder authorization."})
    try:
        campaign = _service(request).create_campaign(
            engagement=engagement, delivery=delivery, conversation_id=body.conversation_id,
            user_id=body.user_id, authorization_id=str(access["action_id"]),
            workspace_root=str(access["approved_root"]), limits=body.limits,
            idempotency_key=body.idempotency_key,
        )
        return _persist_run(repository, campaign, None, "Create a validation campaign for the approved project.")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.get("/campaigns/{campaign_id}")
def get_validation_campaign(campaign_id: str, conversation_id: str, request: Request):
    try:
        campaign = _service(request).get_campaign(campaign_id, conversation_id=conversation_id)
        run = _service(request).get_run(campaign_id, campaign.active_run_id) if campaign.active_run_id else None
        return public_campaign(campaign, run)
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/prepare")
def prepare_validation_campaign(campaign_id: str, body: CampaignActionRequest, request: Request):
    repository = _repository(request)
    access = _access(repository, body.conversation_id)
    try:
        campaign = _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        if campaign.scope_reference.engagement_id:
            engagement = _engagement(repository, campaign.scope_reference.engagement_id, body.conversation_id)
            if engagement.get("folder_access_id") and engagement.get("folder_access_id") != access.get("action_id"):
                raise HTTPException(status_code=409, detail={"code": "workspace_mismatch", "message": "The campaign belongs to a different folder authorization."})
        campaign = _service(request).prepare(
            campaign_id=campaign_id, expected_version=body.expected_state_version,
            authorization_id=str(access["action_id"]), workspace_root=str(access["approved_root"]),
            actor_id=body.actor_id, copy_workspace=False,
        )
        return _persist_run(repository, campaign, None, "Prepare the authorized validation workspace and capture its baseline.")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/runs")
def start_validation_run(campaign_id: str, body: CampaignActionRequest, request: Request):
    repository = _repository(request)
    try:
        campaign = _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        campaign, run = _service(request).start_run(
            campaign_id=campaign_id, expected_version=body.expected_state_version,
            actor_id=body.actor_id, idempotency_key=body.idempotency_key,
        )
        return _persist_run(repository, campaign, run, "Start a new bounded validation run.")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/runs/{run_id}/evaluate")
def evaluate_validation_run(campaign_id: str, run_id: str, body: EvaluateRunRequest, request: Request):
    repository = _repository(request)
    try:
        campaign = _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        engagement = _engagement(repository, campaign.scope_reference.engagement_id, body.conversation_id)
        delivery = _delivery(repository, campaign.project_reference.delivery_job_id, body.conversation_id)
        campaign, run = _service(request).evaluate_run(
            campaign_id=campaign_id, run_id=run_id,
            expected_campaign_version=body.expected_state_version,
            expected_run_version=body.expected_run_version, actor_id=body.actor_id,
            evidence_by_criterion=_delivery_evidence(delivery),
            artifact_hints=_artifact_hints(delivery, engagement),
            allowed_paths=_allowed_paths(delivery),
            regressed_tests=[], current_delivery=delivery, current_engagement=engagement,
        )
        return _persist_run(repository, campaign, run, "Evaluate the approved acceptance criteria and delivery artifacts.")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/runs/{run_id}/review")
def review_validation_run(campaign_id: str, run_id: str, body: HumanReviewRequest, request: Request):
    repository = _repository(request)
    try:
        campaign = _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        engagement = _engagement(repository, campaign.scope_reference.engagement_id, body.conversation_id)
        campaign, run, _review = _service(request).human_review(
            campaign_id=campaign_id, run_id=run_id,
            expected_campaign_version=body.expected_state_version,
            expected_run_version=body.expected_run_version,
            scope_revision_id=body.scope_revision_id, scope_hash=body.scope_hash,
            validation_result_hash=body.validation_result_hash, reviewer_id=body.actor_id,
            action=body.action, notes=body.notes, current_engagement=engagement,
        )
        return _persist_run(repository, campaign, run, "Record the exact human delivery-review decision.")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/runs/{run_id}/pause")
def pause_validation_run(campaign_id: str, run_id: str, body: ActiveRunActionRequest, request: Request):
    repository = _repository(request)
    try:
        _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        campaign, run = _service(request).pause(
            campaign_id=campaign_id, run_id=run_id,
            expected_campaign_version=body.expected_state_version, expected_run_version=body.expected_run_version,
            actor_id=body.actor_id,
        )
        return _persist_run(repository, campaign, run, "Pause project validation safely.")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/runs/{run_id}/resume")
def resume_validation_run(campaign_id: str, run_id: str, body: ActiveRunActionRequest, request: Request):
    repository = _repository(request)
    try:
        campaign = _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        engagement = _engagement(repository, campaign.scope_reference.engagement_id, body.conversation_id)
        delivery = _delivery(repository, campaign.project_reference.delivery_job_id, body.conversation_id)
        campaign, run = _service(request).resume(
            campaign_id=campaign_id, run_id=run_id,
            expected_campaign_version=body.expected_state_version, expected_run_version=body.expected_run_version,
            actor_id=body.actor_id, current_engagement=engagement, current_delivery=delivery,
        )
        return _persist_run(repository, campaign, run, "Resume project validation after rechecking the approved scope and project.")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/restore-baseline")
def restore_validation_baseline(campaign_id: str, body: CampaignActionRequest, request: Request):
    repository = _repository(request)
    try:
        _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        campaign, result = _service(request).restore_baseline(
            campaign_id=campaign_id, expected_campaign_version=body.expected_state_version, actor_id=body.actor_id,
        )
        return _persist_run(repository, campaign, _service(request).get_run(campaign_id, campaign.active_run_id) if campaign.active_run_id else None,
                            f"Restore the validation baseline ({len(result['restored'])} files restored).")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/cancel")
def cancel_validation_campaign(campaign_id: str, body: CancelCampaignRequest, request: Request):
    repository = _repository(request)
    try:
        _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        campaign, run = _service(request).cancel(
            campaign_id=campaign_id, expected_campaign_version=body.expected_state_version,
            expected_run_version=body.expected_run_version, actor_id=body.actor_id, reason=body.reason,
        )
        return _persist_run(repository, campaign, run, "Cancel project validation without sending or deploying anything.")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/runs/{run_id}/remediation")
def request_validation_remediation(campaign_id: str, run_id: str, body: RemediationRequestBody, request: Request):
    try:
        _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        return _service(request).request_remediation(
            campaign_id=campaign_id, run_id=run_id, requested_by=body.requested_by, route=body.route,
        ).model_dump(mode="json")
    except ProjectValidationError as error:
        raise _http_error(error) from error


@router.post("/campaigns/{campaign_id}/recover")
def recover_validation_campaign(campaign_id: str, body: CampaignActionRequest, request: Request):
    repository = _repository(request)
    try:
        _service(request).get_campaign(campaign_id, conversation_id=body.conversation_id)
        campaign = _service(request).recover(campaign_id=campaign_id, actor_id=body.actor_id)
        run = _service(request).get_run(campaign_id, campaign.active_run_id) if campaign.active_run_id else None
        return _persist_run(repository, campaign, run, "Recover interrupted project validation safely.")
    except ProjectValidationError as error:
        raise _http_error(error) from error


__all__ = ["router"]
