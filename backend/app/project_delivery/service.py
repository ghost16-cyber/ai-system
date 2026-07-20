from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from backend.app.folders.safety import project_root_fingerprint, safe_relative_path
from backend.app.project_analysis import (
    IncompleteProjectManifestError, ProjectStateManifest, build_analysis_plan, build_project_index,
    build_project_state_manifest, file_hashes,
)
from backend.app.project_analysis.model_synthesis import SynthesisGateway
from backend.app.project_analysis.model_synthesis.gateway import SynthesisGatewayError
from backend.app.project_delivery.contracts import (
    PLAN_VERSION,
    SPECIFICATION_VERSION,
    AcceptanceCriterion,
    DeliveryStatus,
    ExecutionPlan,
    ExecutionPlanRevision,
    HandoffReport,
    ScopeChange,
    TaskSpecification,
    VerificationMode,
    VerificationRecord,
    VerifierResult,
    VerifierOutcome,
    VerificationState,
    PlanApprovalGrant,
    ImmutableWorkUnitDefinition,
    WorkUnit,
    WorkUnitExecutionState,
    WorkUnitExecutionStatus,
    WorkUnitStatus,
    parse_model_specification,
)
from backend.app.project_delivery.limits import DeliveryLimits, STAGE9_LIMITS


class ProjectDeliveryError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_transition") -> None:
        super().__init__(message)
        self.code = code


