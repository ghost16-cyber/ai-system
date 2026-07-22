from __future__ import annotations

import json
import sqlite3
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


def _manual_started(tmp_path: Path, criteria: list[dict]):
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
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.PROPOSE_PLAN_REVISION, run, "plan-manual", payload={
        "acceptance_criteria": criteria,
        "work_units": [{"work_unit_id": "work-manual", "objective": "Prepare bounded output."}],
    }))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "approve-manual-plan",
                            authority={"operation": "prepare_work_units"}))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.BEGIN_WORK_UNIT, run, "begin-manual",
                            payload={"work_unit_id": "work-manual"}))
    return control


def _record_manual_required(
    control: ProjectControlPlane, criterion: dict, key: str
) -> None:
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.REQUEST_VERIFICATION, run, f"verify-{key}",
                            payload={"criterion_id": criterion["criterion_id"]}))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.RECORD_VERIFIER_RESULT, run, f"required-{key}", payload={
        "criterion_id": criterion["criterion_id"], "criterion_hash": content_hash(criterion),
        "plan_revision_id": run.current_plan_revision_id,
        "scope_revision_id": run.current_scope_revision_id,
        "manifest_hash": run.current_manifest_hash,
        "result_hash": content_hash({"outcome": "manual_required"}),
        "outcome": "manual_required",
    }))


def _manual_pending(tmp_path: Path):
    criterion = {"criterion_id": "manual-criterion", "required": True,
                 "verification_mode": "manual_user_verification_required", "description": "Observe UI"}
    control = _manual_started(tmp_path, [criterion])
    _record_manual_required(control, criterion, "manual")
    return control, criterion


def _evidence_command(control: ProjectControlPlane, criterion: dict, key: str,
                      *, criterion_id: str = "manual-criterion", decision: str = "passed"):
    run = control.get_project("project-1")
    read_model = control.get_read_model("project-1")
    current = (
        run.verification_state.get(criterion_id)
        or run.verification_state["manual-criterion"]
    )
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


def _criterion_evidence_command(
    control: ProjectControlPlane, criterion: dict, key: str, *, decision: str = "passed"
):
    return _evidence_command(
        control,
        criterion,
        key,
        criterion_id=str(criterion["criterion_id"]),
        decision=decision,
    )


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


def test_plan_revision_supersession_appends_one_immutable_invalidation(tmp_path: Path) -> None:
    control, criterion = _manual_pending(tmp_path)
    evidence_command = _evidence_command(control, criterion, "superseded-proof")
    original = control.execute(evidence_command)
    with sqlite3.connect(control.database_path) as connection:
        stored_before = connection.execute(
            "SELECT status, evidence_json, updated_at FROM project_manual_evidence "
            "WHERE evidence_id = 'evidence-superseded-proof'"
        ).fetchone()

    run = control.get_project("project-1")
    revise = command(ProjectCommandType.PROPOSE_PLAN_REVISION, run, "replacement-plan", payload={
        "acceptance_criteria": [criterion],
        "work_units": [{"work_unit_id": "replacement-work"}],
    })
    first = control.execute(revise)
    assert control.execute(revise).event_id == first.event_id

    with sqlite3.connect(control.database_path) as connection:
        stored_after = connection.execute(
            "SELECT status, evidence_json, updated_at FROM project_manual_evidence "
            "WHERE evidence_id = 'evidence-superseded-proof'"
        ).fetchone()
        invalidations = connection.execute(
            "SELECT invalidation_json FROM project_manual_evidence_invalidations "
            "WHERE evidence_id = 'evidence-superseded-proof'"
        ).fetchall()
    assert stored_after == stored_before
    assert len(invalidations) == 1
    invalidation = json.loads(invalidations[0][0])
    assert invalidation["criterion_id"] == "manual-criterion"
    assert invalidation["cause"] == "plan_revision_superseded"
    assert invalidation["superseding_plan_revision_id"] == first.read_model["plan_revision_id"]

    history = control.list_manual_evidence("project-1")
    assert history[0]["status"] == "verification_invalidated"
    assert history[0]["stored_status"] == "passed"
    assert control.get_read_model("project-1").verification_summary["invalidated"] == 1

    restarted = ProjectControlPlane(control.database_path)
    restarted.initialize()
    assert restarted.list_manual_evidence("project-1")[0]["status"] == "verification_invalidated"
    assert restarted.execute(evidence_command).event_id == original.event_id
    stale_new_request = evidence_command.model_copy(update={"idempotency_key": "stale-new-proof"})
    with pytest.raises(ProjectControlError):
        restarted.execute(stale_new_request)


