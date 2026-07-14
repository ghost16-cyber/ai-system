from __future__ import annotations

from typing import Any

from backend.app.project_validation.contracts import (
    ValidationCampaign,
    ValidationRun,
    ValidationState,
)


def public_campaign(campaign: ValidationCampaign, run: ValidationRun | None = None) -> dict[str, Any]:
    """Return bounded, client-readable validation state without raw workspace paths."""
    acceptance = run.acceptance_evaluations if run else []
    manifest = run.deliverable_manifest if run else None
    quality = run.quality_assessment if run else None
    regression = run.regression_result if run else None
    return {
        "schema_version": campaign.schema_version,
        "campaign_id": campaign.campaign_id,
        "conversation_id": campaign.conversation_id,
        "state": campaign.state.value,
        "state_version": campaign.state_version,
        "scope": {
            "engagement_id": campaign.scope_reference.engagement_id,
            "revision_id": campaign.scope_reference.revision_id,
            "revision_number": campaign.scope_reference.revision_number,
            "scope_hash": campaign.scope_reference.scope_hash,
            "objective": campaign.scope_snapshot.objective,
            "deliverables": [
                {
                    "deliverable_id": item.deliverable_id,
                    "title": item.title,
                    "description": item.description,
                    "acceptance_criteria": [criterion.model_dump(mode="json") for criterion in item.acceptance_criteria],
                }
                for item in campaign.scope_snapshot.deliverables
            ],
            "exclusions": campaign.scope_snapshot.exclusions,
        },
        "project": {
            "delivery_job_id": campaign.project_reference.delivery_job_id,
            "plan_revision": campaign.project_reference.plan_revision,
            "status": campaign.project_reference.status,
        },
        "workspace": ({
            "workspace_id": campaign.workspace.workspace_id,
            "display_name": campaign.workspace.display_name,
            "isolated": campaign.workspace.isolated,
            "prepared_at": campaign.workspace.prepared_at.isoformat(),
        } if campaign.workspace else None),
        "baseline": ({
            "snapshot_id": campaign.baseline_snapshot.snapshot_id,
            "file_count": campaign.baseline_snapshot.file_count,
            "total_bytes": campaign.baseline_snapshot.total_bytes,
            "dirty_worktree": campaign.baseline_snapshot.dirty_worktree,
            "stale": campaign.baseline_snapshot.stale,
            "restorable": campaign.baseline_snapshot.restorable,
        } if campaign.baseline_snapshot else None),
        "limits": campaign.limits.model_dump(mode="json"),
        "active_run_id": campaign.active_run_id,
        "cancelled_reason": campaign.cancelled_reason,
        "run": ({
            "run_id": run.run_id,
            "run_number": run.run_number,
            "state": run.state.value,
            "state_version": run.state_version,
            "budget_usage": run.budget_usage.model_dump(mode="json"),
            "acceptance_summary": {
                "total": len(acceptance),
                "passed": sum(item.result.value == "passed" for item in acceptance),
                "failed": sum(item.result.value == "failed" for item in acceptance),
                "blocked": sum(item.result.value == "blocked" for item in acceptance),
                "human_review": sum(item.human_review_required for item in acceptance),
                "items": [
                    {
                        "criterion_id": item.criterion_id,
                        "criterion_text": item.criterion_text,
                        "result": item.result.value,
                        "blocking": item.blocking,
                        "human_review_required": item.human_review_required,
                        "failure_explanation": item.failure_explanation,
                        "evidence": [
                            {"evidence_id": evidence.evidence_id, "type": evidence.evidence_type, "summary": evidence.summary}
                            for evidence in item.evidence
                            if not evidence.sensitive
                        ],
                    }
                    for item in acceptance
                ],
            },
            "deliverables": ({
                "complete": manifest.complete,
                "missing_deliverable_ids": manifest.missing_deliverable_ids,
                "artifacts": [
                    {
                        "artifact_id": item.artifact_id,
                        "deliverable_id": item.deliverable_id,
                        "client_name": item.client_name,
                        "artifact_type": item.artifact_type.value,
                        "exists": item.exists,
                        "size_bytes": item.size_bytes,
                        "human_review_required": item.human_review_required,
                        "warning": item.warning,
                    }
                    for item in manifest.artifacts
                ],
            } if manifest else None),
            "regression": ({
                "blocking": regression.blocking,
                "unexpected_change_count": len(regression.unexpected_changes),
                "regressed_tests": regression.tests_regressed,
                "summary": regression.summary,
            } if regression else None),
            "quality": ({
                "overall_score": quality.overall_score,
                "minimum_score": quality.minimum_score,
                "uncertainty": quality.uncertainty,
                "blocking_findings": quality.blocking_findings,
                "automated_decision": quality.automated_decision.value,
                "dimensions": [
                    {
                        "name": item.name,
                        "score": item.score,
                        "confidence": item.confidence,
                        "explanation": item.explanation,
                    }
                    for item in quality.dimensions
                ],
            } if quality else None),
            "findings": [
                {
                    "finding_id": item.finding_id,
                    "category": item.category.value,
                    "severity": item.severity,
                    "summary": item.summary,
                    "blocking": item.blocking,
                    "recommended_route": item.recommended_route,
                }
                for item in run.findings
            ],
            "automated_decision": run.automated_decision.value if run.automated_decision else None,
            "result_hash": run.result_hash,
            "human_review": ({
                "action": run.human_review.action.value,
                "notes": run.human_review.notes,
                "reviewer_id": run.human_review.reviewer_id,
                "reviewed_at": run.human_review.reviewed_at.isoformat(),
            } if run.human_review else None),
        } if run else None),
    }