_CLEAR_ACTION = re.compile(
    r"\b(add(?:ing)?|analy[sz](?:e|ing)|build(?:ing)?|creat(?:e|ing)|chang(?:e|ing)|"
    r"export(?:ing)?|fix(?:ing)?|generat(?:e|ing)|implement(?:ing)?|produc(?:e|ing)|"
    r"sav(?:e|ing)|updat(?:e|ing)|writ(?:e|ing)|remov(?:e|ing)|renam(?:e|ing)|"
    r"validat(?:e|ing)|test(?:ing)?|refactor(?:ing)?)\b",
    re.I,
)
_PATH_TOKEN = re.compile(r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
_FILE_TOKEN = re.compile(
    r"(?<![\w./-])[A-Za-z0-9_-][A-Za-z0-9_.-]*\."
    r"(?:csv|json|parquet|tsv|xlsx?|py|md|png|jpe?g|svg|html?|pdf|ipynb)"
    r"(?![\w.-])",
    re.I,
)
_MATERIAL_TERMS = {
    "production": "Should this delivery stop at locally verified changes, or target a separately approved production process?",
    "deploy": "Which separately approved deployment environment is in scope? Deployment is excluded until clarified.",
    "credential": "Which credential-dependent behavior should be mocked or left as a documented integration point?",
    "payment": "Which non-production payment behavior is intended? Live payment access remains excluded.",
    "migration": "Is a database migration explicitly in scope, and what rollback requirement should it satisfy?",
}


def create_delivery_job(
    *,
    root: str | Path,
    conversation_id: str,
    folder_access_id: str,
    user_request: str,
    action_run_id: str,
    model_gateway: SynthesisGateway | None = None,
    limits: DeliveryLimits = STAGE9_LIMITS,
    delivery_job_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a compatibility delivery projection without persistence or lifecycle writes."""
    approved = Path(root).resolve()
    delivery_job_id = delivery_job_id or uuid4().hex
    builder_timestamp = created_at or _now()
    manifest = build_project_state_manifest(approved, workspace_id=folder_access_id)
    index = build_project_index(
        approved,
        conversation_id=conversation_id,
        folder_access_id=folder_access_id,
        job_id=delivery_job_id,
    )
    analysis = build_analysis_plan(index, user_request, relevant_paths=_request_paths(user_request))
    source = "deterministic"
    model_calls = 0
    provider_status = "not_invoked"
    specification_data = _deterministic_specification_data(user_request, index, analysis)
    if specification_data is None and model_gateway is not None:
        model_calls = 1
        try:
            payload = _bounded_model_request(user_request, index, analysis, limits)
            result = model_gateway.generate(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            model = parse_model_specification(result.raw_response)
            specification_data = {
                "objective": model.normalized_objective,
                "requirements": model.in_scope_requirements,
                "exclusions": model.explicit_exclusions,
                "assumptions": model.assumptions,
                "deliverables": model.requested_deliverables,
                "criteria": [item.model_dump(mode="json") for item in model.criteria],
                "evidence": model.evidence_references,
                "clarifications": [model.clarification_question] if model.clarification_question else [],
            }
            source = "model-assisted"
            provider_status = f"accepted:{result.provider}:{result.model}"
        except (ValueError, SynthesisGatewayError) as error:
            provider_status = f"rejected:{type(error).__name__}"
    if specification_data is None:
        question = _clarification_question(user_request, analysis)
        specification_data = _clarification_specification_data(user_request, analysis, question)
    specification = _make_specification(
        delivery_job_id=delivery_job_id,
        conversation_id=conversation_id,
        workspace_id=folder_access_id,
        original_request=user_request,
        data=specification_data,
        source=source,
        provider_status=provider_status,
        revision=1,
    )
    clarification_questions = list(specification.clarification_questions)
    plan = None if clarification_questions else build_execution_plan(
        specification,
        index,
        analysis,
        limits=limits,
        created_at=builder_timestamp,
    )
    status = DeliveryStatus.CLARIFICATION if clarification_questions else DeliveryStatus.AWAITING_PLAN_APPROVAL
    now = builder_timestamp
    job = {
        "delivery_job_id": delivery_job_id,
        "canonical_generation": "canonical",
        "action_run_id": action_run_id,
        "conversation_id": conversation_id,
        "folder_access_id": folder_access_id,
        "root_fingerprint": project_root_fingerprint(approved),
        "project_state_hash": manifest.manifest_hash,
        "project_state_manifest": manifest.model_dump(mode="json"),
        "status": status.value,
        "original_user_request": _bounded(user_request, 3000),
        "specification": specification.model_dump(mode="json"),
        "specification_revisions": [specification.model_dump(mode="json")],
        "analysis_id": index["analysis_id"],
        "analysis_hash": stage6_analysis_hash(index),
        "analysis_index": index,
        "analysis": analysis,
        "plan": plan.model_dump(mode="json") if plan else None,
        "plan_revisions": [plan.model_dump(mode="json")] if plan else [],
        "plan_revision": None,
        "plan_revision_history": [],
        "work_unit_execution_states": [],
        "plan_approval": None,
        "clarifications": ([{
            "clarification_id": _stable_id("clarification", delivery_job_id, clarification_questions[0]),
            "question": clarification_questions[0], "answer": None, "status": "pending", "created_at": now,
        }] if clarification_questions else []),
        "active_work_unit_id": None,
        "patch_references": [],
        "command_references": [],
        "verification_records": [],
        "verifier_results": [],
        "scope_changes": [],
        "rollback_records": [],
        "handoff": None,
        "budgets": {
            "clarifications": len(clarification_questions), "planning_model_calls": model_calls,
            "verification_model_calls": 0, "applied_patches": 0, "command_executions": 0,
            "scope_changes": 0,
        },
        "limits": limits.__dict__ if hasattr(limits, "__dict__") else {
            name: getattr(limits, name) for name in limits.__dataclass_fields__
        },
        "last_error": None,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
        "cancelled_at": None,
    }
    if plan is not None:
        job = _install_plan_revision(job, plan, specification)
    return job


def build_execution_plan(
    specification: TaskSpecification,
    index: dict[str, Any],
    analysis: dict[str, Any],
    *,
    limits: DeliveryLimits = STAGE9_LIMITS,
    revision: int = 1,
    created_at: str | None = None,
) -> ExecutionPlan:
    """Build an immutable plan without persistence or lifecycle side effects."""
    if specification.clarification_questions:
        raise ProjectDeliveryError("The task specification still requires clarification.", code="clarification_required")
    paths = _planned_paths(specification, index, analysis)
    if not paths:
        raise ProjectDeliveryError("No known project-relative files support a bounded execution plan.", code="insufficient_evidence")
    if len(paths) > 30:
        raise ProjectDeliveryError("The planned file set exceeds its bounded limit.", code="limit_reached")
    tests = [path for path in paths if _is_test_path(path)]
    implementation = [path for path in paths if path not in tests]
    groups = [group for group in (implementation, tests) if group]
    if len(groups) > limits.max_work_units:
        raise ProjectDeliveryError("The work-unit limit was reached.", code="limit_reached")
    criteria = [item.criterion_id for item in specification.acceptance_criteria]
    units: list[WorkUnit] = []
    for offset, group in enumerate(groups, start=1):
        unit_id = f"wu-{offset:02d}"
        is_test = all(_is_test_path(path) for path in group)
        refs = criteria
        data = {
            "work_unit_id": unit_id,
            "title": "Add or update verification coverage" if is_test else "Implement the bounded project change",
            "objective": "Update only the planned test evidence." if is_test else specification.normalized_objective,
            "requirement_references": [f"requirement-{index + 1}" for index in range(len(specification.in_scope_requirements))],
            "criterion_references": refs,
            "dependencies": [units[-1].work_unit_id] if units else [],
            "expected_files": group,
            "expected_symbols": _symbols_for_paths(analysis, group),
            "expected_patch_scope": f"An immutable patch limited to {len(group)} planned file(s).",
            "expected_validation_commands": [mode.value for mode in specification.required_verification_methods],
            "completion_conditions": [f"Evidence is recorded for {criterion_id}." for criterion_id in refs],
            "risk_level": "medium" if len(group) > 5 else "low",
            "clarification_may_be_required": False,
            "status": WorkUnitStatus.READY if not units else WorkUnitStatus.PENDING,
        }
        data["work_unit_hash"] = immutable_hash({key: value for key, value in data.items() if key != "status"})
        units.append(WorkUnit.model_validate(data))
    mapping = {criterion: [unit.work_unit_id for unit in units if criterion in unit.criterion_references] for criterion in criteria}
    if any(not values for values in mapping.values()):
        raise ProjectDeliveryError("Every acceptance criterion must map to a work unit.", code="invalid_plan")
    _validate_dependencies(units)
    data = {
        "contract_version": PLAN_VERSION,
        "delivery_job_id": specification.delivery_job_id,
        "plan_revision": revision,
        "specification_hash": specification.specification_hash,
        "stage6_analysis_hash": stage6_analysis_hash(index),
        "work_units": [unit.model_dump(mode="json") for unit in units],
        "ordered_work_unit_ids": [unit.work_unit_id for unit in units],
        "dependency_graph": {unit.work_unit_id: unit.dependencies for unit in units},
        "acceptance_criterion_mapping": mapping,
        "estimated_patch_count": len(units),
        "estimated_command_count": min(limits.max_command_executions, len(specification.required_verification_methods)),
        "explicit_exclusions": specification.explicit_exclusions,
        "plan_source": specification.specification_source,
        "confidence": 0.92 if specification.specification_source == "deterministic" else 0.72,
        "confidence_reasons": ["Stage 6 structural evidence identifies the bounded file set and verification configuration."],
        "created_at": created_at or _now(),
    }
    data["plan_hash"] = immutable_hash(_immutable_plan_payload(data))
    return ExecutionPlan.model_validate(data)


def approve_plan(
    job: dict[str, Any], *, plan_hash: str, actor: str = "user",
    root: str | Path | None = None,
) -> dict[str, Any]:
    try:
        approved_manifest = ProjectStateManifest.model_validate(job.get("project_state_manifest"))
    except ValueError as error:
        raise ProjectDeliveryError("A valid complete project manifest is required before approval.", code="incomplete_project_manifest") from error
    if not approved_manifest.complete or approved_manifest.manifest_hash != job.get("project_state_hash"):
        raise ProjectDeliveryError("An incomplete or mismatched project manifest cannot authorize approval.", code="incomplete_project_manifest")
    if root is not None:
        current_manifest = build_project_state_manifest(root, workspace_id=str(job.get("folder_access_id") or ""))
        if current_manifest.manifest_hash != approved_manifest.manifest_hash:
            raise ProjectDeliveryError("The project changed after planning; create a fresh plan before approval.", code="stale_repository")
    if not isinstance(job.get("plan_revision"), dict):
        if isinstance(job.get("plan_approval"), dict):
            raise ProjectDeliveryError(
                "The legacy approval cannot authorize an immutable Stage 0 plan; review and approve it again.",
                code="migration_reapproval_required",
            )
        job = _install_plan_revision(
            job, _plan(job), TaskSpecification.model_validate(job["specification"]),
        )
    revision = _plan_revision(job)
    if revision.content_hash != plan_hash:
        raise ProjectDeliveryError("The exact current plan hash is required.", code="hash_mismatch")
    approval = job.get("plan_approval")
    if isinstance(approval, dict) and approval.get("plan_revision_id") == revision.plan_revision_id and approval.get("plan_content_hash") == plan_hash:
        return job
    if job.get("status") != DeliveryStatus.AWAITING_PLAN_APPROVAL.value:
        raise ProjectDeliveryError("This delivery job is not awaiting plan approval.", code="invalid_state")
    updated = _copy(job)
    grant = PlanApprovalGrant(
        approval_id=_stable_id("plan-approval", job["delivery_job_id"], revision.plan_revision_id, plan_hash),
        project_run_id=job["delivery_job_id"], plan_revision_id=revision.plan_revision_id,
        plan_content_hash=plan_hash, specification_hash=revision.specification_hash,
        scope_revision=int(TaskSpecification.model_validate(job["specification"]).revision),
        workspace_id=job["folder_access_id"], root_fingerprint=job["root_fingerprint"],
        approved_by=_bounded(actor, 80), approved_at=datetime.now(timezone.utc),
        expected_project_state_version=int(job.get("state_version") or 1),
    )
    updated["plan_approval"] = {**grant.model_dump(mode="json"), "plan_hash": plan_hash}
    updated["status"] = DeliveryStatus.PLAN_APPROVED.value
    updated["updated_at"] = _now()
    return updated


def adapt_legacy_delivery_job(job: dict[str, Any]) -> dict[str, Any]:
    """Create a safe v2 projection without carrying a legacy approval forward."""
    if isinstance(job.get("plan_revision"), dict) or not isinstance(job.get("plan"), dict):
        return job
    had_legacy_approval = isinstance(job.get("plan_approval"), dict)
    updated = _copy(job)
    updated["plan_approval"] = None
    updated["status"] = DeliveryStatus.AWAITING_PLAN_APPROVAL.value
    updated = _install_plan_revision(
        updated, _plan(updated), TaskSpecification.model_validate(updated["specification"]),
    )
    updated["legacy_migration"] = {
        "status": "reapproval_required" if had_legacy_approval else "adapted_unapproved",
        "legacy_approval_discarded": had_legacy_approval,
        "adapted_at": _now(),
    }
    if had_legacy_approval:
        updated["last_error"] = {
            "code": "migration_reapproval_required",
            "message": "A legacy mutable-plan approval was discarded; approve the new immutable revision.",
        }
    updated["updated_at"] = _now()
    return updated


def submit_clarification(
    job: dict[str, Any], *, answer: str, root: str | Path, limits: DeliveryLimits = STAGE9_LIMITS,
) -> dict[str, Any]:
    if job.get("status") != DeliveryStatus.CLARIFICATION.value:
        raise ProjectDeliveryError("This delivery job is not awaiting clarification.", code="invalid_state")
    if not answer.strip():
        raise ProjectDeliveryError("A clarification response is required.", code="invalid_request")
    if int(job.get("budgets", {}).get("clarifications", 0)) > limits.max_clarifications:
        return _limit_reached(job, "clarification_limit")
    prior = TaskSpecification.model_validate(job["specification"])
    if prior.revision >= limits.max_specification_revisions:
        return _limit_reached(job, "specification_revision_limit")
    index = build_project_index(
        root, conversation_id=job["conversation_id"], folder_access_id=job["folder_access_id"],
        job_id=job["delivery_job_id"], previous=job.get("analysis_index"),
    )
    analysis = build_analysis_plan(index, f"{prior.original_user_request}\nClarification: {answer}")
    data = _deterministic_specification_data(f"{prior.original_user_request}. Clarification: {answer}", index, analysis)
    if data is None:
        data = {
            "objective": prior.normalized_objective,
            "requirements": [*prior.in_scope_requirements, f"User clarification: {_bounded(answer, 500)}"],
            "exclusions": prior.explicit_exclusions,
            "assumptions": prior.assumptions,
            "deliverables": prior.requested_deliverables,
            "criteria": [item.model_dump(mode="json") for item in prior.acceptance_criteria],
            "evidence": prior.evidence_references,
            "clarifications": [],
        }
    data["clarifications"] = []
    spec = _make_specification(
        delivery_job_id=job["delivery_job_id"], conversation_id=job["conversation_id"],
        workspace_id=job["folder_access_id"], original_request=prior.original_user_request,
        data=data, source="deterministic", provider_status="not_invoked", revision=prior.revision + 1,
    )
    plan = build_execution_plan(spec, index, analysis, limits=limits, revision=len(job.get("plan_revisions") or []) + 1)
    updated = _copy(job)
    clarifications = list(updated.get("clarifications") or [])
    if clarifications:
        clarifications[-1].update({"answer": _bounded(answer, 2000), "status": "answered", "answered_at": _now()})
    manifest = build_project_state_manifest(root, workspace_id=job["folder_access_id"])
    updated.update({
        "specification": spec.model_dump(mode="json"),
        "specification_revisions": [*updated.get("specification_revisions", []), spec.model_dump(mode="json")],
        "plan": plan.model_dump(mode="json"),
        "plan_revisions": [*updated.get("plan_revisions", []), plan.model_dump(mode="json")],
        "plan_approval": None, "clarifications": clarifications,
        "analysis_id": index["analysis_id"], "analysis_hash": stage6_analysis_hash(index),
        "analysis_index": index, "analysis": analysis,
        "project_state_hash": manifest.manifest_hash,
        "project_state_manifest": manifest.model_dump(mode="json"),
        "status": DeliveryStatus.AWAITING_PLAN_APPROVAL.value, "updated_at": _now(),
    })
    return _install_plan_revision(updated, plan, spec)


def activate_next_work_unit(job: dict[str, Any], *, root: str | Path) -> dict[str, Any]:
    plan = _plan(job)
    revision = _plan_revision(job)
    approval = job.get("plan_approval") or {}
    if approval.get("plan_revision_id") != revision.plan_revision_id or approval.get("plan_content_hash") != revision.content_hash:
        raise ProjectDeliveryError("Approve the exact current plan before preparing work.", code="approval_required")
    if job.get("active_work_unit_id"):
        raise ProjectDeliveryError("Only one work unit may be active at a time.", code="conflict")
    if job.get("status") not in {DeliveryStatus.PLAN_APPROVED.value, DeliveryStatus.PATCH_APPLIED.value}:
        raise ProjectDeliveryError("The delivery job is not ready to activate a work unit.", code="invalid_state")
    current_manifest = build_project_state_manifest(root, workspace_id=job["folder_access_id"])
    current_state = current_manifest.manifest_hash
    if current_state != job.get("project_state_hash"):
        raise ProjectDeliveryError("The project changed after planning; replan from fresh analysis.", code="stale_repository")
    fresh = build_project_index(
        root, conversation_id=job["conversation_id"], folder_access_id=job["folder_access_id"],
        job_id=job["delivery_job_id"], previous=job.get("analysis_index"),
    )
    if stage6_analysis_hash(fresh) != plan.stage6_analysis_hash:
        raise ProjectDeliveryError("Stage 6 structural evidence changed after plan approval.", code="stale_analysis")
    units = [unit.model_copy(deep=True) for unit in plan.work_units]
    state_by_id = _execution_state_map(job)
    completed = {unit.work_unit_id for unit in units if state_by_id[unit.work_unit_id].status == WorkUnitExecutionStatus.COMPLETED}
    selected = next((unit for unit in units if state_by_id[unit.work_unit_id].status in {WorkUnitExecutionStatus.READY, WorkUnitExecutionStatus.PENDING} and set(unit.dependencies) <= completed), None)
    if selected is None:
        raise ProjectDeliveryError("No dependency-safe pending work unit is available.", code="blocked_dependency")
    updated = _set_unit_status(job, selected.work_unit_id, WorkUnitStatus.PREPARING)
    updated.update({
        "active_work_unit_id": selected.work_unit_id,
        "analysis_id": fresh["analysis_id"], "analysis_index": fresh,
        "project_state_manifest": current_manifest.model_dump(mode="json"),
        "status": DeliveryStatus.PREPARING.value, "updated_at": _now(),
    })
    return updated


def link_patch_preview(job: dict[str, Any], *, patch: dict[str, Any]) -> dict[str, Any]:
    unit = _active_unit(job)
    actual = {safe_relative_path(path) for path in patch.get("file_set", [])}
    planned = set(unit.expected_files)
    if not actual or not actual <= planned:
        return record_scope_change(
            job, work_unit_id=unit.work_unit_id, reason_code="unplanned_file",
            explanation="The patch preview includes files outside the approved work-unit scope.",
            affected_paths=sorted(actual - planned),
        )
    prior = list(job.get("patch_references") or [])
    if len(prior) >= int(job.get("limits", {}).get("max_applied_patches", STAGE9_LIMITS.max_applied_patches)):
        return _limit_reached(job, "patch_limit")
    patch_id = str(patch.get("patch_id") or "")
    existing = next((item for item in prior if item.get("patch_id") == patch_id), None)
    if existing:
        return job
    reference = {
        "patch_id": patch_id, "work_unit_id": unit.work_unit_id,
        "proposal_fingerprint": patch.get("proposal_fingerprint"), "status": "awaiting_approval",
        "file_set": sorted(actual), "before_hashes": {item["relative_path"]: item["before_hash"] for item in patch.get("changes", [])},
        "after_hashes": {item["relative_path"]: item["after_hash"] for item in patch.get("changes", [])},
        "created_at": _now(),
    }
    updated = _set_unit_status(job, unit.work_unit_id, WorkUnitStatus.AWAITING_PATCH)
    updated.update({
        "patch_references": [*prior, reference], "status": DeliveryStatus.PATCH_READY.value,
        "updated_at": _now(),
    })
    return updated


def record_patch_applied(job: dict[str, Any], *, patch_id: str, current_state_hash: str) -> dict[str, Any]:
    refs = [dict(item) for item in job.get("patch_references") or []]
    match = next((item for item in refs if item.get("patch_id") == patch_id), None)
    if match is None:
        raise ProjectDeliveryError("The patch is not linked to this delivery job.", code="ownership_mismatch")
    if match.get("status") == "applied":
        return job
    match.update({"status": "applied", "applied_at": _now(), "project_state_hash_after": current_state_hash})
    updated = _set_unit_status(job, str(match["work_unit_id"]), WorkUnitStatus.APPLIED)
    budgets = dict(updated.get("budgets") or {})
    budgets["applied_patches"] = int(budgets.get("applied_patches", 0)) + 1
    updated.update({
        "patch_references": refs, "budgets": budgets, "project_state_hash": current_state_hash,
        "status": DeliveryStatus.PATCH_APPLIED.value, "updated_at": _now(),
    })
    return updated


def record_verification(
    job: dict[str, Any], *, work_unit_id: str, criterion_id: str,
    state: VerificationState, method: VerificationMode, evidence_references: Iterable[str] = (),
    command_run_references: Iterable[str] = (), relevant_file_hashes: dict[str, str] | None = None,
    relevant_diff_hashes: Iterable[str] = (), structural_analysis_references: Iterable[str] = (),
    failure_explanation: str | None = None,
    verifier_result: VerifierResult | dict[str, Any] | None = None,
) -> dict[str, Any]:
    specification = TaskSpecification.model_validate(job["specification"])
    criterion = next((item for item in specification.acceptance_criteria if item.criterion_id == criterion_id), None)
    if criterion is None:
        raise ProjectDeliveryError("Unknown acceptance criterion.", code="unknown_criterion")
    if method != criterion.verification_mode:
        raise ProjectDeliveryError("Verification evidence does not match the criterion's declared method.", code="evidence_mismatch")
    evidence = list(dict.fromkeys(str(item) for item in evidence_references if item))[:30]
    command_refs = list(dict.fromkeys(str(item) for item in command_run_references if item))[:15]
    structural_refs = list(dict.fromkeys(str(item) for item in structural_analysis_references if item))[:10]
    typed_result = (
        verifier_result if isinstance(verifier_result, VerifierResult)
        else VerifierResult.model_validate(verifier_result) if isinstance(verifier_result, dict)
        else None
    )
    if state == VerificationState.SATISFIED:
        if typed_result is None:
            raise ProjectDeliveryError("A fresh typed verifier result is required for automatic satisfaction.", code="missing_checker")
        revision = _plan_revision(job)
        if (
            typed_result.outcome != VerifierOutcome.PASSED
            or typed_result.plan_revision_id != revision.plan_revision_id
            or typed_result.project_run_id != job["delivery_job_id"]
            or typed_result.work_unit_id != work_unit_id
            or typed_result.criterion_id != criterion_id
            or typed_result.criterion_definition_hash != immutable_hash(criterion.model_dump(mode="json"))
            or typed_result.input_manifest_hash != job.get("project_state_hash")
        ):
            raise ProjectDeliveryError("The typed verifier result is stale or does not satisfy this criterion.", code="stale_verifier_result")
        if method in {VerificationMode.EXISTING_TEST, VerificationMode.NEW_TEST, VerificationMode.TYPE_CHECK, VerificationMode.LINT, VerificationMode.BUILD, VerificationMode.APPROVED_COMMAND} and not command_refs:
            raise ProjectDeliveryError("Successful approved command evidence is required for this criterion.", code="missing_evidence")
        if method == VerificationMode.STRUCTURAL and not structural_refs:
            raise ProjectDeliveryError("Fresh Stage 6 structural evidence is required.", code="missing_evidence")
        if method in {VerificationMode.FILE_PRESENCE, VerificationMode.CONFIGURATION, VerificationMode.EXACT_ASSERTION} and not (evidence or relevant_file_hashes):
            raise ProjectDeliveryError("File or exact-assertion evidence is required.", code="missing_evidence")
        if method == VerificationMode.MANUAL:
            raise ProjectDeliveryError("Manual verification cannot be satisfied automatically.", code="manual_verification_required")
    record_id = uuid4().hex
    data = {
        "verification_id": record_id, "delivery_job_id": job["delivery_job_id"],
        "work_unit_id": work_unit_id, "criterion_id": criterion_id, "state": state.value,
        "method": method.value, "evidence_references": evidence,
        "command_run_references": command_refs, "relevant_file_hashes": relevant_file_hashes or {},
        "relevant_diff_hashes": list(relevant_diff_hashes)[:15],
        "structural_analysis_references": structural_refs,
        "failure_explanation": _bounded(failure_explanation or "", 4000) or None,
        "verifier_result_id": typed_result.verifier_result_id if typed_result else None,
        "verifier_result_hash": typed_result.result_hash if typed_result else None,
        "input_manifest_hash": typed_result.input_manifest_hash if typed_result else None,
        "plan_revision_id": typed_result.plan_revision_id if typed_result else None,
        "verified_at": _now(),
    }
    data["verification_hash"] = immutable_hash(data)
    record = VerificationRecord.model_validate(data)
    updated = _copy(job)
    updated["verification_records"] = [*updated.get("verification_records", []), record.model_dump(mode="json")]
    if typed_result is not None:
        existing_results = list(updated.get("verifier_results") or [])
        if not any(item.get("verifier_result_id") == typed_result.verifier_result_id for item in existing_results if isinstance(item, dict)):
            existing_results.append(typed_result.model_dump(mode="json"))
        updated["verifier_results"] = existing_results
    if state == VerificationState.FAILED:
        updated["status"] = DeliveryStatus.VERIFICATION_FAILED.value
    elif state == VerificationState.MANUAL:
        updated["status"] = DeliveryStatus.AWAITING_MANUAL.value
    updated["updated_at"] = _now()
    return _recalculate_progress(updated)


def record_scope_change(
    job: dict[str, Any], *, work_unit_id: str | None, reason_code: str,
    explanation: str, affected_paths: list[str], material: bool = True,
) -> dict[str, Any]:
    count = int(job.get("budgets", {}).get("scope_changes", 0)) + 1
    limit = int(job.get("limits", {}).get("max_scope_change_cycles", STAGE9_LIMITS.max_scope_change_cycles))
    if count > limit:
        return _limit_reached(job, "scope_change_limit")
    data = {
        "scope_change_id": uuid4().hex, "delivery_job_id": job["delivery_job_id"],
        "work_unit_id": work_unit_id, "reason_code": _bounded(reason_code, 80),
        "explanation": _bounded(explanation, 2000),
        "affected_paths": [safe_relative_path(path) for path in affected_paths][:30],
        "material": material, "created_at": _now(),
    }
    data["scope_change_hash"] = immutable_hash(data)
    record = ScopeChange.model_validate(data)
    updated = _copy(job)
    budgets = dict(updated.get("budgets") or {})
    budgets["scope_changes"] = count
    updated.update({
        "scope_changes": [*updated.get("scope_changes", []), record.model_dump(mode="json")],
        "budgets": budgets, "plan_approval": None,
        "status": DeliveryStatus.REPLANNING.value if material else updated.get("status"),
        "updated_at": _now(),
    })
    return updated


def revise_scope(
    job: dict[str, Any], *, root: str | Path, explanation: str,
    limits: DeliveryLimits = STAGE9_LIMITS,
) -> dict[str, Any]:
    if job.get("status") != DeliveryStatus.REPLANNING.value:
        raise ProjectDeliveryError("This delivery job is not awaiting a scope revision.", code="invalid_state")
    prior = TaskSpecification.model_validate(job["specification"])
    if prior.revision >= limits.max_specification_revisions:
        return _limit_reached(job, "specification_revision_limit")
    if len(job.get("plan_revisions") or []) >= limits.max_plan_revisions:
        return _limit_reached(job, "plan_revision_limit")
    request = f"{prior.original_user_request}. Approved scope revision: {_bounded(explanation, 1000)}"
    index = build_project_index(
        root, conversation_id=job["conversation_id"], folder_access_id=job["folder_access_id"],
        job_id=job["delivery_job_id"], previous=job.get("analysis_index"),
    )
    analysis = build_analysis_plan(index, request)
    data = _deterministic_specification_data(request, index, analysis)
    if data is None:
        raise ProjectDeliveryError("The scope revision is still too ambiguous for a bounded plan.", code="clarification_required")
    spec = _make_specification(
        delivery_job_id=job["delivery_job_id"], conversation_id=job["conversation_id"],
        workspace_id=job["folder_access_id"], original_request=prior.original_user_request,
        data=data, source="deterministic", provider_status="not_invoked", revision=prior.revision + 1,
    )
    plan = build_execution_plan(
        spec, index, analysis, limits=limits, revision=len(job.get("plan_revisions") or []) + 1,
    )
    updated = _copy(job)
    manifest = build_project_state_manifest(root, workspace_id=job["folder_access_id"])
    updated.update({
        "specification": spec.model_dump(mode="json"),
        "specification_revisions": [*updated.get("specification_revisions", []), spec.model_dump(mode="json")],
        "plan": plan.model_dump(mode="json"),
        "plan_revisions": [*updated.get("plan_revisions", []), plan.model_dump(mode="json")],
        "plan_approval": None, "active_work_unit_id": None,
        "analysis_id": index["analysis_id"], "analysis_hash": stage6_analysis_hash(index),
        "analysis_index": index, "analysis": analysis,
        "project_state_hash": manifest.manifest_hash,
        "project_state_manifest": manifest.model_dump(mode="json"),
        "status": DeliveryStatus.AWAITING_PLAN_APPROVAL.value, "updated_at": _now(),
    })
    return _install_plan_revision(updated, plan, spec, supersedes=_plan_revision(job).plan_revision_id)


def record_rollback(job: dict[str, Any], *, patch_id: str, restored_state_hash: str) -> dict[str, Any]:
    refs = [dict(item) for item in job.get("patch_references") or []]
    match = next((item for item in refs if item.get("patch_id") == patch_id), None)
    if match is None or match.get("status") != "applied":
        raise ProjectDeliveryError("Only an applied Stage 9 patch may be rolled back.", code="invalid_rollback")
    later_applied = [item for item in refs[refs.index(match) + 1:] if item.get("status") == "applied"]
    if later_applied:
        raise ProjectDeliveryError("Rollback must preserve dependency order; roll back later patches first.", code="unsafe_rollback")
    match.update({"status": "rolled_back", "rolled_back_at": _now()})
    invalidated = [item for item in job.get("verification_records") or [] if item.get("work_unit_id") == match.get("work_unit_id")]
    updated = _set_unit_status(job, str(match["work_unit_id"]), WorkUnitStatus.ROLLED_BACK)
    updated.update({
        "patch_references": refs,
        "verification_records": [item for item in updated.get("verification_records", []) if item not in invalidated],
        "rollback_records": [*updated.get("rollback_records", []), {
            "rollback_id": _stable_id("rollback", job["delivery_job_id"], patch_id, _now()),
            "patch_id": patch_id, "work_unit_id": match["work_unit_id"],
            "restored_state_hash": restored_state_hash, "invalidated_verification_count": len(invalidated),
            "completed_at": _now(),
        }],
        "project_state_hash": restored_state_hash, "active_work_unit_id": None,
        "status": DeliveryStatus.PLAN_APPROVED.value, "handoff": None,
        "completed_at": None, "updated_at": _now(),
    })
    return updated


def generate_handoff(
    job: dict[str, Any],
    *,
    root: str | Path | None = None,
    handoff_id: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Build a handoff projection without persistence or lifecycle side effects."""
    if root is not None:
        current_manifest = build_project_state_manifest(
            root, workspace_id=str(job.get("folder_access_id") or "")
        )
        if current_manifest.manifest_hash != job.get("project_state_hash"):
            raise ProjectDeliveryError(
                "The project changed after verification; run the required verifiers again before handoff.",
                code="stale_verifier_result",
            )
    if job.get("handoff"):
        return job
    specification = TaskSpecification.model_validate(job["specification"])
    fresh_records = _fresh_verification_records(job)
    latest = _latest_criterion_states({**job, "verification_records": fresh_records})
    required = [item for item in specification.acceptance_criteria if item.required]
    manual = [item for item in required if latest.get(item.criterion_id) == VerificationState.MANUAL.value]
    failed = [item for item in required if latest.get(item.criterion_id) in {VerificationState.FAILED.value, VerificationState.BLOCKED.value}]
    pending = [item for item in required if latest.get(item.criterion_id) not in {VerificationState.SATISFIED.value, VerificationState.WAIVED.value, VerificationState.MANUAL.value, VerificationState.FAILED.value, VerificationState.BLOCKED.value}]
    if manual:
        completion = "awaiting_manual_verification"
    elif failed or pending:
        completion = "blocked" if failed else "partially_completed"
    else:
        completion = "completed"
    refs = [item for item in job.get("patch_references") or [] if item.get("status") == "applied"]
    changed = sorted({path for item in refs for path in item.get("file_set", [])})[:50]
    commands = [item for item in job.get("command_references") or []]
    builder_timestamp = completed_at or _now()
    data = {
        "handoff_id": handoff_id or uuid4().hex, "delivery_job_id": job["delivery_job_id"],
        "original_objective": specification.normalized_objective,
        "final_specification_hash": specification.specification_hash,
        "implemented_requirements": specification.in_scope_requirements if completion == "completed" else [],
        "excluded_or_deferred_items": specification.explicit_exclusions,
        "changed_files": changed, "created_files": [], "removed_files": [],
        "database_or_configuration_changes": [path for path in changed if "migration" in path.lower() or "config" in path.lower()],
        "tests_added_or_changed": [path for path in changed if _is_test_path(path)],
        "validation_commands_and_outcomes": [f"{item.get('action', 'validation')}: {item.get('status', 'recorded')}" for item in commands],
        "acceptance_criterion_results": {item.criterion_id: latest.get(item.criterion_id, VerificationState.PENDING.value) for item in specification.acceptance_criteria},
        "repair_cycles_used": int(job.get("repair_cycles_used", 0)),
        "rollback_available": bool(refs),
        "setup_or_run_instructions": [str(item.get("verified_instruction")) for item in commands if item.get("verified_instruction")],
        "known_limitations": [item.requirement for item in failed + pending],
        "manual_checks_still_required": [item.requirement for item in manual],
        "relevant_hashes": {
            "specification": specification.specification_hash,
            "plan": _plan_revision(job).content_hash,
            "project_state": str(job.get("project_state_hash") or ""),
        },
        "verifier_result_ids": [str(item["verifier_result_id"]) for item in fresh_records if item.get("verifier_result_id")][:30],
        "completion_status": completion, "completed_at": builder_timestamp,
    }
    data["handoff_hash"] = "0" * 64
    normalized = HandoffReport.model_validate(data).model_dump(mode="json")
    normalized.pop("handoff_hash", None)
    data["handoff_hash"] = immutable_hash(normalized)
    handoff = HandoffReport.model_validate(data)
    updated = _copy(job)
    updated["handoff"] = handoff.model_dump(mode="json")
    updated["status"] = {
        "completed": DeliveryStatus.COMPLETED.value,
        "partially_completed": DeliveryStatus.PARTIAL.value,
        "blocked": DeliveryStatus.BLOCKED.value,
        "awaiting_manual_verification": DeliveryStatus.AWAITING_MANUAL.value,
    }[completion]
    updated["completed_at"] = builder_timestamp if completion == "completed" else None
    updated["updated_at"] = builder_timestamp
    return updated


def cancel_delivery(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("status") == DeliveryStatus.COMPLETED.value:
        raise ProjectDeliveryError("A completed delivery is immutable; create a follow-up job or roll it back.", code="terminal_state")
    if job.get("status") == DeliveryStatus.CANCELLED.value:
        return job
    updated = _copy(job)
    updated.update({"status": DeliveryStatus.CANCELLED.value, "cancelled_at": _now(), "updated_at": _now()})
    return updated


def public_delivery_job(job: dict[str, Any]) -> dict[str, Any]:
    excluded = {"analysis_index"}
    return {key: value for key, value in job.items() if key not in excluded}


def stage6_analysis_hash(index: dict[str, Any]) -> str:
    structural = {
        "index_version": index.get("index_version"),
        "root_fingerprint": index.get("root_fingerprint"),
        "files": sorted(file_hashes(index).items()),
        "relationships": sorted(
            [
                {key: item.get(key) for key in sorted(item) if key not in {"created_at"}}
                for item in index.get("relationships", []) if isinstance(item, dict)
            ],
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        ),
    }
    return immutable_hash(structural)


def immutable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _deterministic_specification_data(user_request: str, index: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any] | None:
    paths = _planned_paths_from_request(user_request, index, analysis)
    if not _CLEAR_ACTION.search(user_request) or not paths:
        return None
    lowered = user_request.lower()
    criteria: list[dict[str, Any]] = []
    mode = VerificationMode.STRUCTURAL
    if any(word in lowered for word in ("test", "failing", "pytest")):
        mode = VerificationMode.EXISTING_TEST
    elif "type" in lowered and any(Path(path).suffix in {".ts", ".tsx"} for path in paths):
        mode = VerificationMode.TYPE_CHECK
    criteria.append(AcceptanceCriterion(
        criterion_id="criterion-01", requirement=_bounded(user_request.strip(), 1000), required=True,
        verification_mode=mode, expected_evidence_type="approved command result" if mode != VerificationMode.STRUCTURAL else "fresh Stage 6 structural reference",
    ).model_dump(mode="json"))
    criteria.append(AcceptanceCriterion(
        criterion_id="criterion-02", requirement="No files outside the approved work-unit scope are changed.", required=True,
        verification_mode=VerificationMode.EXACT_ASSERTION, expected_evidence_type="planned-to-actual diff and file hashes",
    ).model_dump(mode="json"))
    required_methods = list(dict.fromkeys([item["verification_mode"] for item in criteria]))
    deliverables = [f"Create or update {path}." for path in paths]
    return {
        "objective": _bounded(user_request.strip().rstrip("."), 1000),
        "requirements": [_bounded(user_request.strip(), 1000)],
        "exclusions": ["Deployment, credentials, network access, Git writes, and unplanned dependency changes."],
        "assumptions": [],
        "deliverables": deliverables,
        "criteria": criteria, "required_methods": required_methods,
        "optional_methods": [VerificationMode.MANUAL.value],
        "evidence": paths[:20], "clarifications": [],
    }


def _clarification_specification_data(user_request: str, analysis: dict[str, Any], question: str) -> dict[str, Any]:
    paths = [str(item.get("relative_path")) for item in analysis.get("coherent_file_set", []) if item.get("relative_path")][:20]
    return {
        "objective": _bounded(user_request.strip(), 1000) or "Clarify the bounded project task.",
        "requirements": ["Resolve the material ambiguity before implementation planning."],
        "exclusions": ["No file modification or command execution before clarification and plan approval."],
        "assumptions": [], "deliverables": ["A bounded, verifiable project deliverable."],
        "criteria": [AcceptanceCriterion(
            criterion_id="criterion-01", requirement="The clarified objective is implemented and independently verified.",
            verification_mode=VerificationMode.STRUCTURAL, expected_evidence_type="fresh Stage 6 structural reference",
        ).model_dump(mode="json")],
        "evidence": paths, "clarifications": [question],
    }


def _make_specification(
    *, delivery_job_id: str, conversation_id: str, workspace_id: str, original_request: str,
    data: dict[str, Any], source: str, provider_status: str, revision: int,
) -> TaskSpecification:
    criteria = [AcceptanceCriterion.model_validate(item) for item in data["criteria"]]
    required = data.get("required_methods") or list(dict.fromkeys(item.verification_mode for item in criteria if item.required))
    optional = data.get("optional_methods") or list(dict.fromkeys(item.verification_mode for item in criteria if not item.required))
    value = {
        "contract_version": SPECIFICATION_VERSION, "task_id": uuid4().hex,
        "delivery_job_id": delivery_job_id, "conversation_id": conversation_id,
        "workspace_id": workspace_id, "original_user_request": _bounded(original_request, 3000),
        "normalized_objective": _bounded(data["objective"], 1000),
        "in_scope_requirements": list(data["requirements"])[:20],
        "explicit_exclusions": list(data.get("exclusions") or [])[:20],
        "assumptions": list(data.get("assumptions") or [])[:20],
        "constraints": [
            "Stay inside the authorized workspace.", "Every patch requires exact approval.",
            "Every executable command requires separate approval.", "Model output is untrusted data.",
        ],
        "requested_deliverables": list(data["deliverables"])[:20],
        "acceptance_criteria": [item.model_dump(mode="json") for item in criteria],
        "required_verification_methods": [VerificationMode(item).value for item in required][:20],
        "optional_verification_methods": [VerificationMode(item).value for item in optional][:20],
        "known_risks": ["Repository evidence may change after planning and make approvals stale."],
        "clarification_questions": [str(item) for item in data.get("clarifications") or [] if item][:3],
        "specification_source": source, "provider_status": _bounded(provider_status, 80),
        "evidence_references": [safe_relative_path(str(item)) for item in data.get("evidence") or []][:20],
        "revision": revision, "created_at": _now(),
    }
    value["specification_hash"] = immutable_hash(value)
    return TaskSpecification.model_validate(value)


def _bounded_model_request(user_request: str, index: dict[str, Any], analysis: dict[str, Any], limits: DeliveryLimits) -> dict[str, Any]:
    evidence = []
    for item in index.get("files", [])[:limits.max_model_evidence_items]:
        evidence.append({
            "relative_path": item.get("relative_path"), "file_hash": item.get("file_hash"),
            "language": item.get("language"), "parse_status": item.get("parse_status"),
            "symbols": [{"name": symbol.get("name"), "kind": symbol.get("kind")} for symbol in item.get("symbols", [])[:12]],
        })
    return {
        "contract_version": "astra.project-delivery.model-specification.request.v1",
        "request_id": uuid4().hex, "untrusted_user_request": _redact(_bounded(user_request, 3000)),
        "bounded_structural_evidence": evidence,
        "analysis_confidence": analysis.get("confidence", {}),
        "required_response_contract": "astra.project-delivery.model-specification.v1",
        "safety": ["Do not authorize actions.", "Do not return source code or commands.", "Return one strict JSON object."],
    }


def _request_paths(value: str) -> list[str]:
    paths = []
    for token in _PATH_TOKEN.findall(value or ""):
        try:
            paths.append(safe_relative_path(token.rstrip(".,;:!?")))
        except Exception:
            continue
    return list(dict.fromkeys(paths))[:20]


def _planned_paths_from_request(user_request: str, index: dict[str, Any], analysis: dict[str, Any]) -> list[str]:
    known = {str(item.get("relative_path")) for item in index.get("files", [])}
    inferred_outputs = _artifact_output_paths(user_request)
    if inferred_outputs:
        return inferred_outputs[:30]
    explicit = _request_paths(user_request)
    if explicit:
        return explicit
    return [str(item.get("relative_path")) for item in analysis.get("coherent_file_set", []) if item.get("relative_path") in known][:30]


def _artifact_output_paths(user_request: str) -> list[str]:
    lowered = user_request.lower()
    explicit = [*_request_paths(user_request), *_bare_file_paths(user_request)]
    data_suffixes = {".csv", ".json", ".parquet", ".tsv", ".xls", ".xlsx"}
    artifact_suffixes = {".py", ".md", ".png", ".jpg", ".jpeg", ".svg", ".html", ".htm", ".pdf", ".ipynb"}
    explicit_outputs = [path for path in explicit if Path(path).suffix.lower() in artifact_suffixes]
    asks_for_artifacts = bool(re.search(r"\b(?:script|charts?|plots?|reports?|notebooks?|png|markdown)\b", lowered))
    if not asks_for_artifacts:
        return []

    data_paths = [path for path in explicit if Path(path).suffix.lower() in data_suffixes]
    base = Path(data_paths[0]).stem if data_paths else "project"
    base = re.sub(r"[^a-z0-9_-]+", "_", base.lower()).strip("_") or "project"
    outputs = list(explicit_outputs)
    if re.search(r"\b(?:python\s+)?script\b", lowered) and not any(Path(path).suffix.lower() == ".py" for path in outputs):
        outputs.append(f"{base}_analysis.py")
    if re.search(r"\b(?:png\s+)?(?:charts?|plots?)\b|\bpng\b", lowered) and not any(Path(path).suffix.lower() == ".png" for path in outputs):
        count_match = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight)\s+(?:png\s+)?(?:charts?|plots?)\b", lowered)
        counts = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
        raw_count = count_match.group(1) if count_match else "1"
        chart_count = max(1, min(12, int(raw_count) if raw_count.isdigit() else counts[raw_count]))
        outputs.extend(f"{base}_chart_{index}.png" for index in range(1, chart_count + 1))
    if re.search(r"\b(?:markdown\s+)?report\b", lowered) and not any(Path(path).suffix.lower() == ".md" for path in outputs):
        outputs.append(f"{base}_report.md")
    return list(dict.fromkeys(outputs))


def _bare_file_paths(value: str) -> list[str]:
    paths = []
    for token in _FILE_TOKEN.findall(value or ""):
        try:
            paths.append(safe_relative_path(token.rstrip(".,;:!?")))
        except Exception:
            continue
    return list(dict.fromkeys(paths))[:20]


def _planned_paths(specification: TaskSpecification, index: dict[str, Any], analysis: dict[str, Any]) -> list[str]:
    known = {str(item.get("relative_path")) for item in index.get("files", [])}
    paths = [path for path in specification.evidence_references if path in known]
    if not paths:
        paths = _planned_paths_from_request(specification.original_user_request, index, analysis)
    for path in paths:
        safe_relative_path(path)
    return list(dict.fromkeys(paths))[:30]


def _clarification_question(user_request: str, analysis: dict[str, Any]) -> str:
    lowered = user_request.lower()
    for term, question in _MATERIAL_TERMS.items():
        excluded = re.search(
            rf"\b(?:do\s+not|don't|must\s+not|without|no)\b[^.!?]{{0,120}}\b{re.escape(term)}(?:ment|ion|ions|ed|ing)?\b",
            lowered,
        )
        if term in lowered and excluded is None:
            return question
    if not analysis.get("coherent_file_set"):
        return "Which project-relative component or file should this delivery target?"
    return "What exact observable behavior should the completed change produce?"


def _symbols_for_paths(analysis: dict[str, Any], paths: list[str]) -> list[str]:
    selected = set(paths)
    return list(dict.fromkeys(
        f"{item.get('relative_path')}:{item.get('name')}" for item in analysis.get("relevant_symbols", [])
        if item.get("relative_path") in selected and item.get("name")
    ))[:30]


def _validate_dependencies(units: list[WorkUnit]) -> None:
    ids = {unit.work_unit_id for unit in units}
    if any(set(unit.dependencies) - ids for unit in units):
        raise ProjectDeliveryError("A work unit references an unknown dependency.", code="invalid_plan")
    graph = {unit.work_unit_id: unit.dependencies for unit in units}
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node: str) -> None:
        if node in visiting:
            raise ProjectDeliveryError("Cyclic work-unit dependencies are not allowed.", code="cyclic_plan")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)
    for node in graph:
        visit(node)


