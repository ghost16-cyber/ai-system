from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from backend.app.folders.safety import project_root_fingerprint, safe_relative_path
from backend.app.project_analysis import build_analysis_plan, build_project_index, file_hashes
from backend.app.project_analysis.diagnosis import project_state_hash
from backend.app.project_analysis.model_synthesis import SynthesisGateway
from backend.app.project_analysis.model_synthesis.gateway import SynthesisGatewayError
from backend.app.project_delivery.contracts import (
    PLAN_VERSION,
    SPECIFICATION_VERSION,
    AcceptanceCriterion,
    DeliveryStatus,
    ExecutionPlan,
    HandoffReport,
    ScopeChange,
    TaskSpecification,
    VerificationMode,
    VerificationRecord,
    VerificationState,
    WorkUnit,
    WorkUnitStatus,
    parse_model_specification,
)
from backend.app.project_delivery.limits import DeliveryLimits, STAGE9_LIMITS


class ProjectDeliveryError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_transition") -> None:
        super().__init__(message)
        self.code = code


_CLEAR_ACTION = re.compile(
    r"\b(add(?:ing)?|chang(?:e|ing)|fix(?:ing)?|implement(?:ing)?|updat(?:e|ing)|"
    r"remov(?:e|ing)|renam(?:e|ing)|validat(?:e|ing)|test(?:ing)?|refactor(?:ing)?)\b",
    re.I,
)
_PATH_TOKEN = re.compile(r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")
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
) -> dict[str, Any]:
    approved = Path(root).resolve()
    delivery_job_id = uuid4().hex
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
    plan = None if clarification_questions else build_execution_plan(specification, index, analysis, limits=limits)
    status = DeliveryStatus.CLARIFICATION if clarification_questions else DeliveryStatus.AWAITING_PLAN_APPROVAL
    now = _now()
    return {
        "delivery_job_id": delivery_job_id,
        "action_run_id": action_run_id,
        "conversation_id": conversation_id,
        "folder_access_id": folder_access_id,
        "root_fingerprint": project_root_fingerprint(approved),
        "project_state_hash": project_state_hash(approved),
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
        "plan_approval": None,
        "clarifications": ([{
            "clarification_id": _stable_id("clarification", delivery_job_id, clarification_questions[0]),
            "question": clarification_questions[0], "answer": None, "status": "pending", "created_at": now,
        }] if clarification_questions else []),
        "active_work_unit_id": None,
        "patch_references": [],
        "command_references": [],
        "verification_records": [],
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


def build_execution_plan(
    specification: TaskSpecification,
    index: dict[str, Any],
    analysis: dict[str, Any],
    *,
    limits: DeliveryLimits = STAGE9_LIMITS,
    revision: int = 1,
) -> ExecutionPlan:
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
        data["work_unit_hash"] = immutable_hash(data)
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
        "created_at": _now(),
    }
    data["plan_hash"] = immutable_hash(data)
    return ExecutionPlan.model_validate(data)


def approve_plan(job: dict[str, Any], *, plan_hash: str, actor: str = "user") -> dict[str, Any]:
    plan = _plan(job)
    if plan.plan_hash != plan_hash:
        raise ProjectDeliveryError("The exact current plan hash is required.", code="hash_mismatch")
    approval = job.get("plan_approval")
    if isinstance(approval, dict) and approval.get("plan_hash") == plan_hash:
        return job
    if job.get("status") != DeliveryStatus.AWAITING_PLAN_APPROVAL.value:
        raise ProjectDeliveryError("This delivery job is not awaiting plan approval.", code="invalid_state")
    updated = _copy(job)
    updated["plan_approval"] = {
        "approval_id": _stable_id("plan-approval", job["delivery_job_id"], plan_hash),
        "plan_hash": plan_hash, "specification_hash": plan.specification_hash,
        "approved_by": _bounded(actor, 80), "approved_at": _now(),
        "authority": "prepare_work_units_only",
    }
    updated["status"] = DeliveryStatus.PLAN_APPROVED.value
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
    updated.update({
        "specification": spec.model_dump(mode="json"),
        "specification_revisions": [*updated.get("specification_revisions", []), spec.model_dump(mode="json")],
        "plan": plan.model_dump(mode="json"),
        "plan_revisions": [*updated.get("plan_revisions", []), plan.model_dump(mode="json")],
        "plan_approval": None, "clarifications": clarifications,
        "analysis_id": index["analysis_id"], "analysis_hash": stage6_analysis_hash(index),
        "analysis_index": index, "analysis": analysis,
        "project_state_hash": project_state_hash(root),
        "status": DeliveryStatus.AWAITING_PLAN_APPROVAL.value, "updated_at": _now(),
    })
    return updated


