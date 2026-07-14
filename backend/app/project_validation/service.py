from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.project_validation.acceptance import evaluate_acceptance_criteria
from backend.app.project_validation.contracts import (
    AcceptanceResult,
    ApprovedScopeReference,
    BudgetUsage,
    FailureCategory,
    HumanReviewAction,
    HumanReviewDecision,
    ReadinessDecision,
    ReliabilityFinding,
    RemediationRequest,
    Stage9ProjectReference,
    ValidationCampaign,
    ValidationCriterion,
    ValidationDeliverable,
    ValidationLimits,
    ValidationRun,
    ValidationScopeSnapshot,
    ValidationState,
    stable_hash,
)
from backend.app.project_validation.inspection import build_deliverable_manifest
from backend.app.project_validation.limits import add_usage, enforce_budget, evaluate_budget
from backend.app.project_validation.quality import assess_quality
from backend.app.project_validation.regression import evaluate_regression
from backend.app.project_validation.store import ProjectValidationStore, ValidationConflictError
from backend.app.project_validation.workflow import transition_state
from backend.app.project_validation.workspace import capture_snapshot, compare_snapshot, prepare_workspace, restore_snapshot


class ProjectValidationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_ACTIVE_STATES = {
    ValidationState.PREPARING_WORKSPACE, ValidationState.RUNNING,
    ValidationState.EVALUATING_ACCEPTANCE, ValidationState.INSPECTING_DELIVERABLES,
    ValidationState.RUNNING_REGRESSION, ValidationState.QUALITY_REVIEW,
    ValidationState.RECOVERING, ValidationState.FAILED,
}