def _replace_plan_units(plan: ExecutionPlan, units: list[WorkUnit]) -> ExecutionPlan:
    value = plan.model_dump(mode="json")
    value["work_units"] = [unit.model_dump(mode="json") for unit in units]
    return ExecutionPlan.model_validate(value)


def _set_unit_status(job: dict[str, Any], unit_id: str, status: WorkUnitStatus) -> dict[str, Any]:
    plan = _plan(job)
    units = []
    found = False
    for unit in plan.work_units:
        if unit.work_unit_id == unit_id:
            value = unit.model_dump(mode="json")
            value["status"] = status.value
            unit = WorkUnit.model_validate(value)
            found = True
        units.append(unit)
    if not found:
        raise ProjectDeliveryError("Unknown work unit.", code="unknown_work_unit")
    updated = _copy(job)
    updated["plan"] = _replace_plan_units(plan, units).model_dump(mode="json")
    states = _execution_state_map(updated)
    current = states[unit_id]
    target = _runtime_status(status)
    state_update: dict[str, Any] = {
        "status": target,
        "state_version": current.state_version + 1,
    }
    if target == WorkUnitExecutionStatus.ACTIVE and current.status != WorkUnitExecutionStatus.ACTIVE:
        state_update.update({
            "attempt_count": current.attempt_count + 1,
            "active_attempt_id": uuid4().hex,
            "started_at": datetime.now(timezone.utc),
            "completed_at": None,
        })
    if target in {WorkUnitExecutionStatus.COMPLETED, WorkUnitExecutionStatus.FAILED, WorkUnitExecutionStatus.CANCELLED, WorkUnitExecutionStatus.ROLLED_BACK}:
        state_update.update({"completed_at": datetime.now(timezone.utc), "active_attempt_id": None})
    states[unit_id] = current.model_copy(update=state_update)
    updated["work_unit_execution_states"] = [
        states[item].model_dump(mode="json") for item in _plan_revision(updated).ordered_work_unit_ids
    ]
    return updated


