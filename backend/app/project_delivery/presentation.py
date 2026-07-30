from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.app.project_delivery.contracts import DeliveryStatus, VerificationState, WorkUnitStatus
from backend.app.project_delivery.service import public_delivery_job
from backend.app.schemas.api import ChatRunResponse


def build_delivery_action(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job["status"])
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    units = [item for item in plan.get("work_units", []) if isinstance(item, dict)]
    runtime = {str(item.get("work_unit_id")): str(item.get("status")) for item in job.get("work_unit_execution_states") or [] if isinstance(item, dict)}
    completed = sum(1 for item in units if runtime.get(str(item.get("work_unit_id"))) == "completed")
    criteria = list((job.get("specification") or {}).get("acceptance_criteria") or [])
    required = [item for item in criteria if item.get("required") is not False]
    latest: dict[str, str] = {}
    revision_id = str((job.get("plan_revision") or {}).get("plan_revision_id") or "")
    manifest_hash = str(job.get("project_state_hash") or "")
    fresh_results = {
        str(item.get("verifier_result_id")) for item in job.get("verifier_results") or []
        if isinstance(item, dict) and item.get("plan_revision_id") == revision_id and item.get("input_manifest_hash") == manifest_hash
    }
    for record in job.get("verification_records") or []:
        if isinstance(record, dict):
            state = str(record.get("state") or "pending")
            if state == VerificationState.SATISFIED.value and str(record.get("verifier_result_id")) not in fresh_results:
                state = "stale"
            latest[str(record.get("criterion_id") or "")] = state
    satisfied = sum(1 for item in required if latest.get(str(item.get("criterion_id"))) in {VerificationState.SATISFIED.value, VerificationState.WAIVED.value})
    title = {
        DeliveryStatus.CLARIFICATION.value: "Project delivery needs clarification",
        DeliveryStatus.AWAITING_PLAN_APPROVAL.value: "Review project delivery plan",
        DeliveryStatus.PLAN_APPROVED.value: "Project delivery plan approved",
        DeliveryStatus.REPLANNING.value: "Project delivery needs replanning",
        DeliveryStatus.LIMIT_REACHED.value: "Project delivery limit reached",
        DeliveryStatus.AWAITING_MANUAL.value: "Project delivery needs a manual check",
        DeliveryStatus.COMPLETED.value: "Project delivery completed",
        DeliveryStatus.BLOCKED.value: "Project delivery blocked",
        DeliveryStatus.CANCELLED.value: "Project delivery cancelled",
    }.get(status, "Project delivery")
    return {
        "action_id": job["delivery_job_id"], "action_type": "project_delivery", "title": title,
        "summary": str((job.get("specification") or {}).get("normalized_objective") or job.get("original_user_request") or "Project delivery"),
        "steps": [str(item.get("title") or "Work unit") for item in units],
        "safety_information": {
            "plan_approval_only_prepares_work": True, "patch_approval_required_per_preview": True,
            "command_approval_is_separate": True, "repairs_and_reruns_require_approval": True,
            "model_cannot_authorize_or_complete": True,
        },
        "status": status,
        "approval_required": status == DeliveryStatus.AWAITING_PLAN_APPROVAL.value,
        "result_summary": (
            f"Work units {completed} of {len(units)} complete; required criteria {satisfied} of {len(required)} satisfied."
            if units else "The bounded task specification is awaiting clarification."
        ),
        "error": (job.get("last_error") or {}).get("message") if isinstance(job.get("last_error"), dict) else None,
        "technical_details": {
            "project_delivery": public_delivery_job(job),
            "progress": {"completed_work_units": completed, "total_work_units": len(units), "satisfied_required_criteria": satisfied, "total_required_criteria": len(required)},
        },
    }


def build_delivery_chat_run(
    job: dict[str, Any], *, message: str, response: str | None = None,
    run_id: str | None = None,
) -> ChatRunResponse:
    status = str(job["status"])
    if response is None:
        if status == DeliveryStatus.CLARIFICATION.value:
            clarification = next((item for item in job.get("clarifications") or [] if item.get("status") == "pending"), {})
            response = str(clarification.get("question") or "I need one material clarification before planning this delivery.")
        elif status == DeliveryStatus.AWAITING_PLAN_APPROVAL.value:
            response = "I prepared an immutable project delivery plan. Approving it only allows the first work unit to be prepared; it does not modify files or run commands."
        elif status == DeliveryStatus.COMPLETED.value:
            response = "All required acceptance criteria have fresh typed verifier evidence. The client-ready handoff is available in this conversation."
        else:
            response = "The bounded project delivery state was updated. No unapproved file or command action was performed."
    paths = list((job.get("specification") or {}).get("evidence_references") or [])[:20]
    return ChatRunResponse(
        run_id=run_id or uuid4().hex, conversation_id=str(job["conversation_id"]),
        user_message=message, assistant_response=response, selected_specialist="project_delivery",
        intent="project_delivery", confidence=1.0, rag_used=False,
        rag_skip_reason="Project delivery uses bounded Stage 6 evidence from the authorized workspace.",
        rag_context_count=0, source_count=len(paths), source_paths=paths,
        grounding_status="grounded" if paths else "weak", runtime_decision=status,
        safety_decision="approval_bound", used_real_slm=(job.get("specification") or {}).get("specification_source") == "model-assisted",
        slm_provider=str((job.get("specification") or {}).get("provider_status") or "not_invoked"),
        slm_fallback_reason="Deterministic interpretation runs before bounded model fallback.",
        memory_used=True, created_at=datetime.now(timezone.utc), trace_summary=[{
            "phase": "project_delivery", "title": "Project delivery state updated",
            "detail": "Stage 9 preserved independent plan, patch, command, repair, rerun, and rollback approval boundaries.",
            "status": "passed", "data": {"delivery_job_id": job["delivery_job_id"], "status": status},
        }], action=build_delivery_action(job),
    )


__all__ = ["build_delivery_action", "build_delivery_chat_run"]