class ProjectValidationService:
    def __init__(self, database_path: str | Path):
        self.store = ProjectValidationStore(database_path)
        self.store.initialize()
        self._lock = threading.RLock()

    def create_campaign(
        self,
        *,
        engagement: dict[str, Any],
        delivery: dict[str, Any],
        conversation_id: str,
        user_id: str,
        authorization_id: str,
        workspace_root: str | Path,
        limits: ValidationLimits | None = None,
        idempotency_key: str | None = None,
    ) -> ValidationCampaign:
        scope = _extract_scope_snapshot(engagement)
        engagement_id = str(engagement.get("engagement_id") or "")
        if not engagement_id:
            raise ProjectValidationError("invalid_engagement", "A persisted Stage 10 engagement is required.")
        if str(engagement.get("conversation_id") or "") != conversation_id:
            raise ProjectValidationError("ownership_mismatch", "The engagement belongs to a different conversation.")
        if str(engagement.get("state") or "") not in {"scope_approved", "project_launched"}:
            raise ProjectValidationError("scope_not_approved", "Validation requires an approved Stage 10 scope.")
        approved_revision_id = str(engagement.get("approved_scope_revision_id") or scope.revision_id)
        if approved_revision_id != scope.revision_id:
            raise ProjectValidationError("stale_scope", "The displayed scope is not the exact approved scope revision.")
        launch = engagement.get("project_launch") or {}
        delivery_job_id = str(delivery.get("delivery_job_id") or launch.get("delivery_job_id") or "")
        if not delivery_job_id:
            raise ProjectValidationError("missing_project", "A Stage 9 project-delivery job is required.")
        if launch and launch.get("delivery_job_id") and str(launch.get("delivery_job_id")) != delivery_job_id:
            raise ProjectValidationError("project_mismatch", "The Stage 9 project does not belong to this approved engagement.")
        if idempotency_key:
            replay = self.store.get_idempotent(f"campaign:{engagement_id}", idempotency_key)
            if replay:
                existing = self.store.get_campaign(str(replay["campaign_id"]))
                if existing:
                    return existing
        now = datetime.now(timezone.utc)
        campaign = ValidationCampaign(
            campaign_id=f"validation-campaign-{uuid4().hex}", conversation_id=conversation_id,
            user_id=user_id, scope_reference=ApprovedScopeReference(
                engagement_id=engagement_id, revision_id=scope.revision_id,
                revision_number=scope.revision_number, scope_hash=scope.scope_hash,
            ),
            project_reference=Stage9ProjectReference(
                delivery_job_id=delivery_job_id,
                plan_revision=int(delivery.get("plan_revision") or delivery.get("current_plan_revision") or 1),
                plan_hash=delivery.get("plan_hash") or _nested(delivery, "plan", "plan_hash"),
                status=str(delivery.get("status") or "unknown"),
            ),
            scope_snapshot=scope, limits=limits or ValidationLimits(),
            state=ValidationState.CREATED, state_version=1,
            created_at=now, updated_at=now,
        )
        # Validate the root before persisting the campaign, without storing file contents.
        prepare_workspace(
            workspace_root, authorization_id=authorization_id,
            conversation_id=conversation_id, copy_workspace=False,
        )
        self.store.create_campaign(campaign)
        self.store.audit(
            campaign_id=campaign.campaign_id, event_type="validation_campaign_created", actor_id=user_id,
            payload={"engagement_id": engagement_id, "delivery_job_id": delivery_job_id, "scope_hash": scope.scope_hash},
        )
        if idempotency_key:
            self.store.save_idempotent(f"campaign:{engagement_id}", idempotency_key, {"campaign_id": campaign.campaign_id})
        return campaign

    def prepare(
        self,
        *, campaign_id: str, expected_version: int, authorization_id: str,
        workspace_root: str | Path, actor_id: str, copy_workspace: bool = False,
    ) -> ValidationCampaign:
        with self._lock:
            campaign = self._campaign(campaign_id)
            self._expect_campaign_version(campaign, expected_version)
            if campaign.state != ValidationState.CREATED:
                raise ProjectValidationError("invalid_state", "This validation campaign has already been prepared.")
            campaign = self._advance_campaign(campaign, ValidationState.PREPARING_WORKSPACE)
            workspace = prepare_workspace(
                workspace_root, authorization_id=authorization_id,
                conversation_id=campaign.conversation_id, copy_workspace=copy_workspace,
            )
            self.store.audit(campaign_id=campaign_id, event_type="workspace_prepared", actor_id=actor_id, payload={
                "workspace_id": workspace.workspace_id, "isolated": workspace.isolated, "display_name": workspace.display_name,
            })
            snapshot = capture_snapshot(workspace, campaign_id=campaign_id, limits=campaign.limits)
            usage = add_usage(BudgetUsage(), snapshot_files=snapshot.file_count, snapshot_bytes=snapshot.total_bytes)
            enforce_budget(campaign.limits, usage)
            campaign = campaign.model_copy(update={"workspace": workspace, "baseline_snapshot": snapshot})
            campaign = self._advance_campaign(campaign, ValidationState.BASELINE_CAPTURED)
            self.store.audit(campaign_id=campaign_id, event_type="baseline_captured", actor_id=actor_id, payload={
                "snapshot_id": snapshot.snapshot_id, "file_count": snapshot.file_count,
                "total_bytes": snapshot.total_bytes, "directory_hash": snapshot.directory_hash,
            })
            campaign = self._advance_campaign(campaign, ValidationState.READY)
            return campaign

    def start_run(
        self, *, campaign_id: str, expected_version: int, actor_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[ValidationCampaign, ValidationRun]:
        with self._lock:
            campaign = self._campaign(campaign_id)
            self._expect_campaign_version(campaign, expected_version)
            if campaign.state not in {ValidationState.READY, ValidationState.REMEDIATION_REQUIRED}:
                raise ProjectValidationError("invalid_state", "The campaign is not ready to begin a validation run.")
            if not campaign.workspace or not campaign.baseline_snapshot:
                raise ProjectValidationError("workspace_missing", "Prepare the authorized workspace and baseline first.")
            if idempotency_key:
                replay = self.store.get_idempotent(f"run:{campaign_id}", idempotency_key)
                if replay:
                    run = self.store.get_run(str(replay["run_id"]))
                    current = self._campaign(campaign_id)
                    if run:
                        return current, run
            runs = self.store.list_runs(campaign_id)
            if len(runs) >= campaign.limits.max_runs_per_campaign:
                raise ProjectValidationError("run_limit", "The campaign reached its configured validation-run limit.")
            now = datetime.now(timezone.utc)
            run = ValidationRun(
                run_id=f"validation-run-{uuid4().hex}", campaign_id=campaign_id,
                run_number=len(runs) + 1, state=ValidationState.RUNNING, state_version=1,
                scope_reference=campaign.scope_reference, project_reference=campaign.project_reference,
                baseline_snapshot_id=campaign.baseline_snapshot.snapshot_id,
                budget_usage=BudgetUsage(snapshot_files=campaign.baseline_snapshot.file_count, snapshot_bytes=campaign.baseline_snapshot.total_bytes),
                started_at=now, updated_at=now,
            )
            self.store.create_run(run)
            updated_campaign = campaign.model_copy(update={
                "run_ids": [*campaign.run_ids, run.run_id], "active_run_id": run.run_id,
            })
            updated_campaign = self._advance_campaign(updated_campaign, ValidationState.RUNNING)
            self.store.audit(campaign_id=campaign_id, run_id=run.run_id, event_type="validation_started", actor_id=actor_id, payload={"run_number": run.run_number})
            if idempotency_key:
                self.store.save_idempotent(f"run:{campaign_id}", idempotency_key, {"run_id": run.run_id})
            return updated_campaign, run

    def evaluate_run(
        self,
        *, campaign_id: str, run_id: str, expected_campaign_version: int,
        expected_run_version: int, actor_id: str,
        evidence_by_criterion: dict[str, list[dict[str, Any]]] | None = None,
        artifact_hints: dict[str, str] | None = None,
        allowed_paths: list[str] | None = None,
        regressed_tests: list[str] | None = None,
        current_delivery: dict[str, Any] | None = None,
        current_engagement: dict[str, Any] | None = None,
    ) -> tuple[ValidationCampaign, ValidationRun]:
        with self._lock:
            campaign = self._campaign(campaign_id)
            run = self._run(run_id, campaign_id)
            self._expect_campaign_version(campaign, expected_campaign_version)
            self._expect_run_version(run, expected_run_version)
            if campaign.active_run_id != run_id or campaign.state != ValidationState.RUNNING or run.state != ValidationState.RUNNING:
                raise ProjectValidationError("invalid_state", "Only the active running validation can be evaluated.")
            if not campaign.workspace or not campaign.baseline_snapshot:
                raise ProjectValidationError("workspace_missing", "The campaign baseline is unavailable.")
            if current_engagement is None:
                raise ProjectValidationError("engagement_state_missing", "Current Stage 10 engagement state is required for validation.")
            _verify_current_scope(campaign, current_engagement)
            if current_delivery is None:
                raise ProjectValidationError("project_state_missing", "Current Stage 9 project state is required for validation.")
            if str(current_delivery.get("delivery_job_id") or "") != campaign.project_reference.delivery_job_id:
                raise ProjectValidationError("project_mismatch", "The current Stage 9 project does not match this campaign.")
            current_status = str(current_delivery.get("status") or "")
            if current_status not in {"delivery_completed", "awaiting_manual_verification"}:
                raise ProjectValidationError("delivery_incomplete", "Stage 9 execution must finish before delivery validation.")
            current_plan_hash = current_delivery.get("plan_hash") or _nested(current_delivery, "plan", "plan_hash")
            if campaign.project_reference.plan_hash and current_plan_hash != campaign.project_reference.plan_hash:
                raise ProjectValidationError("stale_project_plan", "The Stage 9 plan changed after this validation campaign was created.")
            linked = current_delivery.get("client_engagement") or {}
            if linked and (
                str(linked.get("engagement_id") or "") != campaign.scope_reference.engagement_id
                or str(linked.get("scope_revision_id") or "") != campaign.scope_reference.revision_id
                or str(linked.get("scope_hash") or "") != campaign.scope_reference.scope_hash
            ):
                raise ProjectValidationError("scope_mismatch", "The Stage 9 project is linked to a different approved scope.")
            snapshot_diff = compare_snapshot(campaign.baseline_snapshot, campaign.workspace, campaign.limits)
            usage = add_usage(
                run.budget_usage,
                generated_files=len(snapshot_diff["created"]), modified_files=len(snapshot_diff["modified"]),
                deleted_files=len(snapshot_diff["deleted"]), total_changed_bytes=int(snapshot_diff["changed_bytes"]),
                evidence_items=sum(len(value) for value in (evidence_by_criterion or {}).values()),
            )
            budget = evaluate_budget(campaign.limits, usage)
            if budget.exceeded:
                run = run.model_copy(update={"budget_usage": usage})
                run = self._advance_run(run, ValidationState.BUDGET_EXCEEDED)
                campaign = self._advance_campaign(campaign, ValidationState.BUDGET_EXCEEDED)
                self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type="budget_exceeded", actor_id=actor_id, payload={"limits": list(budget.exceeded)})
                return campaign, run
            if budget.warnings:
                self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type="budget_warning_generated", actor_id=actor_id, payload={"limits": list(budget.warnings), "remaining": budget.remaining})
            run = run.model_copy(update={"budget_usage": usage})
            run = self._advance_run(run, ValidationState.EVALUATING_ACCEPTANCE)
            campaign = self._advance_campaign(campaign, ValidationState.EVALUATING_ACCEPTANCE)
            criteria = [
                criterion.model_dump(mode="json")
                for deliverable in campaign.scope_snapshot.deliverables
                for criterion in deliverable.acceptance_criteria
            ]
            evaluations = evaluate_acceptance_criteria(criteria, evidence_by_criterion or {})
            self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type="acceptance_evaluation_completed", actor_id=actor_id, payload={
                "criterion_count": len(evaluations), "failed": sum(item.blocking for item in evaluations),
                "human_review": sum(item.human_review_required for item in evaluations),
            })
            run = run.model_copy(update={"acceptance_evaluations": evaluations})
            run = self._advance_run(run, ValidationState.INSPECTING_DELIVERABLES)
            campaign = self._advance_campaign(campaign, ValidationState.INSPECTING_DELIVERABLES)
            manifest = build_deliverable_manifest(
                run_id=run_id, workspace_root=campaign.workspace.validation_root,
                deliverables=[item.model_dump(mode="json") for item in campaign.scope_snapshot.deliverables],
                artifact_hints=artifact_hints,
            )
            run = run.model_copy(update={"deliverable_manifest": manifest})
            self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type="deliverable_manifest_generated", actor_id=actor_id, payload={
                "complete": manifest.complete, "missing": manifest.missing_deliverable_ids,
            })
            run = self._advance_run(run, ValidationState.RUNNING_REGRESSION)
            campaign = self._advance_campaign(campaign, ValidationState.RUNNING_REGRESSION)
            regression = evaluate_regression(
                run_id=run_id, snapshot_diff=snapshot_diff, allowed_paths=allowed_paths, regressed_tests=regressed_tests,
            )
            run = run.model_copy(update={"regression_result": regression})
            if regression.blocking:
                self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type="regression_detected", actor_id=actor_id, payload={"summary": regression.summary})
            run = self._advance_run(run, ValidationState.QUALITY_REVIEW)
            campaign = self._advance_campaign(campaign, ValidationState.QUALITY_REVIEW)
            quality = assess_quality(run_id=run_id, evaluations=evaluations, manifest=manifest, regression=regression)
            findings = _findings(evaluations, manifest.missing_deliverable_ids, regression)
            result_payload = {
                "scope_hash": campaign.scope_reference.scope_hash,
                "evaluations": [item.model_dump(mode="json") for item in evaluations],
                "manifest_hash": manifest.manifest_hash,
                "regression": regression.model_dump(mode="json"),
                "quality_hash": quality.assessment_hash,
                "findings": [item.model_dump(mode="json") for item in findings],
            }
            result_hash = stable_hash(result_payload)
            final_state = ValidationState.REMEDIATION_REQUIRED if quality.automated_decision == ReadinessDecision.REMEDIATION_REQUIRED else ValidationState.AWAITING_HUMAN_REVIEW
            run = run.model_copy(update={
                "quality_assessment": quality, "findings": findings,
                "automated_decision": quality.automated_decision, "result_hash": result_hash,
                "completed_at": datetime.now(timezone.utc),
            })
            run = self._advance_run(run, final_state)
            campaign = self._advance_campaign(campaign, final_state)
            self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type="delivery_readiness_calculated", actor_id=actor_id, payload={
                "automated_decision": quality.automated_decision.value, "overall_score": quality.overall_score,
                "blocking_findings": len(quality.blocking_findings), "result_hash": result_hash,
            })
            return campaign, run

    def human_review(
        self,
        *, campaign_id: str, run_id: str, expected_campaign_version: int,
        expected_run_version: int, scope_revision_id: str, scope_hash: str,
        validation_result_hash: str, reviewer_id: str, action: HumanReviewAction | str,
        notes: str = "", current_engagement: dict[str, Any] | None = None,
    ) -> tuple[ValidationCampaign, ValidationRun, HumanReviewDecision]:
        with self._lock:
            campaign = self._campaign(campaign_id)
            run = self._run(run_id, campaign_id)
            self._expect_campaign_version(campaign, expected_campaign_version)
            self._expect_run_version(run, expected_run_version)
            if campaign.state != ValidationState.AWAITING_HUMAN_REVIEW or run.state != ValidationState.AWAITING_HUMAN_REVIEW:
                raise ProjectValidationError("invalid_state", "This run is not awaiting human delivery review.")
            if current_engagement is None:
                raise ProjectValidationError("engagement_state_missing", "Current Stage 10 engagement state is required for delivery review.")
            _verify_current_scope(campaign, current_engagement)
            if scope_revision_id != campaign.scope_reference.revision_id or scope_hash != campaign.scope_reference.scope_hash:
                raise ProjectValidationError("stale_scope", "The human review does not reference the exact approved scope.")
            if not run.result_hash or validation_result_hash != run.result_hash:
                raise ProjectValidationError("stale_validation", "The validation result changed after this review was prepared.")
            selected = HumanReviewAction(action)
            if selected in {HumanReviewAction.APPROVE, HumanReviewAction.APPROVE_WITH_NOTES}:
                if run.quality_assessment is None or run.quality_assessment.blocking_findings:
                    raise ProjectValidationError("blocking_findings", "Blocking findings must be remediated before delivery approval.")
                target = ValidationState.DELIVERY_READY
            elif selected == HumanReviewAction.REJECT:
                target = ValidationState.DELIVERY_REJECTED
            elif selected in {HumanReviewAction.REQUEST_REMEDIATION, HumanReviewAction.REQUEST_SCOPE_CHANGE}:
                target = ValidationState.REMEDIATION_REQUIRED
            else:
                target = ValidationState.CANCELLED
            review = HumanReviewDecision(
                review_id=f"human-review-{uuid4().hex}", campaign_id=campaign_id, run_id=run_id,
                scope_revision_id=scope_revision_id, scope_hash=scope_hash,
                validation_result_hash=validation_result_hash, reviewer_id=reviewer_id,
                action=selected, notes=notes, reviewed_at=datetime.now(timezone.utc),
            )
            self.store.save_review(review)
            run = run.model_copy(update={"human_review": review})
            run = self._advance_run(run, target)
            campaign = self._advance_campaign(campaign, target)
            event = "human_delivery_approved" if target == ValidationState.DELIVERY_READY else "human_delivery_rejected" if target == ValidationState.DELIVERY_REJECTED else "remediation_requested"
            self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type=event, actor_id=reviewer_id, payload={"action": selected.value, "notes": notes})
            return campaign, run, review

    def pause(
        self, *, campaign_id: str, run_id: str, expected_campaign_version: int,
        expected_run_version: int, actor_id: str,
    ) -> tuple[ValidationCampaign, ValidationRun]:
        with self._lock:
            campaign = self._campaign(campaign_id)
            run = self._run(run_id, campaign_id)
            self._expect_campaign_version(campaign, expected_campaign_version)
            self._expect_run_version(run, expected_run_version)
            if campaign.active_run_id != run_id:
                raise ProjectValidationError("invalid_state", "Only the active validation run can be paused.")
            if campaign.state not in {ValidationState.RUNNING, ValidationState.BUDGET_EXCEEDED} or run.state not in {ValidationState.RUNNING, ValidationState.BUDGET_EXCEEDED}:
                raise ProjectValidationError("invalid_state", "This validation run cannot be paused from its current state.")
            run = self._advance_run(run, ValidationState.EXECUTION_PAUSED)
            campaign = self._advance_campaign(campaign, ValidationState.EXECUTION_PAUSED)
            self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type="validation_paused", actor_id=actor_id, payload={"reason": "explicit_user_pause"})
            return campaign, run

    def resume(
        self, *, campaign_id: str, run_id: str, expected_campaign_version: int,
        expected_run_version: int, actor_id: str, current_engagement: dict[str, Any],
        current_delivery: dict[str, Any],
    ) -> tuple[ValidationCampaign, ValidationRun]:
        with self._lock:
            campaign = self._campaign(campaign_id)
            run = self._run(run_id, campaign_id)
            self._expect_campaign_version(campaign, expected_campaign_version)
            self._expect_run_version(run, expected_run_version)
            if campaign.active_run_id != run_id or campaign.state != ValidationState.EXECUTION_PAUSED or run.state != ValidationState.EXECUTION_PAUSED:
                raise ProjectValidationError("invalid_state", "This validation run is not paused.")
            _verify_current_scope(campaign, current_engagement)
            if str(current_delivery.get("delivery_job_id") or "") != campaign.project_reference.delivery_job_id:
                raise ProjectValidationError("project_mismatch", "The Stage 9 project no longer matches this validation campaign.")
            run = self._advance_run(run, ValidationState.RUNNING)
            campaign = self._advance_campaign(campaign, ValidationState.RUNNING)
            self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type="validation_resumed", actor_id=actor_id, payload={"result": "authorization_revalidated"})
            return campaign, run

    def cancel(
        self, *, campaign_id: str, expected_campaign_version: int, actor_id: str,
        reason: str = "Cancelled by the user.", expected_run_version: int | None = None,
    ) -> tuple[ValidationCampaign, ValidationRun | None]:
        with self._lock:
            campaign = self._campaign(campaign_id)
            self._expect_campaign_version(campaign, expected_campaign_version)
            if campaign.state in {ValidationState.DELIVERY_READY, ValidationState.CANCELLED}:
                raise ProjectValidationError("invalid_state", "This validation campaign is already final.")
            run = self._run(campaign.active_run_id, campaign_id) if campaign.active_run_id else None
            if run and run.state not in {ValidationState.DELIVERY_READY, ValidationState.CANCELLED}:
                if expected_run_version is None:
                    raise ProjectValidationError("invalid_request", "The active run version is required to cancel safely.")
                self._expect_run_version(run, expected_run_version)
                run = self._advance_run(run, ValidationState.CANCELLED)
            campaign = campaign.model_copy(update={"cancelled_reason": reason[:2000]})
            campaign = self._advance_campaign(campaign, ValidationState.CANCELLED)
            self.store.audit(campaign_id=campaign_id, run_id=campaign.active_run_id, event_type="validation_cancelled", actor_id=actor_id, payload={"reason": reason[:500]})
            return campaign, run

    def restore_baseline(
        self, *, campaign_id: str, expected_campaign_version: int, actor_id: str,
    ) -> tuple[ValidationCampaign, dict[str, list[str] | bool]]:
        with self._lock:
            campaign = self._campaign(campaign_id)
            self._expect_campaign_version(campaign, expected_campaign_version)
            if not campaign.workspace or not campaign.baseline_snapshot:
                raise ProjectValidationError("snapshot_missing", "The campaign baseline is unavailable.")
            if campaign.state not in {ValidationState.REMEDIATION_REQUIRED, ValidationState.EXECUTION_PAUSED, ValidationState.FAILED, ValidationState.DELIVERY_REJECTED}:
                raise ProjectValidationError("invalid_state", "Baseline restoration is allowed only after a paused, failed, rejected, or remediation-required validation.")
            try:
                result = restore_snapshot(campaign.baseline_snapshot, campaign.workspace)
            except (OSError, ValueError) as error:
                self.store.audit(campaign_id=campaign_id, run_id=campaign.active_run_id, event_type="snapshot_restoration_failed", actor_id=actor_id, payload={"error": str(error)[:500]})
                raise ProjectValidationError("restore_failed", "The baseline could not be restored completely.") from error
            event = "snapshot_restoration_completed" if result["complete"] else "snapshot_restoration_partial"
            self.store.audit(campaign_id=campaign_id, run_id=campaign.active_run_id, event_type=event, actor_id=actor_id, payload={"restored": len(result["restored"]), "removed": len(result["removed"]), "failed": result["failed"]})
            return campaign, result

    def request_remediation(
        self, *, campaign_id: str, run_id: str, requested_by: str, route: str,
    ) -> RemediationRequest:
        run = self._run(run_id, campaign_id)
        if not run.findings:
            raise ProjectValidationError("no_findings", "No validation findings are available for remediation.")
        request = RemediationRequest(
            remediation_id=f"remediation-{uuid4().hex}", campaign_id=campaign_id, run_id=run_id,
            findings=run.findings, requested_route=route, requested_by=requested_by,
            requested_at=datetime.now(timezone.utc),
        )
        self.store.audit(campaign_id=campaign_id, run_id=run_id, event_type="remediation_requested", actor_id=requested_by, payload={"route": route, "finding_count": len(run.findings)})
        return request

    def recover(self, *, campaign_id: str, actor_id: str) -> ValidationCampaign:
        with self._lock:
            campaign = self._campaign(campaign_id)
            if campaign.state not in _ACTIVE_STATES:
                return campaign
            campaign = self._advance_campaign(campaign, ValidationState.RECOVERING)
            active_run = None
            if campaign.active_run_id:
                active_run = self._run(campaign.active_run_id, campaign_id)
                if active_run.state in _ACTIVE_STATES:
                    active_run = self._advance_run(active_run, ValidationState.RECOVERING)
                    active_run = self._advance_run(active_run, ValidationState.EXECUTION_PAUSED)
            if active_run is not None:
                target = ValidationState.EXECUTION_PAUSED
                result = "paused_for_reconciliation"
            elif campaign.baseline_snapshot is not None:
                target = ValidationState.READY
                result = "baseline_available_ready_to_retry"
            else:
                target = ValidationState.FAILED
                result = "baseline_unavailable_manual_restart_required"
            campaign = self._advance_campaign(campaign, target)
            self.store.audit(campaign_id=campaign_id, run_id=campaign.active_run_id, event_type="recovery_completed", actor_id=actor_id, payload={"result": result})
            return campaign

    def get_campaign(self, campaign_id: str, *, conversation_id: str | None = None) -> ValidationCampaign:
        campaign = self._campaign(campaign_id)
        if conversation_id is not None and campaign.conversation_id != conversation_id:
            raise ProjectValidationError("ownership_mismatch", "The validation campaign belongs to a different conversation.")
        return campaign

    def get_run(self, campaign_id: str, run_id: str) -> ValidationRun:
        return self._run(run_id, campaign_id)

    def _campaign(self, campaign_id: str) -> ValidationCampaign:
        campaign = self.store.get_campaign(campaign_id)
        if not campaign:
            raise ProjectValidationError("not_found", "Validation campaign not found.")
        return campaign

    def _run(self, run_id: str, campaign_id: str) -> ValidationRun:
        run = self.store.get_run(run_id)
        if not run or run.campaign_id != campaign_id:
            raise ProjectValidationError("not_found", "Validation run not found.")
        return run

    @staticmethod
    def _expect_campaign_version(campaign: ValidationCampaign, expected: int) -> None:
        if campaign.state_version != expected:
            raise ProjectValidationError("conflict", "The validation campaign changed before this action completed.")

    @staticmethod
    def _expect_run_version(run: ValidationRun, expected: int) -> None:
        if run.state_version != expected:
            raise ProjectValidationError("conflict", "The validation run changed before this action completed.")

    def _advance_campaign(self, campaign: ValidationCampaign, target: ValidationState) -> ValidationCampaign:
        target = transition_state(campaign.state, target)
        current_version = campaign.state_version
        updated = campaign.model_copy(update={
            "state": target, "state_version": current_version + 1, "updated_at": datetime.now(timezone.utc),
        })
        try:
            self.store.save_campaign(updated, expected_version=current_version)
        except ValidationConflictError as error:
            raise ProjectValidationError("conflict", str(error)) from error
        return updated

    def _advance_run(self, run: ValidationRun, target: ValidationState) -> ValidationRun:
        target = transition_state(run.state, target)
        current_version = run.state_version
        updated = run.model_copy(update={
            "state": target, "state_version": current_version + 1, "updated_at": datetime.now(timezone.utc),
        })
        try:
            self.store.save_run(updated, expected_version=current_version)
        except ValidationConflictError as error:
            raise ProjectValidationError("conflict", str(error)) from error
        return updated