def test_scope_revision_supersession_records_exact_cause(tmp_path: Path) -> None:
    control, criterion = _manual_pending(tmp_path)
    control.execute(_evidence_command(control, criterion, "scope-proof"))
    run = control.get_project("project-1")
    control.execute(command(
        ProjectCommandType.MARK_BLOCKED,
        run,
        "prepare-scope-change",
        payload={"reason": "Scope revision requested."},
    ))
    run = control.get_project("project-1")
    revised = control.execute(command(
        ProjectCommandType.REVISE_SCOPE,
        run,
        "supersede-scope",
        payload={"specification_hash": content_hash({"manual": "revision-2"})},
    ))
    history = control.list_manual_evidence("project-1")
    invalidation = history[0]["invalidation"]
    assert invalidation["cause"] == "scope_revision_superseded"
    assert invalidation["superseding_scope_revision_id"] == revised.read_model["scope_revision_id"]


def test_criterion_specific_invalidation_preserves_unrelated_evidence_and_fresh_handoff(
    tmp_path: Path,
) -> None:
    criteria = [
        {"criterion_id": name, "required": True,
         "verification_mode": "manual_user_verification_required", "description": name}
        for name in ("criterion-a", "criterion-b", "criterion-c")
    ]
    control = _manual_started(tmp_path, criteria)
    for criterion in (criteria[1], criteria[0]):
        _record_manual_required(control, criterion, f"initial-{criterion['criterion_id']}")
        control.execute(_criterion_evidence_command(
            control, criterion, f"proof-{criterion['criterion_id']}"
        ))

    run = control.get_project("project-1")
    control.execute(command(
        ProjectCommandType.REQUEST_VERIFICATION,
        run,
        "reverify-a",
        payload={"criterion_id": "criterion-a"},
    ))
    history = {item["criterion_id"]: item for item in control.list_manual_evidence("project-1")}
    assert history["criterion-a"]["status"] == "verification_invalidated"
    assert history["criterion-b"]["status"] == "passed"
    assert control.get_read_model("project-1").handoff_eligible is False

    run = control.get_project("project-1")
    with pytest.raises(ProjectControlError):
        control.execute(command(
            ProjectCommandType.REQUEST_HANDOFF,
            run,
            "handoff-with-invalidated-a",
            payload={"final_manifest_hash": run.current_manifest_hash},
        ))

    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.RECORD_VERIFIER_RESULT, run, "required-a-fresh", payload={
        "criterion_id": "criterion-a", "criterion_hash": content_hash(criteria[0]),
        "plan_revision_id": run.current_plan_revision_id,
        "scope_revision_id": run.current_scope_revision_id,
        "manifest_hash": run.current_manifest_hash,
        "result_hash": content_hash({"outcome": "manual_required", "fresh": True}),
        "outcome": "manual_required",
    }))
    control.execute(_criterion_evidence_command(control, criteria[0], "fresh-proof-a"))
    _record_manual_required(control, criteria[2], "criterion-c")
    control.execute(_criterion_evidence_command(control, criteria[2], "proof-criterion-c"))
    run = control.get_project("project-1")
    control.execute(command(
        ProjectCommandType.COMPLETE_WORK_UNIT,
        run,
        "complete-after-current-evidence",
        payload={"work_unit_id": "work-manual"},
    ))
    assert control.get_read_model("project-1").handoff_eligible is True

    run = control.get_project("project-1")
    handed_off = control.execute(command(
        ProjectCommandType.REQUEST_HANDOFF,
        run,
        "handoff-current-evidence",
        payload={"final_manifest_hash": run.current_manifest_hash},
    ))
    assert handed_off.lifecycle_status.value == "handoff_ready"


def test_manual_evidence_wrong_project_identity_is_rejected(tmp_path: Path) -> None:
    control, criterion = _manual_pending(tmp_path)
    request = _evidence_command(control, criterion, "wrong-project-proof")
    foreign = request.model_copy(update={"project_run_id": "another-project"})
    with pytest.raises(ProjectControlError) as error:
        control.execute(foreign)
    assert error.value.code.value == "project_not_found"
