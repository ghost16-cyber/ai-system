from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from uuid import uuid4

from backend.app.client_engagement.contracts import (
    ENGAGEMENT_SCHEMA_VERSION,
    ApprovalStatus,
    Assumption,
    ClarificationAnswer,
    ClarificationQuestion,
    ClientEngagementRequest,
    ClientIdentityMetadata,
    CreatorType,
    EngagementPublicResponse,
    EngagementState,
    EvidenceSourceType,
    ProjectLaunchResult,
    ScopeApproval,
    ScopeChangeRequest,
    ScopeRejection,
    ScopeRevision,
)
from backend.app.client_engagement.evidence import collect_authorized_evidence, public_evidence
from backend.app.client_engagement.extraction import extract_requirements, generate_clarification_questions, reasonable_assumption_for
from backend.app.client_engagement.limits import EngagementLimits, STAGE10_LIMITS
from backend.app.client_engagement.scoping import build_scope_revision, scope_change_impact, verify_scope_revision
from backend.app.client_engagement.workflow import EngagementTransitionError, transition_state


class EngagementError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_transition") -> None:
        super().__init__(message)
        self.code = code


class EngagementService:
    def __init__(self, repository: Any, *, model_gateway: Any | None = None, limits: EngagementLimits = STAGE10_LIMITS) -> None:
        self.repository = repository
        self.model_gateway = model_gateway
        self.limits = limits

    def create(
        self, *, conversation_id: str, original_request: str, user_id: str,
        display_name: str | None = None, organization: str | None = None,
        folder_root: str | None = None, folder_access_id: str | None = None,
        structural_summary: dict[str, Any] | None = None,
        project_metadata: dict[str, Any] | None = None,
        uploaded_documents: Iterable[dict[str, Any]] = (),
        constraints: Iterable[str] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        original_request = original_request.strip()
        if not original_request:
            raise EngagementError("A client request is required.", code="invalid_request")
        create_scope = f"new:{conversation_id}"
        if idempotency_key:
            existing = self.repository.get_client_engagement_idempotency(create_scope, "create", idempotency_key)
            if existing:
                return self.repository.get_client_engagement(str(existing["engagement_id"]))
        now = _now()
        engagement_id = uuid4().hex
        request = ClientEngagementRequest(
            schema_version=ENGAGEMENT_SCHEMA_VERSION, engagement_id=engagement_id,
            conversation_id=conversation_id, original_request=original_request,
            client=ClientIdentityMetadata(schema_version=ENGAGEMENT_SCHEMA_VERSION, user_id=user_id, display_name=display_name, organization=organization),
            folder_access_id=folder_access_id, constraints=list(constraints)[:30], created_at=now,
        )
        engagement: dict[str, Any] = {
            "schema_version": ENGAGEMENT_SCHEMA_VERSION,
            "engagement_id": engagement_id, "conversation_id": conversation_id,
            "folder_access_id": folder_access_id, "request": request.model_dump(mode="json"),
            "state": EngagementState.DRAFT.value, "state_version": 1,
            "evidence": [], "requirements": [], "questions": [], "answers": [],
            "assumptions": [], "scope_revisions": [], "current_scope": None,
            "approvals": [], "rejections": [], "scope_changes": [], "project_launch": None,
            "launch_attempts": 0, "clarification_rounds": 0, "model_fallback_used": False,
            "limitation": None, "last_error": None, "recovery": {"recovered_count": 0, "last_recovered_at": None},
            "created_at": now, "updated_at": now, "cancelled_at": None,
        }
        self.repository.store_client_engagement(engagement)
        self._audit(engagement, "engagement_created", "created", {"has_authorized_folder": bool(folder_access_id)})
        if idempotency_key:
            self.repository.store_client_engagement_idempotency(create_scope, "create", idempotency_key, {"engagement_id": engagement_id}, now)
        return self.analyze(
            engagement_id=engagement_id, expected_version=1,
            folder_root=folder_root, structural_summary=structural_summary,
            project_metadata=project_metadata, uploaded_documents=uploaded_documents,
            idempotency_key=f"initial:{idempotency_key or engagement_id}",
        )

    def analyze(
        self, *, engagement_id: str, expected_version: int,
        folder_root: str | None = None, structural_summary: dict[str, Any] | None = None,
        project_metadata: dict[str, Any] | None = None,
        uploaded_documents: Iterable[dict[str, Any]] = (),
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        replay = self._replay(engagement_id, "analyze", idempotency_key)
        if replay: return replay
        current = self._current(engagement_id, expected_version)
        if current["state"] not in {EngagementState.DRAFT.value, EngagementState.FAILED.value}:
            raise EngagementError("This engagement is not ready for evidence analysis.", code="invalid_state")
        updated = _copy(current)
        try:
            self._move(updated, EngagementState.COLLECTING_EVIDENCE)
            self._audit(updated, "evidence_authorized", "authorized", {"folder_access_id": updated.get("folder_access_id")})
            request = ClientEngagementRequest.model_validate(updated["request"])
            evidence = collect_authorized_evidence(
                engagement_id=engagement_id, conversation_id=updated["conversation_id"],
                original_request=request.original_request, folder_root=folder_root,
                folder_access_id=updated.get("folder_access_id"), structural_summary=structural_summary,
                project_metadata=project_metadata, uploaded_documents=uploaded_documents,
                user_constraints=request.constraints, limits=self.limits,
            )
            updated["evidence"] = [item.model_dump(mode="json") for item in evidence]
            if len(evidence) >= self.limits.max_evidence_items:
                updated["limitation"] = "The authorized evidence reached the configured item limit. Review or narrow the evidence before relying on omitted material."
            self._audit(updated, "evidence_collected", "completed", {"evidence_count": len(evidence)})
            self._move(updated, EngagementState.EXTRACTING_REQUIREMENTS)
            requirements, model_assumptions, extraction_audit = extract_requirements(evidence, model_gateway=self.model_gateway, limits=self.limits)
            updated["requirements"] = [item.model_dump(mode="json") for item in requirements]
            if len(requirements) >= self.limits.max_requirements:
                updated["limitation"] = "Requirement extraction reached the configured limit. No critical requirement should be assumed to be included beyond this bound."
            updated["model_fallback_used"] = extraction_audit["model_invoked"]
            if extraction_audit["model_invoked"]:
                self._audit(updated, "model_fallback_invoked", "completed" if not extraction_audit["model_rejected"] else "rejected", {})
            if extraction_audit["model_rejected"]:
                self._audit(updated, "model_output_rejected", "rejected", {"reason": "strict_contract_or_evidence_validation"})
            for text in model_assumptions:
                updated["assumptions"].append(_assumption_from_model(engagement_id, text).model_dump(mode="json"))
            self._audit(updated, "requirement_extraction_completed", "completed", {"requirement_count": len(requirements)})
            questions = generate_clarification_questions(
                engagement_id=engagement_id, requirements=requirements, evidence=evidence,
                round_number=1, limits=self.limits,
            )
            updated["questions"] = [item.model_dump(mode="json") for item in questions]
            updated["clarification_rounds"] = 1 if questions else 0
            if questions:
                self._audit(updated, "clarification_requested", "pending", {"question_ids": [item.question_id for item in questions]})
            if any(item.blocking for item in questions):
                self._move(updated, EngagementState.CLARIFICATION_REQUIRED)
            else:
                self._prepare_scope(updated, reason="Initial evidence-grounded scope")
        except Exception as error:
            if isinstance(error, EngagementError): raise
            updated["state"] = EngagementState.FAILED.value
            updated["last_error"] = {"code": "analysis_failed", "message": _controlled(error), "at": _now()}
            self._audit(updated, "failure_recorded", "failed", {"code": "analysis_failed"})
        stored = self._store_transition(current, updated)
        self._remember(stored, "analyze", idempotency_key)
        return stored

    def submit_answers(
        self, *, engagement_id: str, expected_version: int, answers: dict[str, str] | None,
        answered_by: str, use_reasonable_assumptions: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        replay = self._replay(engagement_id, "answer", idempotency_key)
        if replay: return replay
        current = self._current(engagement_id, expected_version)
        if current["state"] != EngagementState.CLARIFICATION_REQUIRED.value:
            raise EngagementError("This engagement is not awaiting clarification answers.", code="invalid_state")
        updated = _copy(current)
        supplied = answers or {}
        pending = [ClarificationQuestion.model_validate(item) for item in updated["questions"] if item.get("status") == "pending"]
        unknown = set(supplied) - {item.question_id for item in pending}
        if unknown:
            raise EngagementError("A clarification answer referenced a question outside this engagement or round.", code="ownership_mismatch")
        if not supplied and not use_reasonable_assumptions:
            raise EngagementError("Provide at least one answer or explicitly accept reasonable assumptions.", code="invalid_request")
        now = _now()
        new_answers: list[ClarificationAnswer] = []
        question_payloads = [dict(item) for item in updated["questions"]]
        for question in pending:
            text = str(supplied.get(question.question_id) or "").strip()
            if text:
                answer = ClarificationAnswer(
                    schema_version=ENGAGEMENT_SCHEMA_VERSION, answer_id=uuid4().hex,
                    question_id=question.question_id, engagement_id=engagement_id,
                    answer=text, use_reasonable_assumption=False, answered_by=answered_by,
                    created_at=now,
                )
                new_answers.append(answer)
                _set_question_status(question_payloads, question.question_id, "answered")
            elif use_reasonable_assumptions:
                assumption = reasonable_assumption_for(question)
                updated["assumptions"].append(assumption.model_dump(mode="json"))
                _set_question_status(question_payloads, question.question_id, "assumption_accepted")
                self._audit(updated, "assumption_accepted", "accepted", {"assumption_id": assumption.assumption_id, "question_id": question.question_id})
        updated["questions"] = question_payloads
        updated["answers"] = [*updated["answers"], *[item.model_dump(mode="json") for item in new_answers]]
        for answer in new_answers:
            self._audit(updated, "clarification_answered", "answered", {"question_id": answer.question_id, "answer_id": answer.answer_id})
        if new_answers:
            request = ClientEngagementRequest.model_validate(updated["request"])
            new_evidence = collect_authorized_evidence(
                engagement_id=engagement_id, conversation_id=updated["conversation_id"],
                original_request=request.original_request, clarification_answers=updated["answers"],
                user_constraints=request.constraints, limits=self.limits,
            )
            known = {item["evidence_id"] for item in updated["evidence"]}
            updated["evidence"].extend(item.model_dump(mode="json") for item in new_evidence if item.evidence_id not in known)
            requirements, _, _ = extract_requirements(updated["evidence"], model_gateway=None, limits=self.limits)
            updated["requirements"] = [item.model_dump(mode="json") for item in requirements]
        still_pending = [item for item in updated["questions"] if item.get("status") == "pending"]
        blocking = [item for item in still_pending if item.get("blocking")]
        if blocking:
            updated["updated_at"] = now
        else:
            self._prepare_scope(updated, reason="Scope prepared after clarification")
        stored = self._store_transition(current, updated)
        self._remember(stored, "answer", idempotency_key)
        return stored

    def generate_scope(self, *, engagement_id: str, expected_version: int, reason: str = "Scope regenerated", idempotency_key: str | None = None) -> dict[str, Any]:
        replay = self._replay(engagement_id, "scope", idempotency_key)
        if replay: return replay
        current = self._current(engagement_id, expected_version)
        if current["state"] not in {EngagementState.CLARIFICATION_REQUIRED.value, EngagementState.SCOPE_PREPARING.value, EngagementState.AWAITING_SCOPE_APPROVAL.value}:
            raise EngagementError("A scope cannot be generated from the current state.", code="invalid_state")
        if any(item.get("status") == "pending" and item.get("blocking") for item in current["questions"]):
            raise EngagementError("Blocking clarification questions must be answered or explicitly converted to assumptions.", code="clarification_required")
        if len(current["scope_revisions"]) >= self.limits.max_scope_revisions:
            raise EngagementError("The configured scope-revision limit was reached.", code="limit_reached")
        updated = _copy(current)
        if updated["state"] == EngagementState.AWAITING_SCOPE_APPROVAL.value:
            self._move(updated, EngagementState.SCOPE_PREPARING)
        self._prepare_scope(updated, reason=reason)
        stored = self._store_transition(current, updated)
        self._remember(stored, "scope", idempotency_key)
        return stored

    def approve_scope(
        self, *, engagement_id: str, expected_version: int, revision_id: str,
        scope_hash: str, approving_user: str, idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        replay = self._replay(engagement_id, "approve", idempotency_key)
        if replay: return replay
        current = self._current(engagement_id, expected_version)
        if current["state"] not in {EngagementState.AWAITING_SCOPE_APPROVAL.value, EngagementState.SCOPE_CHANGE_REVIEW.value}:
            raise EngagementError("This engagement is not awaiting exact scope approval.", code="invalid_state")
        scope_change_approval = current["state"] == EngagementState.SCOPE_CHANGE_REVIEW.value
        self._audit(current, "scope_approval_attempted", "attempted", {"revision_id": revision_id, "scope_hash": scope_hash})
        if current.get("approvals"):
            latest = current["approvals"][-1]
            if latest.get("revision_id") == revision_id and latest.get("scope_hash") == scope_hash:
                raise EngagementError("This exact scope revision was already approved.", code="duplicate_approval")
        if current.get("limitation"):
            raise EngagementError("The current scope is incomplete because a configured intake limit was reached.", code="limit_reached")
        revision = self._revision(current, revision_id)
        if current.get("current_scope", {}).get("revision_id") != revision_id:
            raise EngagementError("A newer scope revision superseded this preview.", code="stale_revision")
        try:
            verify_scope_revision(revision)
        except ValueError as error:
            self._audit(current, "scope_approval_rejected", "rejected", {"code": "tampered_scope"})
            raise EngagementError(str(error), code="hash_mismatch") from error
        if revision.scope_hash != scope_hash:
            self._audit(current, "scope_approval_rejected", "rejected", {"code": "hash_mismatch"})
            raise EngagementError("Approval requires the exact displayed scope hash.", code="hash_mismatch")
        if any(item.get("status") == "pending" and item.get("blocking") for item in current["questions"]):
            raise EngagementError("Blocking clarification remains unresolved.", code="clarification_required")
        now_dt = datetime.now(timezone.utc)
        stale = [item for item in current["evidence"] if item.get("source_type") in {EvidenceSourceType.AUTHORIZED_FOLDER.value, EvidenceSourceType.STRUCTURAL_SUMMARY.value} and _parse_time(item.get("stale_after")) <= now_dt]
        if stale:
            self._audit(current, "scope_approval_rejected", "rejected", {"code": "stale_evidence"})
            raise EngagementError("Material project evidence is stale; refresh the scope preview before approval.", code="stale_evidence")
        updated = _copy(current)
        approval = ScopeApproval(
            schema_version=ENGAGEMENT_SCHEMA_VERSION, approval_id=uuid4().hex,
            engagement_id=engagement_id, revision_id=revision_id,
            revision_number=revision.revision_number, scope_hash=scope_hash,
            approving_user=approving_user, engagement_state_version=expected_version,
            approved_at=now_dt,
        )
        updated["approvals"].append(approval.model_dump(mode="json"))
        updated["approved_scope_revision_id"] = revision_id
        self._move(updated, EngagementState.SCOPE_APPROVED)
        self._audit(updated, "scope_approved", "approved", {"approval_id": approval.approval_id, "revision_id": revision_id})
        if scope_change_approval:
            self._audit(updated, "scope_change_approved", "approved", {"approval_id": approval.approval_id, "revision_id": revision_id})
        stored = self._store_transition(current, updated)
        self._remember(stored, "approve", idempotency_key)
        return stored

    def reject_scope(self, *, engagement_id: str, expected_version: int, revision_id: str, reason: str, rejecting_user: str, idempotency_key: str | None = None) -> dict[str, Any]:
        replay = self._replay(engagement_id, "reject", idempotency_key)
        if replay: return replay
        current = self._current(engagement_id, expected_version)
        if current["state"] not in {EngagementState.AWAITING_SCOPE_APPROVAL.value, EngagementState.SCOPE_CHANGE_REVIEW.value}:
            raise EngagementError("This engagement is not awaiting a scope decision.", code="invalid_state")
        self._revision(current, revision_id)
        updated = _copy(current)
        rejection = ScopeRejection(schema_version=ENGAGEMENT_SCHEMA_VERSION, rejection_id=uuid4().hex, engagement_id=engagement_id, revision_id=revision_id, reason=reason, rejecting_user=rejecting_user, created_at=datetime.now(timezone.utc))
        updated["rejections"].append(rejection.model_dump(mode="json"))
        self._move(updated, EngagementState.SCOPE_PREPARING)
        self._audit(updated, "scope_rejected_by_user", "rejected", {"revision_id": revision_id, "rejection_id": rejection.rejection_id})
        stored = self._store_transition(current, updated)
        self._remember(stored, "reject", idempotency_key)
        return stored

    def launch(
        self, *, engagement_id: str, expected_version: int,
        launch_stage9: Callable[[dict[str, Any], ScopeRevision], dict[str, Any]],
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        replay = self._replay(engagement_id, "launch", idempotency_key)
        if replay:
            stored = self.repository.get_client_engagement(engagement_id)
            return stored, replay
        current = self._current(engagement_id, expected_version)
        existing = current.get("project_launch")
        if isinstance(existing, dict):
            self._audit(current, "duplicate_launch_prevented", "prevented", {"delivery_job_id": existing.get("delivery_job_id")})
            raise EngagementError("This approved scope already has a Stage 9 project.", code="duplicate_launch")
        if current["state"] != EngagementState.SCOPE_APPROVED.value:
            raise EngagementError("Approve the exact current scope before launching Stage 9.", code="approval_required")
        if int(current.get("launch_attempts") or 0) >= self.limits.max_project_launch_attempts:
            raise EngagementError("The configured project-launch attempt limit was reached.", code="limit_reached")
        revision = self._revision(current, str(current.get("approved_scope_revision_id") or ""))
        approval = next((item for item in reversed(current["approvals"]) if item.get("revision_id") == revision.revision_id and item.get("scope_hash") == revision.scope_hash), None)
        if approval is None:
            raise EngagementError("The approved revision record is unavailable.", code="approval_required")
        updated = _copy(current)
        self._move(updated, EngagementState.PROJECT_LAUNCHING)
        updated["launch_attempts"] = int(updated.get("launch_attempts") or 0) + 1
        self._audit(updated, "project_launch_attempted", "attempted", {"revision_id": revision.revision_id})
        try:
            delivery = launch_stage9(updated, revision)
        except Exception as error:
            self._move(updated, EngagementState.SCOPE_APPROVED)
            updated["last_error"] = {"code": "stage9_launch_failed", "message": _controlled(error), "at": _now()}
            self._audit(updated, "failure_recorded", "failed", {"code": "stage9_launch_failed"})
            self._store_transition(current, updated)
            raise EngagementError("Stage 9 project launch failed safely; the approved scope remains available for an idempotent retry.", code="launch_failed") from error
        delivery_id = str(delivery.get("delivery_job_id") or "")
        specification_hash = str((delivery.get("specification") or {}).get("specification_hash") or delivery.get("stage9_task_specification_hash") or "")
        if not delivery_id or len(specification_hash) != 64:
            raise EngagementError("Stage 9 returned an invalid project launch result.", code="launch_failed")
        launch = ProjectLaunchResult(
            schema_version=ENGAGEMENT_SCHEMA_VERSION, launch_id=uuid4().hex,
            engagement_id=engagement_id, scope_revision_id=revision.revision_id,
            scope_hash=revision.scope_hash, delivery_job_id=delivery_id,
            stage9_task_specification_hash=specification_hash,
            launched_at=datetime.now(timezone.utc), idempotent_replay=False,
        )
        updated["project_launch"] = launch.model_dump(mode="json")
        updated["last_error"] = None
        self._move(updated, EngagementState.PROJECT_LAUNCHED)
        self._audit(updated, "project_launched", "launched", {"delivery_job_id": delivery_id, "revision_id": revision.revision_id})
        stored = self._store_transition(current, updated)
        self._remember_payload(engagement_id, "launch", idempotency_key, delivery)
        return stored, delivery

    def request_scope_change(self, *, engagement_id: str, expected_version: int, requested_change: str, requested_by: str, idempotency_key: str | None = None) -> dict[str, Any]:
        replay = self._replay(engagement_id, "scope_change", idempotency_key)
        if replay: return replay
        current = self._current(engagement_id, expected_version)
        if current["state"] not in {EngagementState.SCOPE_APPROVED.value, EngagementState.PROJECT_LAUNCHED.value}:
            raise EngagementError("Scope changes require an approved scope.", code="invalid_state")
        if len(current["scope_revisions"]) >= self.limits.max_scope_revisions:
            raise EngagementError("The configured scope-revision limit was reached.", code="limit_reached")
        prior = self._revision(current, str(current.get("approved_scope_revision_id") or ""))
        impact = scope_change_impact(prior.scope, requested_change)
        updated = _copy(current)
        self._move(updated, EngagementState.SCOPE_CHANGE_REQUESTED)
        change_id = uuid4().hex
        revision = build_scope_revision(
            engagement_id=engagement_id, requirements=updated["requirements"], evidence=updated["evidence"],
            assumptions=updated["assumptions"], questions=updated["questions"],
            revision_number=len(updated["scope_revisions"]) + 1, parent_revision_id=prior.revision_id,
            reason=f"Scope change: {requested_change[:900]}", creator_type=CreatorType.USER_CHANGE,
            prior_scope=prior.scope, requested_change=requested_change, limits=self.limits,
        )
        change = ScopeChangeRequest(
            schema_version=ENGAGEMENT_SCHEMA_VERSION, change_id=change_id,
            engagement_id=engagement_id, requested_change=requested_change,
            classification=impact["classification"], affected_deliverable_ids=impact["affected_deliverable_ids"],
            affected_milestone_ids=impact["affected_milestone_ids"], estimate_impact=impact["estimate_impact"],
            risk_impact=impact["risk_impact"], acceptance_criteria_impact=impact["acceptance_criteria_impact"],
            resulting_revision_id=revision.revision_id, requested_by=requested_by,
            created_at=datetime.now(timezone.utc),
        )
        updated["scope_changes"].append(change.model_dump(mode="json"))
        updated["scope_revisions"].append(revision.model_dump(mode="json"))
        updated["current_scope"] = revision.model_dump(mode="json")
        self._move(updated, EngagementState.SCOPE_CHANGE_REVIEW)
        self._audit(updated, "scope_change_requested", "review_required", {"change_id": change_id, "classification": change.classification.value, "revision_id": revision.revision_id})
        stored = self._store_transition(current, updated)
        self._remember(stored, "scope_change", idempotency_key)
        return stored

    def cancel(self, *, engagement_id: str, expected_version: int, actor: str, idempotency_key: str | None = None) -> dict[str, Any]:
        replay = self._replay(engagement_id, "cancel", idempotency_key)
        if replay: return replay
        current = self._current(engagement_id, expected_version)
        if current["state"] in {EngagementState.CANCELLED.value, EngagementState.FAILED.value}:
            raise EngagementError("The engagement is already terminal.", code="invalid_state")
        updated = _copy(current)
        self._move(updated, EngagementState.CANCELLED)
        updated["cancelled_at"] = _now()
        self._audit(updated, "engagement_cancelled", "cancelled", {"actor": actor})
        stored = self._store_transition(current, updated)
        self._remember(stored, "cancel", idempotency_key)
        return stored

    def recover(self, *, engagement_id: str, conversation_id: str) -> dict[str, Any]:
        current = self.repository.get_client_engagement(engagement_id)
        if current.get("conversation_id") != conversation_id:
            raise EngagementError("The engagement belongs to a different conversation.", code="ownership_mismatch")
        if current["state"] == EngagementState.PROJECT_LAUNCHING.value:
            updated = _copy(current)
            launch = updated.get("project_launch")
            updated["state"] = EngagementState.PROJECT_LAUNCHED.value if launch else EngagementState.SCOPE_APPROVED.value
            updated["recovery"]["recovered_count"] = int(updated["recovery"].get("recovered_count") or 0) + 1
            updated["recovery"]["last_recovered_at"] = _now()
            self._audit(updated, "recovery_performed", "recovered", {"from_state": EngagementState.PROJECT_LAUNCHING.value, "to_state": updated["state"]})
            return self._store_transition(current, updated)
        self._audit(current, "recovery_performed", "no_change", {"state": current["state"]})
        return current

    def _prepare_scope(self, updated: dict[str, Any], *, reason: str) -> None:
        if updated["state"] != EngagementState.SCOPE_PREPARING.value:
            self._move(updated, EngagementState.SCOPE_PREPARING)
        parent = updated["scope_revisions"][-1]["revision_id"] if updated["scope_revisions"] else None
        revision = build_scope_revision(
            engagement_id=updated["engagement_id"], requirements=updated["requirements"],
            evidence=updated["evidence"], assumptions=updated["assumptions"], questions=updated["questions"],
            revision_number=len(updated["scope_revisions"]) + 1, parent_revision_id=parent,
            reason=reason, creator_type=CreatorType.DETERMINISTIC_SYSTEM, limits=self.limits,
        )
        updated["scope_revisions"].append(revision.model_dump(mode="json"))
        updated["current_scope"] = revision.model_dump(mode="json")
        self._move(updated, EngagementState.SCOPE_READY)
        self._audit(updated, "scope_revision_created", "created", {"revision_id": revision.revision_id, "revision_number": revision.revision_number, "scope_hash": revision.scope_hash})
        self._move(updated, EngagementState.AWAITING_SCOPE_APPROVAL)
        self._audit(updated, "scope_preview_shown", "shown", {"revision_id": revision.revision_id, "scope_hash": revision.scope_hash})

    def _current(self, engagement_id: str, expected_version: int) -> dict[str, Any]:
        try:
            current = self.repository.get_client_engagement(engagement_id)
        except LookupError as error:
            raise EngagementError("Client engagement not found.", code="not_found") from error
        if int(current.get("state_version") or 0) != int(expected_version):
            raise EngagementError("The engagement changed concurrently; reload the current state.", code="conflict")
        return current

    def _store_transition(self, current: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
        updated["updated_at"] = _now()
        stored = self.repository.transition_client_engagement(updated, expected_version=int(current["state_version"]))
        if stored is None:
            raise EngagementError("The engagement changed concurrently; reload the current state.", code="conflict")
        self.repository.store_client_engagement_records(stored)
        return stored

    def _move(self, engagement: dict[str, Any], target: EngagementState) -> None:
        try:
            engagement["state"] = transition_state(engagement["state"], target).value
        except EngagementTransitionError as error:
            raise EngagementError(str(error), code="invalid_transition") from error

    def _revision(self, engagement: dict[str, Any], revision_id: str) -> ScopeRevision:
        match = next((item for item in engagement["scope_revisions"] if item.get("revision_id") == revision_id), None)
        if match is None:
            raise EngagementError("Scope revision not found in this engagement.", code="not_found")
        return ScopeRevision.model_validate(match)

    def _audit(self, engagement: dict[str, Any], operation: str, status: str, metadata: dict[str, Any]) -> None:
        safe = _safe_audit(metadata, self.limits.max_audit_metadata_chars)
        self.repository.store_client_engagement_audit_event({
            "event_id": uuid4().hex, "engagement_id": engagement["engagement_id"],
            "conversation_id": engagement["conversation_id"], "operation": operation,
            "status": status, "metadata": safe, "created_at": _now(),
        })

    def _replay(self, engagement_id: str, operation: str, key: str | None) -> dict[str, Any] | None:
        if not key: return None
        payload = self.repository.get_client_engagement_idempotency(engagement_id, operation, key)
        if not payload: return None
        if payload.get("engagement_id"):
            return self.repository.get_client_engagement(engagement_id)
        return payload

    def _remember(self, engagement: dict[str, Any], operation: str, key: str | None) -> None:
        if key:
            self.repository.store_client_engagement_idempotency(engagement["engagement_id"], operation, key, {"engagement_id": engagement["engagement_id"], "state_version": engagement["state_version"]}, _now())

    def _remember_payload(self, engagement_id: str, operation: str, key: str | None, payload: dict[str, Any]) -> None:
        if key:
            self.repository.store_client_engagement_idempotency(engagement_id, operation, key, payload, _now())


def public_engagement(engagement: dict[str, Any]) -> EngagementPublicResponse:
    request = ClientEngagementRequest.model_validate(engagement["request"])
    current_scope = ScopeRevision.model_validate(engagement["current_scope"]) if engagement.get("current_scope") else None
    pending = [ClarificationQuestion.model_validate(item) for item in engagement.get("questions") or [] if item.get("status") == "pending"]
    missing = [item.question for item in pending]
    evidence = []
    for item in (engagement.get("evidence") or [])[: STAGE10_LIMITS.max_public_evidence_items]:
        from backend.app.client_engagement.contracts import EngagementEvidenceReference
        evidence.append(public_evidence(EngagementEvidenceReference.model_validate(item)))
    launch = ProjectLaunchResult.model_validate(engagement["project_launch"]) if engagement.get("project_launch") else None
    changes = [ScopeChangeRequest.model_validate(item) for item in engagement.get("scope_changes") or []]
    outcome = current_scope.scope.desired_outcome if current_scope else request.original_request
    return EngagementPublicResponse(
        schema_version=ENGAGEMENT_SCHEMA_VERSION, engagement_id=engagement["engagement_id"],
        conversation_id=engagement["conversation_id"], state=EngagementState(engagement["state"]),
        state_version=int(engagement["state_version"]), understood_outcome=outcome,
        authorized_evidence=evidence, missing_information=missing, pending_questions=pending,
        current_scope_revision=current_scope, approved_scope_revision_id=engagement.get("approved_scope_revision_id"),
        project_launch=launch, scope_changes=changes, limitation=engagement.get("limitation"),
        created_at=_parse_time(engagement["created_at"]), updated_at=_parse_time(engagement["updated_at"]),
    )


def stage9_task_from_scope(revision: ScopeRevision | dict[str, Any]) -> str:
    value = revision if isinstance(revision, ScopeRevision) else ScopeRevision.model_validate(revision)
    scope = value.scope
    lines = [f"Approved outcome: {scope.desired_outcome}", "Deliverables:"]
    for deliverable in scope.deliverables:
        lines.append(f"- {deliverable.title}: {deliverable.description}")
        for criterion in deliverable.acceptance_criteria:
            lines.append(f"  Acceptance: {criterion.statement} [{criterion.review_mode.value}]")
    if scope.constraints:
        lines.append("Constraints: " + "; ".join(item.text for item in scope.constraints))
    if scope.exclusions:
        lines.append("Exclusions: " + "; ".join(item.text for item in scope.exclusions))
    if scope.dependencies:
        lines.append("Dependencies: " + "; ".join(item.text for item in scope.dependencies))
    lines.append("Evidence references: " + ", ".join(sorted({value for refs in scope.evidence_traceability.values() for value in refs})))
    lines.append(f"Maximum allowed scope: {scope.recommended_delivery_configuration.get('max_work_units', 20)} work units; no unapproved additions.")
    lines.append("Expected verification: record evidence for every acceptance criterion; human-review criteria remain manual.")
    lines.append(f"Stage 10 traceability: engagement={value.engagement_id}; scope_revision={value.revision_id}; scope_hash={value.scope_hash}.")
    return "\n".join(lines)[:3000]


def detect_engagement_request(message: str) -> bool:
    """Conservatively identify client-style intake without replacing explicit Stage 9 commands."""
    normalized = " ".join(str(message or "").lower().split())
    if not re_search_action(normalized):
        return False
    scenario_signal = any(term in normalized for term in (
        "website", "web site", "contact form", "restaurant", "supplied sales dataset",
        "produce four charts", "produce 4 charts", "application in this folder crashes",
        "users upload a csv", "client request", "scope this project",
    ))
    return scenario_signal and not ("project delivery" in normalized or "stage 9" in normalized)


def re_search_action(value: str) -> bool:
    import re
    return bool(re.search(r"\b(build|create|produce|analy[sz]e|diagnose|fix|repair|scope)\b", value))


def _set_question_status(values: list[dict[str, Any]], question_id: str, status: str) -> None:
    for item in values:
        if item.get("question_id") == question_id:
            item["status"] = status


def _assumption_from_model(engagement_id: str, text: str) -> Assumption:
    return Assumption(schema_version=ENGAGEMENT_SCHEMA_VERSION, assumption_id="assumption-" + hashlib.sha256(f"{engagement_id}\0{text}".encode()).hexdigest()[:20], text=text, evidence_ids=[], accepted_by_user=False, materially_reduces_confidence=True, created_at=datetime.now(timezone.utc))


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, default=str))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime): return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _controlled(error: Exception) -> str:
    if isinstance(error, (ValueError, OSError)):
        return str(error)[:1000]
    return "The operation failed safely. Review the engagement audit record and retry."


def _safe_audit(metadata: dict[str, Any], limit: int) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = key.lower()
        if any(token in lowered for token in ("secret", "token", "password", "cookie", "authorization", "content", "excerpt", "path")):
            safe[key] = "[REDACTED]"
        elif isinstance(value, str): safe[key] = value[:1000]
        elif isinstance(value, list): safe[key] = [str(item)[:200] for item in value[:30]]
        elif isinstance(value, (int, float, bool, type(None))): safe[key] = value
    encoded = json.dumps(safe, sort_keys=True)
    return safe if len(encoded) <= limit else {"bounded": True, "summary": encoded[: max(0, limit - 100)]}


__all__ = ["EngagementError", "EngagementService", "detect_engagement_request", "public_engagement", "stage9_task_from_scope"]