def _verify_current_scope(campaign: ValidationCampaign, engagement: dict[str, Any]) -> None:
    if str(engagement.get("engagement_id") or "") != campaign.scope_reference.engagement_id:
        raise ProjectValidationError("engagement_mismatch", "The current engagement does not match this validation campaign.")
    if str(engagement.get("state") or "") not in {"scope_approved", "project_launched"}:
        raise ProjectValidationError("scope_invalidated", "The approved client scope is no longer active.")
    current = _extract_scope_snapshot(engagement)
    approved_revision = str(engagement.get("approved_scope_revision_id") or current.revision_id)
    if approved_revision != campaign.scope_reference.revision_id or current.revision_id != campaign.scope_reference.revision_id or current.scope_hash != campaign.scope_reference.scope_hash:
        raise ProjectValidationError("scope_invalidated", "The approved Stage 10 scope changed after this validation campaign was created.")


def _extract_scope_snapshot(engagement: dict[str, Any]) -> ValidationScopeSnapshot:
    revision = engagement.get("current_scope_revision") or engagement.get("current_scope") or {}
    scope = revision.get("scope") or {}
    canonical_scope = str(revision.get("canonical_scope") or "")
    scope_hash = str(revision.get("scope_hash") or "")
    if not canonical_scope or len(scope_hash) != 64:
        raise ProjectValidationError("invalid_scope", "The Stage 10 scope revision is incomplete.")
    if hashlib.sha256(canonical_scope.encode("utf-8")).hexdigest() != scope_hash:
        raise ProjectValidationError("tampered_scope", "The approved Stage 10 scope hash does not match its canonical scope.")
    normalized_scope = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if normalized_scope != canonical_scope:
        raise ProjectValidationError("tampered_scope", "The displayed Stage 10 scope no longer matches its immutable canonical revision.")
    deliverables: list[ValidationDeliverable] = []
    for deliverable in scope.get("deliverables", []):
        criteria = [ValidationCriterion(
            criterion_id=str(item.get("criterion_id") or ""),
            statement=str(item.get("statement") or ""),
            required=True, review_mode=str(item.get("review_mode") or "automated"),
        ) for item in deliverable.get("acceptance_criteria", [])]
        deliverables.append(ValidationDeliverable(
            deliverable_id=str(deliverable.get("deliverable_id") or ""),
            title=str(deliverable.get("title") or ""), description=str(deliverable.get("description") or ""),
            acceptance_criteria=criteria,
        ))
    return ValidationScopeSnapshot(
        revision_id=str(revision.get("revision_id") or ""), revision_number=int(revision.get("revision_number") or 1),
        scope_hash=scope_hash, objective=str(scope.get("desired_outcome") or scope.get("problem_statement") or "Approved client outcome"),
        deliverables=deliverables, exclusions=[str(item.get("text") or item) for item in scope.get("exclusions", [])],
        canonical_scope=canonical_scope,
    )