def build_validation_action(campaign: ValidationCampaign, run: ValidationRun | None = None) -> dict[str, Any]:
    title = {
        ValidationState.CREATED: "Prepare project validation",
        ValidationState.PREPARING_WORKSPACE: "Preparing validation workspace",
        ValidationState.READY: "Validation is ready to start",
        ValidationState.RUNNING: "Project validation is running",
        ValidationState.BUDGET_EXCEEDED: "Validation paused at a safety limit",
        ValidationState.REMEDIATION_REQUIRED: "Project needs remediation",
        ValidationState.AWAITING_HUMAN_REVIEW: "Review project delivery readiness",
        ValidationState.DELIVERY_READY: "Project approved as delivery-ready",
        ValidationState.DELIVERY_REJECTED: "Project delivery was rejected",
        ValidationState.EXECUTION_PAUSED: "Validation paused safely",
        ValidationState.CANCELLED: "Project validation cancelled",
        ValidationState.FAILED: "Validation needs attention",
    }.get(campaign.state, "Project validation")
    summary = _summary(campaign, run)
    return {
        "action_id": campaign.campaign_id,
        "action_type": "project_validation",
        "title": title,
        "summary": summary,
        "status": campaign.state.value,
        "approval_required": campaign.state == ValidationState.AWAITING_HUMAN_REVIEW,
        "safety_information": {
            "exact_scope_bound": True,
            "exact_validation_result_bound": True,
            "human_review_required_before_delivery_ready": True,
            "does_not_approve_patches": True,
            "does_not_approve_commands": True,
            "does_not_send_or_deploy": True,
        },
        "technical_details": {"project_validation": public_campaign(campaign, run)},
    }


def _summary(campaign: ValidationCampaign, run: ValidationRun | None) -> str:
    if campaign.state == ValidationState.AWAITING_HUMAN_REVIEW and run and run.quality_assessment:
        return f"Automated checks are complete with a quality score of {run.quality_assessment.overall_score:.0f}/100. Human delivery review is still required."
    if campaign.state == ValidationState.REMEDIATION_REQUIRED and run:
        return f"Astra found {len(run.findings)} blocking issue(s). Existing Stage 8–10 workflows must resolve them before another validation run."
    if campaign.state == ValidationState.DELIVERY_READY:
        return "The exact validated result received human approval. Nothing was sent or deployed automatically."
    if campaign.state == ValidationState.BUDGET_EXCEEDED:
        return "Validation stopped safely because a configured resource limit was reached."
    if campaign.state == ValidationState.CANCELLED:
        return campaign.cancelled_reason or "Validation was cancelled safely. Nothing was sent or deployed."
    return f"Validation is in the {campaign.state.value.replace('_', ' ')} phase for the approved scope."


__all__ = ["build_validation_action", "public_campaign"]


def build_validation_chat_run(
    campaign: ValidationCampaign,
    run: ValidationRun | None,
    *,
    message: str,
):
    """Build a persisted chat-native Stage 11 response using Astra's existing contract."""
    from datetime import datetime, timezone
    from uuid import uuid4
    from backend.app.schemas.api import ChatRunResponse

    action = build_validation_action(campaign, run)
    return ChatRunResponse(
        run_id=uuid4().hex,
        conversation_id=campaign.conversation_id,
        user_message=message,
        assistant_response=action["summary"],
        selected_specialist="project_validation",
        intent="project_validation",
        confidence=1.0,
        rag_used=False,
        rag_skip_reason="Stage 11 uses the exact approved scope, Stage 9 records, and authorized workspace evidence.",
        rag_context_count=0,
        source_count=0,
        source_paths=[],
        grounding_status="grounded",
        runtime_decision=campaign.state.value,
        safety_decision="human_delivery_review_required",
        used_real_slm=False,
        slm_provider="not_invoked",
        slm_fallback_reason="Deterministic validation runs before optional model review.",
        memory_used=True,
        created_at=datetime.now(timezone.utc),
        trace_summary=[{
            "phase": "project_validation",
            "title": "Delivery validation updated",
            "detail": "The result is bound to the exact approved scope and does not approve patches, commands, sending, or deployment.",
            "status": "passed",
            "data": {"campaign_id": campaign.campaign_id, "state": campaign.state.value, "run_id": run.run_id if run else None},
        }],
        action=action,
    )


__all__ = ["build_validation_action", "build_validation_chat_run", "public_campaign"]