def _active_unit(job: dict[str, Any]) -> WorkUnit:
    unit_id = job.get("active_work_unit_id")
    unit = next((item for item in _plan(job).work_units if item.work_unit_id == unit_id), None)
    if unit is None:
        raise ProjectDeliveryError("No active work unit is available.", code="invalid_state")
    return unit


def _recalculate_progress(job: dict[str, Any]) -> dict[str, Any]:
    latest = _latest_criterion_states(job)
    plan = _plan(job)
    updated = _copy(job)
    units: list[WorkUnit] = []
    for unit in plan.work_units:
        states = [latest.get(item) for item in unit.criterion_references]
        if states and all(item in {VerificationState.SATISFIED.value, VerificationState.WAIVED.value} for item in states):
            value = unit.model_dump(mode="json")
            value["status"] = WorkUnitStatus.SATISFIED.value
            unit = WorkUnit.model_validate(value)
        units.append(unit)
    updated["plan"] = _replace_plan_units(plan, units).model_dump(mode="json")
    for unit in units:
        if unit.status == WorkUnitStatus.SATISFIED:
            updated = _set_unit_status(updated, unit.work_unit_id, WorkUnitStatus.SATISFIED)
    active = updated.get("active_work_unit_id")
    if any(unit.work_unit_id == active and unit.status == WorkUnitStatus.SATISFIED for unit in units):
        updated["active_work_unit_id"] = None
        updated["status"] = DeliveryStatus.PLAN_APPROVED.value
    return updated


