from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.app.client_engagement.contracts import EngagementState
from backend.app.client_engagement.evidence import public_evidence
from backend.app.client_engagement.service import public_engagement
from backend.app.schemas.api import ChatRunResponse


def build_engagement_action(engagement: dict) -> dict:
    public = public_engagement(engagement)
    state = str(engagement["state"])
    scope = engagement.get("current_scope") or {}
    title = {
        EngagementState.CLARIFICATION_REQUIRED.value: "Client engagement needs clarification",
        EngagementState.AWAITING_SCOPE_APPROVAL.value: "Review exact client scope",
        EngagementState.SCOPE_APPROVED.value: "Client scope approved",
        EngagementState.PROJECT_LAUNCHED.value: "Project delivery launched",
        EngagementState.SCOPE_CHANGE_REVIEW.value: "Review scope change",
        EngagementState.CANCELLED.value: "Client engagement cancelled",
        EngagementState.FAILED.value: "Client engagement needs attention",
    }.get(state, "Client engagement intake")
    return {
        "action_id": engagement["engagement_id"], "action_type": "client_engagement",
        "title": title, "summary": public.understood_outcome, "status": state,
        "approval_required": state in {EngagementState.AWAITING_SCOPE_APPROVAL.value, EngagementState.SCOPE_CHANGE_REVIEW.value},
        "safety_information": {
            "approves_exact_scope_only": True, "scope_hash_bound": True,
            "does_not_approve_patches": True, "does_not_approve_commands": True,
            "external_communication_disabled": True,
        },
        "technical_details": {"client_engagement": public.model_dump(mode="json")},
    }


def build_engagement_chat_run(engagement: dict, *, message: str, response: str | None = None, run_id: str | None = None) -> ChatRunResponse:
    state = str(engagement["state"])
    if response is None:
        if state == EngagementState.CLARIFICATION_REQUIRED.value:
            response = "I found a few material gaps. Answer any or all questions, or explicitly ask me to use the documented reasonable assumptions."
        elif state in {EngagementState.AWAITING_SCOPE_APPROVAL.value, EngagementState.SCOPE_CHANGE_REVIEW.value}:
            response = "I prepared a client-readable, evidence-grounded scope. Approval applies only to the exact displayed revision and does not approve project patches or commands."
        elif state == EngagementState.SCOPE_APPROVED.value:
            response = "The exact scope revision is approved. Project launch is a separate idempotent action; project work will retain all Stage 9 approval gates."
        elif state == EngagementState.PROJECT_LAUNCHED.value:
            response = "The approved scope launched one Stage 9 project in this conversation. Patch and command approvals remain separate."
        else:
            response = "The client engagement state was updated without executing project changes or external communication."
    evidence = engagement.get("evidence") or []
    return ChatRunResponse(
        run_id=run_id or uuid4().hex, conversation_id=str(engagement["conversation_id"]),
        user_message=message, assistant_response=response, selected_specialist="client_engagement",
        intent="client_engagement", confidence=1.0, rag_used=False,
        rag_skip_reason="Stage 10 uses bounded evidence from the engagement and explicitly authorized folder context.",
        rag_context_count=0, source_count=len(evidence), source_paths=[],
        grounding_status="grounded" if evidence else "weak", runtime_decision=state,
        safety_decision="exact_scope_approval_bound", used_real_slm=bool(engagement.get("model_fallback_used")),
        slm_provider="bounded_model_fallback" if engagement.get("model_fallback_used") else "not_invoked",
        slm_fallback_reason="Deterministic extraction runs first.", memory_used=True,
        created_at=datetime.now(timezone.utc), trace_summary=[{
            "phase": "client_engagement", "title": "Engagement state updated",
            "detail": "Scope approval remains distinct from Stage 9 plan, patch, command, repair, and rollback approvals.",
            "status": "passed", "data": {"engagement_id": engagement["engagement_id"], "state": state},
        }], action=build_engagement_action(engagement),
    )


__all__ = ["build_engagement_action", "build_engagement_chat_run"]