def _findings(evaluations, missing_deliverables: list[str], regression) -> list[ReliabilityFinding]:
    findings: list[ReliabilityFinding] = []
    for item in evaluations:
        if not item.blocking:
            continue
        category = FailureCategory.UNMET_REQUIREMENT
        route = "stage8_repair" if item.result == AcceptanceResult.FAILED else "stage9_replan"
        findings.append(ReliabilityFinding(
            finding_id=f"finding-{uuid4().hex}", category=category, severity="error",
            summary=item.failure_explanation or f"Acceptance criterion was not satisfied: {item.criterion_text}",
            evidence_ids=[evidence.evidence_id for evidence in item.evidence], blocking=True, recommended_route=route,
        ))
    for deliverable_id in missing_deliverables:
        findings.append(ReliabilityFinding(
            finding_id=f"finding-{uuid4().hex}", category=FailureCategory.MISSING_DELIVERABLE,
            severity="error", summary=f"Approved deliverable was not found: {deliverable_id}",
            blocking=True, recommended_route="stage9_replan",
        ))
    if regression.blocking:
        findings.append(ReliabilityFinding(
            finding_id=f"finding-{uuid4().hex}", category=FailureCategory.REGRESSION,
            severity="critical" if regression.tests_regressed else "error", summary=regression.summary,
            blocking=True, recommended_route="stage8_repair",
        ))
    return findings


def _nested(value: dict[str, Any], *path: str) -> Any:
    current: Any = value
    for item in path:
        if not isinstance(current, dict):
            return None
        current = current.get(item)
    return current


__all__ = ["ProjectValidationError", "ProjectValidationService"]