def _latest_criterion_states(job: dict[str, Any]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for record in job.get("verification_records") or []:
        if isinstance(record, dict) and record.get("criterion_id"):
            latest[str(record["criterion_id"])] = str(record.get("state") or VerificationState.PENDING.value)
    return latest


def _fresh_verification_records(job: dict[str, Any]) -> list[dict[str, Any]]:
    revision = _plan_revision(job)
    current_manifest = str(job.get("project_state_hash") or "")
    results = {
        str(item.get("verifier_result_id")): item
        for item in job.get("verifier_results") or [] if isinstance(item, dict)
    }
    specification = TaskSpecification.model_validate(job["specification"])
    criteria = {item.criterion_id: item for item in specification.acceptance_criteria}
    fresh: list[dict[str, Any]] = []
    for record in job.get("verification_records") or []:
        if not isinstance(record, dict):
            continue
        if record.get("state") in {VerificationState.FAILED.value, VerificationState.BLOCKED.value, VerificationState.MANUAL.value} and not record.get("verifier_result_id"):
            fresh.append(record)
            continue
        result = results.get(str(record.get("verifier_result_id") or ""))
        if not result:
            continue
        try:
            typed_result = VerifierResult.model_validate(result)
        except ValueError:
            continue
        result_payload = typed_result.model_dump(mode="json")
        claimed_result_hash = result_payload.pop("result_hash", None)
        criterion = criteria.get(str(record.get("criterion_id") or ""))
        if (
            record.get("plan_revision_id") == revision.plan_revision_id
            and record.get("input_manifest_hash") == current_manifest
            and typed_result.plan_revision_id == revision.plan_revision_id
            and typed_result.input_manifest_hash == current_manifest
            and typed_result.outcome == VerifierOutcome.PASSED
            and criterion is not None
            and typed_result.criterion_definition_hash == immutable_hash(criterion.model_dump(mode="json"))
            and all(item.outcome == "passed" for item in typed_result.performed_checks)
            and record.get("verifier_result_hash") == typed_result.result_hash
            and claimed_result_hash == immutable_hash(result_payload)
        ):
            fresh.append(record)
    return fresh


def _plan(job: dict[str, Any]) -> ExecutionPlan:
    if not isinstance(job.get("plan"), dict):
        raise ProjectDeliveryError("This delivery job does not have an execution plan.", code="missing_plan")
    return ExecutionPlan.model_validate(job["plan"])


def _plan_revision(job: dict[str, Any]) -> ExecutionPlanRevision:
    value = job.get("plan_revision")
    if not isinstance(value, dict):
        if isinstance(job.get("plan_approval"), dict):
            raise ProjectDeliveryError(
                "This legacy plan approval predates immutable plan revisions and must be approved again.",
                code="migration_reapproval_required",
            )
        raise ProjectDeliveryError("This delivery job has no immutable plan revision.", code="missing_plan_revision")
    revision = ExecutionPlanRevision.model_validate(value)
    if revision.content_hash != immutable_hash(_immutable_plan_payload(_plan(job).model_dump(mode="json"))):
        raise ProjectDeliveryError("The immutable plan revision content hash is invalid.", code="stale_plan_approval")
    return revision


def _install_plan_revision(
    job: dict[str, Any], plan: ExecutionPlan, specification: TaskSpecification,
    *, supersedes: str | None = None,
) -> dict[str, Any]:
    updated = _copy(job)
    payload = _immutable_plan_payload(plan.model_dump(mode="json"))
    digest = immutable_hash(payload)
    if plan.plan_hash != digest:
        plan_value = plan.model_dump(mode="json")
        plan_value["plan_hash"] = digest
        plan = ExecutionPlan.model_validate(plan_value)
        updated["plan"] = plan.model_dump(mode="json")
    revision_id = _stable_id("plan-revision", job["delivery_job_id"], str(plan.plan_revision), digest)
    definitions = tuple(
        ImmutableWorkUnitDefinition(
            work_unit_id=unit.work_unit_id, title=unit.title, objective=unit.objective,
            requirement_references=tuple(unit.requirement_references),
            acceptance_criteria_ids=tuple(unit.criterion_references),
            dependencies=tuple(unit.dependencies), expected_files=tuple(unit.expected_files),
            expected_symbols=tuple(unit.expected_symbols), bounded_inputs=tuple(unit.expected_files),
            bounded_outputs=tuple(unit.expected_files), expected_patch_scope=unit.expected_patch_scope,
            expected_validation_commands=tuple(unit.expected_validation_commands),
            completion_conditions=tuple(unit.completion_conditions), risk_level=unit.risk_level,
            clarification_may_be_required=unit.clarification_may_be_required,
        ) for unit in plan.work_units
    )
    revision = ExecutionPlanRevision(
        plan_revision_id=revision_id, project_run_id=job["delivery_job_id"],
        revision_number=plan.plan_revision,
        task_specification_revision_id=f"{specification.task_id}:r{specification.revision}",
        specification_hash=plan.specification_hash, stage6_analysis_hash=plan.stage6_analysis_hash,
        work_units=definitions, ordered_work_unit_ids=tuple(plan.ordered_work_unit_ids),
        dependencies=tuple((key, tuple(plan.dependency_graph[key])) for key in sorted(plan.dependency_graph)),
        acceptance_criteria=tuple((key, tuple(plan.acceptance_criterion_mapping[key])) for key in sorted(plan.acceptance_criterion_mapping)),
        estimated_patch_count=plan.estimated_patch_count,
        estimated_command_count=plan.estimated_command_count,
        explicit_exclusions=tuple(plan.explicit_exclusions), plan_source=plan.plan_source,
        confidence=plan.confidence, confidence_reasons=tuple(plan.confidence_reasons),
        created_at=datetime.now(timezone.utc), content_hash=digest,
        supersedes_revision_id=supersedes,
    )
    states = [
        WorkUnitExecutionState(
            project_run_id=job["delivery_job_id"], plan_revision_id=revision_id,
            work_unit_id=unit.work_unit_id,
            status=WorkUnitExecutionStatus.READY if index == 0 else WorkUnitExecutionStatus.PENDING,
            attempt_count=0, state_version=1,
        ).model_dump(mode="json")
        for index, unit in enumerate(definitions)
    ]
    history = list(updated.get("plan_revision_history") or [])
    if not any(item.get("plan_revision_id") == revision_id for item in history if isinstance(item, dict)):
        history.append(revision.model_dump(mode="json"))
    updated.update({
        "plan_revision": revision.model_dump(mode="json"),
        "plan_revision_history": history,
        "work_unit_execution_states": states,
        "plan_approval": None,
    })
    return updated


def _immutable_plan_payload(plan: dict[str, Any]) -> dict[str, Any]:
    units = []
    for raw in plan.get("work_units") or []:
        unit = dict(raw)
        unit.pop("status", None)
        unit.pop("work_unit_hash", None)
        for key in ("requirement_references", "criterion_references", "dependencies", "expected_files", "expected_symbols", "expected_validation_commands", "completion_conditions"):
            if key in unit:
                unit[key] = list(unit[key])
        units.append(unit)
    return {
        "schema_version": "astra.project-delivery.plan-content.v2",
        "project_run_id": plan.get("delivery_job_id"),
        "revision_number": plan.get("plan_revision"),
        "specification_hash": plan.get("specification_hash"),
        "stage6_analysis_hash": plan.get("stage6_analysis_hash"),
        "work_units": units,
        "ordered_work_unit_ids": list(plan.get("ordered_work_unit_ids") or []),
        "dependency_graph": {key: list(value) for key, value in sorted((plan.get("dependency_graph") or {}).items())},
        "acceptance_criterion_mapping": {key: list(value) for key, value in sorted((plan.get("acceptance_criterion_mapping") or {}).items())},
        "estimated_patch_count": plan.get("estimated_patch_count"),
        "estimated_command_count": plan.get("estimated_command_count"),
        "explicit_exclusions": list(plan.get("explicit_exclusions") or []),
        "plan_source": plan.get("plan_source"),
        "confidence": plan.get("confidence"),
        "confidence_reasons": list(plan.get("confidence_reasons") or []),
    }


def _execution_state_map(job: dict[str, Any]) -> dict[str, WorkUnitExecutionState]:
    revision = _plan_revision(job)
    values = {
        str(item.get("work_unit_id")): WorkUnitExecutionState.model_validate(item)
        for item in job.get("work_unit_execution_states") or [] if isinstance(item, dict)
    }
    missing = set(revision.ordered_work_unit_ids) - set(values)
    if missing:
        raise ProjectDeliveryError(f"Work-unit execution state is missing for {sorted(missing)}.", code="invalid_runtime_state")
    if any(item.plan_revision_id != revision.plan_revision_id for item in values.values()):
        raise ProjectDeliveryError("Work-unit execution state belongs to a different plan revision.", code="stale_runtime_state")
    return values


def _runtime_status(status: WorkUnitStatus) -> WorkUnitExecutionStatus:
    return {
        WorkUnitStatus.PENDING: WorkUnitExecutionStatus.PENDING,
        WorkUnitStatus.READY: WorkUnitExecutionStatus.READY,
        WorkUnitStatus.PREPARING: WorkUnitExecutionStatus.ACTIVE,
        WorkUnitStatus.AWAITING_PATCH: WorkUnitExecutionStatus.ACTIVE,
        WorkUnitStatus.APPLIED: WorkUnitExecutionStatus.ACTIVE,
        WorkUnitStatus.VERIFYING: WorkUnitExecutionStatus.ACTIVE,
        WorkUnitStatus.SATISFIED: WorkUnitExecutionStatus.COMPLETED,
        WorkUnitStatus.BLOCKED: WorkUnitExecutionStatus.BLOCKED,
        WorkUnitStatus.ROLLED_BACK: WorkUnitExecutionStatus.ROLLED_BACK,
    }[status]


def _limit_reached(job: dict[str, Any], code: str) -> dict[str, Any]:
    updated = _copy(job)
    updated.update({
        "status": DeliveryStatus.LIMIT_REACHED.value,
        "last_error": {"code": code, "message": "A configured Stage 9 limit was reached; no action continued."},
        "updated_at": _now(),
    })
    return updated


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, default=str))


def _is_test_path(path: str) -> bool:
    name = Path(path).name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name or "/tests/" in f"/{path.lower()}"


def _stable_id(*parts: str) -> str:
    return immutable_hash(parts)[:32]


def _redact(value: str) -> str:
    result = re.sub(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+", r"\1[REDACTED]", value)
    result = re.sub(r"(?i)\b(api[_-]?key|token|password|secret)\s*[=:]\s*[^\s,;]+", r"\1=[REDACTED]", result)
    result = re.sub(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[REDACTED]@", result)
    return result


def _bounded(value: str, limit: int) -> str:
    return " ".join(str(value).split())[:limit]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ProjectDeliveryError", "adapt_legacy_delivery_job", "activate_next_work_unit", "approve_plan", "build_execution_plan",
    "cancel_delivery", "create_delivery_job", "generate_handoff", "immutable_hash",
    "link_patch_preview", "public_delivery_job", "record_patch_applied", "record_rollback",
    "record_scope_change", "record_verification", "stage6_analysis_hash", "submit_clarification",
    "revise_scope",
]
