from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlError,
    ProjectControlPlane,
    ProjectLifecycle,
)
from backend.app.project_control.contracts import content_hash
from tests.test_project_control import base, command


def _manual_pending(tmp_path: Path):
    control = ProjectControlPlane(tmp_path / "manual.db")
    control.initialize()
    control.execute(ProjectCommand(
        **base(), command_type=ProjectCommandType.INITIALIZE_PROJECT,
        expected_state_version=0, idempotency_key="initialize-manual",
    ))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.ATTACH_SPECIFICATION, run, "spec-manual", payload={
        "task_specification_id": "spec-manual", "specification_hash": content_hash({"manual": True}),
        "included_paths": ["app.py"], "allowed_operations": ["read", "verify"],
    }))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.REGISTER_MANIFEST, run, "manifest-manual", payload={
        "manifest_hash": content_hash({"app.py": "one"}), "complete": True,
    }))
    criterion = {"criterion_id": "manual-criterion", "required": True,
                 "verification_mode": "manual_user_verification_required", "description": "Observe UI"}
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.PROPOSE_PLAN_REVISION, run, "plan-manual", payload={
        "acceptance_criteria": [criterion],
        "work_units": [{"work_unit_id": "work-manual", "objective": "Prepare bounded output."}],
    }))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "approve-manual-plan",
                            authority={"operation": "prepare_work_units"}))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.BEGIN_WORK_UNIT, run, "begin-manual",
                            payload={"work_unit_id": "work-manual"}))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.REQUEST_VERIFICATION, run, "verify-manual",
                            payload={"criterion_id": "manual-criterion"}))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.RECORD_VERIFIER_RESULT, run, "manual-required", payload={
        "criterion_id": "manual-criterion", "criterion_hash": content_hash(criterion),
        "plan_revision_id": run.current_plan_revision_id,
        "scope_revision_id": run.current_scope_revision_id,
        "manifest_hash": run.current_manifest_hash,
        "result_hash": content_hash({"outcome": "manual_required"}),
        "outcome": "manual_required",
    }))
    return control, criterion


def _evidence_command(control: ProjectControlPlane, criterion: dict, key: str,
                      *, criterion_id: str = "manual-criterion", decision: str = "passed"):
    run = control.get_project("project-1")
    read_model = control.get_read_model("project-1")
    current = run.verification_state["manual-criterion"]
    evidence = {"kind": "observation_notes", "notes": "Observed expected UI state."}
    return command(ProjectCommandType.SUBMIT_MANUAL_EVIDENCE, run, key, payload={
        "criterion_id": criterion_id,
        "criterion_hash": content_hash(criterion),
        "work_unit_id": read_model.current_work_unit,
        "execution_attempt_id": read_model.active_execution_attempt_id,
        "verification_artifact_id": current.get("verification_artifact_id"),
        "plan_revision_id": run.current_plan_revision_id,
        "scope_revision_id": run.current_scope_revision_id,
        "manifest_hash": run.current_manifest_hash,
        "evidence_id": f"evidence-{key}", "evidence_hash": content_hash(evidence),
        "decision": decision, "evidence": evidence,
    }, authority={"criterion_id": criterion_id})


def test_manual_required_is_pending_evidence_not_failure(tmp_path: Path) -> None:
    control, _criterion = _manual_pending(tmp_path)
    run = control.get_project("project-1")
    assert run.lifecycle_status == ProjectLifecycle.VERIFICATION_PENDING
    assert run.pending_user_action == "submit_manual_evidence:manual-criterion"
    assert run.verification_state["manual-criterion"]["outcome"] == "manual_evidence_required"
    assert run.repair_state == {}
    assert control.get_read_model("project-1").handoff_eligible is False


def test_manual_evidence_satisfies_only_exact_criterion_and_replays(tmp_path: Path) -> None:
    control, criterion = _manual_pending(tmp_path)
    request = _evidence_command(control, criterion, "manual-proof")
    first = control.execute(request)
    replay = control.execute(request)
    assert replay.replayed is True
    assert replay.event_id == first.event_id
    run = control.get_project("project-1")
    assert run.verification_state["manual-criterion"]["outcome"] == "passed"
    assert run.verification_state["manual-criterion"]["evidence_id"] == "evidence-manual-proof"
    assert len([event for event in control.list_events("project-1") if event.request_id == "manual-proof"]) == 1


def test_manual_evidence_for_another_criterion_fails_closed(tmp_path: Path) -> None:
    control, criterion = _manual_pending(tmp_path)
    with pytest.raises(ProjectControlError):
        control.execute(_evidence_command(control, criterion, "wrong-criterion", criterion_id="criterion-b"))
    assert control.get_project("project-1").verification_state["manual-criterion"]["outcome"] == "manual_evidence_required"


def test_failed_manual_evidence_marks_only_the_criterion_without_repair(tmp_path: Path) -> None:
    control, criterion = _manual_pending(tmp_path)
    control.execute(_evidence_command(control, criterion, "manual-failed", decision="failed"))
    run = control.get_project("project-1")
    assert run.lifecycle_status == ProjectLifecycle.VERIFICATION_PENDING
    assert run.verification_state["manual-criterion"]["outcome"] == "failed"
    assert run.pending_user_action == "review_manual_failure:manual-criterion"
    assert run.repair_state == {}