def activate_next_work_unit(job: dict[str, Any], *, root: str | Path) -> dict[str, Any]:
    plan = _plan(job)
    approval = job.get("plan_approval") or {}
    if approval.get("plan_hash") != plan.plan_hash:
        raise ProjectDeliveryError("Approve the exact current plan before preparing work.", code="approval_required")
    if job.get("active_work_unit_id"):
        raise ProjectDeliveryError("Only one work unit may be active at a time.", code="conflict")
    if job.get("status") not in {DeliveryStatus.PLAN_APPROVED.value, DeliveryStatus.PATCH_APPLIED.value}:
        raise ProjectDeliveryError("The delivery job is not ready to activate a work unit.", code="invalid_state")
    current_state = project_state_hash(root)
    if current_state != job.get("project_state_hash"):
        raise ProjectDeliveryError("The project changed after planning; replan from fresh analysis.", code="stale_repository")
    fresh = build_project_index(
        root, conversation_id=job["conversation_id"], folder_access_id=job["folder_access_id"],
        job_id=job["delivery_job_id"], previous=job.get("analysis_index"),
    )
    if stage6_analysis_hash(fresh) != plan.stage6_analysis_hash:
        raise ProjectDeliveryError("Stage 6 structural evidence changed after plan approval.", code="stale_analysis")
    units = [unit.model_copy(deep=True) for unit in plan.work_units]
    completed = {unit.work_unit_id for unit in units if unit.status == WorkUnitStatus.SATISFIED}
    selected = next((unit for unit in units if unit.status in {WorkUnitStatus.READY, WorkUnitStatus.PENDING} and set(unit.dependencies) <= completed), None)
    if selected is None:
        raise ProjectDeliveryError("No dependency-safe pending work unit is available.", code="blocked_dependency")
    updated_units = []
    for unit in units:
        if unit.work_unit_id == selected.work_unit_id:
            payload = unit.model_dump(mode="json")
            payload["status"] = WorkUnitStatus.PREPARING.value
            unit = WorkUnit.model_validate(payload)
        updated_units.append(unit)
    updated_plan = _replace_plan_units(plan, updated_units)
    updated = _copy(job)
    updated.update({
        "plan": updated_plan.model_dump(mode="json"), "active_work_unit_id": selected.work_unit_id,
        "analysis_id": fresh["analysis_id"], "analysis_index": fresh,
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
    if state == VerificationState.SATISFIED:
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
        "verified_at": _now(),
    }
    data["verification_hash"] = immutable_hash(data)
    record = VerificationRecord.model_validate(data)
    updated = _copy(job)
    updated["verification_records"] = [*updated.get("verification_records", []), record.model_dump(mode="json")]
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
    updated.update({
        "specification": spec.model_dump(mode="json"),
        "specification_revisions": [*updated.get("specification_revisions", []), spec.model_dump(mode="json")],
        "plan": plan.model_dump(mode="json"),
        "plan_revisions": [*updated.get("plan_revisions", []), plan.model_dump(mode="json")],
        "plan_approval": None, "active_work_unit_id": None,
        "analysis_id": index["analysis_id"], "analysis_hash": stage6_analysis_hash(index),
        "analysis_index": index, "analysis": analysis,
        "project_state_hash": project_state_hash(root),
        "status": DeliveryStatus.AWAITING_PLAN_APPROVAL.value, "updated_at": _now(),
    })
    return updated


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


def generate_handoff(job: dict[str, Any]) -> dict[str, Any]:
    if job.get("handoff"):
        return job
    specification = TaskSpecification.model_validate(job["specification"])
    latest = _latest_criterion_states(job)
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
    data = {
        "handoff_id": uuid4().hex, "delivery_job_id": job["delivery_job_id"],
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
            "plan": _plan(job).plan_hash,
            "project_state": str(job.get("project_state_hash") or ""),
        },
        "completion_status": completion, "completed_at": _now(),
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
    updated["completed_at"] = _now() if completion == "completed" else None
    updated["updated_at"] = _now()
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
    return {
        "objective": _bounded(user_request.strip().rstrip("."), 1000),
        "requirements": [_bounded(user_request.strip(), 1000)],
        "exclusions": ["Deployment, credentials, network access, Git writes, and unplanned dependency changes."],
        "assumptions": [],
        "deliverables": ["Approved project changes and evidence-backed acceptance verification."],
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
    explicit = [path for path in _request_paths(user_request) if path in known]
    if explicit:
        return explicit
    return [str(item.get("relative_path")) for item in analysis.get("coherent_file_set", []) if item.get("relative_path") in known][:30]


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
        if term in lowered:
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


def _plan(job: dict[str, Any]) -> ExecutionPlan:
    if not isinstance(job.get("plan"), dict):
        raise ProjectDeliveryError("This delivery job does not have an execution plan.", code="missing_plan")
    return ExecutionPlan.model_validate(job["plan"])


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
    "ProjectDeliveryError", "activate_next_work_unit", "approve_plan", "build_execution_plan",
    "cancel_delivery", "create_delivery_job", "generate_handoff", "immutable_hash",
    "link_patch_preview", "public_delivery_job", "record_patch_applied", "record_rollback",
    "record_scope_change", "record_verification", "stage6_analysis_hash", "submit_clarification",
    "revise_scope",
]
