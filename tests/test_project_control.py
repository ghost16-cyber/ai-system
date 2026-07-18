from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlError,
    ProjectControlErrorCode,
    ProjectControlPlane,
    ProjectLifecycle,
)
from backend.app.project_control.contracts import content_hash


def base() -> dict[str, str]:
    return {
        "project_run_id": "project-1", "conversation_id": "conversation-1",
        "workspace_id": "workspace-1", "repository_root": "canonical-root",
        "repository_root_fingerprint": "root-fingerprint", "actor_id": "local-user",
    }


def command(kind: ProjectCommandType | str, run, key: str, *, payload=None, authority=None, **changes) -> ProjectCommand:
    values = {
        **base(), "command_type": kind, "expected_state_version": run.state_version,
        "idempotency_key": key, "plan_revision_id": run.current_plan_revision_id,
        "scope_revision_id": run.current_scope_revision_id,
        "manifest_hash": run.current_manifest_hash, "payload": payload or {},
        "authority_scope": authority or {},
    }
    values.update(changes)
    return ProjectCommand(**values)


@pytest.fixture
def control(tmp_path: Path) -> ProjectControlPlane:
    value = ProjectControlPlane(tmp_path / "control.db")
    value.initialize()
    value.execute(ProjectCommand(
        **base(), command_type=ProjectCommandType.INITIALIZE_PROJECT,
        expected_state_version=0, idempotency_key="initialize",
    ))
    return value


def planned(control: ProjectControlPlane):
    run = control.get_project("project-1")
    specification_hash = content_hash({"specification": "one"})
    control.execute(command(ProjectCommandType.ATTACH_SPECIFICATION, run, "specification", payload={
        "task_specification_id": "specification-1", "specification_hash": specification_hash,
        "included_paths": ["backend/app.py"], "excluded_paths": [".env"],
        "allowed_operations": ["read", "patch", "verify"], "reason": "Initial bounded scope.",
    }))
    run = control.get_project("project-1")
    manifest_hash = content_hash({"files": {"backend/app.py": "abc"}})
    control.execute(command(ProjectCommandType.REGISTER_MANIFEST, run, "manifest", payload={
        "manifest_hash": manifest_hash, "complete": True,
    }))
    run = control.get_project("project-1")
    criterion = {"criterion_id": "criterion-1", "required": True, "verification_mode": "structural_code_inspection"}
    control.execute(command(ProjectCommandType.PROPOSE_PLAN_REVISION, run, "plan", payload={
        "acceptance_criteria": [criterion],
        "work_units": [{"work_unit_id": "work-1", "objective": "Change the bounded file."}],
        "configured_limits": {"max_patch_attempts": 3},
    }))
    return control.get_project("project-1"), criterion


def approved(control: ProjectControlPlane):
    run, criterion = planned(control)
    control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "approve-plan", authority={
        "operation": "prepare_work_units", "work_unit_ids": ["work-1"],
    }))
    return control.get_project("project-1"), criterion


def test_canonical_lifecycle_revisions_and_approval_are_separate(control: ProjectControlPlane) -> None:
    run, _criterion = approved(control)
    assert run.lifecycle_status == ProjectLifecycle.READY_FOR_WORK
    assert run.current_plan_revision_id and run.current_scope_revision_id
    assert run.work_unit_state["work-1"]["status"] == "pending"
    approvals = control.list_approvals(run.project_run_id, active_only=True)
    assert [item.approval_type.value for item in approvals] == ["plan"]
    assert control.get_read_model(run.project_run_id).approval_fresh is True


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("conversation_id", "other", ProjectControlErrorCode.CONVERSATION_MISMATCH),
        ("workspace_id", "other", ProjectControlErrorCode.WORKSPACE_MISMATCH),
        ("repository_root", "other", ProjectControlErrorCode.REPOSITORY_ROOT_MISMATCH),
        ("actor_id", "other", ProjectControlErrorCode.ACTOR_MISMATCH),
    ],
)
def test_identity_bindings_fail_closed(control: ProjectControlPlane, field: str, value: str, code) -> None:
    run = control.get_project("project-1")
    with pytest.raises(ProjectControlError) as caught:
        control.execute(command(ProjectCommandType.REQUEST_CLARIFICATION, run, f"wrong-{field}", payload={"reason": "test"}, **{field: value}))
    assert caught.value.code == code
    assert control.get_project(run.project_run_id).state_version == run.state_version


def test_illegal_and_stale_transitions_leave_no_event(control: ProjectControlPlane) -> None:
    run = control.get_project("project-1")
    before = len(control.list_events(run.project_run_id))
    with pytest.raises(ProjectControlError) as illegal:
        control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "too-early", authority={"operation": "prepare"}))
    assert illegal.value.code == ProjectControlErrorCode.ILLEGAL_TRANSITION
    with pytest.raises(ProjectControlError) as stale:
        control.execute(command(ProjectCommandType.ATTACH_SPECIFICATION, run, "stale", payload={}, expected_state_version=99))
    assert stale.value.code == ProjectControlErrorCode.STALE_STATE_VERSION
    assert len(control.list_events(run.project_run_id)) == before


def test_exact_idempotent_replay_returns_original_without_new_event(control: ProjectControlPlane) -> None:
    run, _criterion = planned(control)
    request = command(ProjectCommandType.APPROVE_PLAN, run, "same-approval", authority={"operation": "prepare"})
    first = control.execute(request)
    second = control.execute(request)
    assert second == first
    assert len([event for event in control.list_events(run.project_run_id) if event.request_id == "same-approval"]) == 1
    assert len(control.list_approvals(run.project_run_id)) == 1


def test_idempotency_key_with_changed_payload_conflicts(control: ProjectControlPlane) -> None:
    run, _criterion = planned(control)
    control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "approval-key", authority={"operation": "prepare"}))
    with pytest.raises(ProjectControlError) as caught:
        control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "approval-key", authority={"operation": "different"}))
    assert caught.value.code == ProjectControlErrorCode.IDEMPOTENCY_CONFLICT


def test_scope_revision_invalidates_authority_and_definitions_remain_immutable(control: ProjectControlPlane) -> None:
    run, _criterion = approved(control)
    old_plan = run.current_plan_revision_id
    old_scope = run.current_scope_revision_id
    control.execute(command(ProjectCommandType.REVISE_SCOPE, run, "scope-2", payload={
        "included_paths": ["backend/app.py", "backend/new.py"], "excluded_paths": [".env"],
        "allowed_operations": ["read", "patch", "verify"], "reason": "Approved material scope change.",
    }))
    current = control.get_project(run.project_run_id)
    assert current.current_scope_revision_id != old_scope
    assert current.current_plan_revision_id is None
    assert control.list_approvals(run.project_run_id, active_only=True) == []
    with sqlite3.connect(control.database_path) as connection:
        stored = connection.execute("SELECT revision_json FROM project_plan_revisions_v3 WHERE plan_revision_id = ?", (old_plan,)).fetchone()
    assert stored is not None and '"work_unit_id":"work-1"' in stored[0]


def test_manifest_and_revision_mismatches_fail(control: ProjectControlPlane) -> None:
    run, _criterion = planned(control)
    with pytest.raises(ProjectControlError) as plan_error:
        control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "wrong-plan", authority={"operation": "prepare"}, plan_revision_id="stale-plan"))
    assert plan_error.value.code == ProjectControlErrorCode.PLAN_REVISION_MISMATCH
    with pytest.raises(ProjectControlError) as manifest_error:
        control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "wrong-manifest", authority={"operation": "prepare"}, manifest_hash="0" * 64))
    assert manifest_error.value.code == ProjectControlErrorCode.MANIFEST_MISMATCH


def test_simultaneous_approval_only_one_version_wins(control: ProjectControlPlane) -> None:
    run, _criterion = planned(control)
    requests = [command(ProjectCommandType.APPROVE_PLAN, run, f"approval-{index}", authority={"operation": "prepare", "attempt": index}) for index in range(2)]
    def execute(item):
        try:
            return control.execute(item)
        except ProjectControlError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(execute, requests))
    assert sum(not isinstance(item, ProjectControlErrorCode) for item in outcomes) == 1
    assert ProjectControlErrorCode.STALE_STATE_VERSION in outcomes
    assert len(control.list_approvals(run.project_run_id)) == 1


def test_active_attempt_survives_restart_and_recovers_without_reexecution(control: ProjectControlPlane) -> None:
    run, _criterion = approved(control)
    control.execute(command(ProjectCommandType.BEGIN_WORK_UNIT, run, "begin", payload={"work_unit_id": "work-1"}, authority={"work_unit_id": "work-1"}))
    restarted = ProjectControlPlane(control.database_path)
    restarted.initialize()
    current = restarted.get_project(run.project_run_id)
    attempts = restarted.list_attempts(run.project_run_id)
    assert len(attempts) == 1 and attempts[0].status.value == "active"
    restarted.execute(command(ProjectCommandType.RECOVER_ATTEMPT, current, "recover", payload={"execution_attempt_id": attempts[0].execution_attempt_id}))
    assert restarted.list_attempts(run.project_run_id)[0].status.value == "interrupted"
    assert restarted.get_project(run.project_run_id).lifecycle_status == ProjectLifecycle.BLOCKED


def test_corrupted_and_unsupported_state_fail_explicitly(control: ProjectControlPlane) -> None:
    with sqlite3.connect(control.database_path) as connection:
        connection.execute("UPDATE project_runs SET run_json = '{broken' WHERE project_run_id = 'project-1'")
    with pytest.raises(ProjectControlError) as corrupted:
        control.get_project("project-1")
    assert corrupted.value.code == ProjectControlErrorCode.CORRUPTED_STORED_STATE
    with sqlite3.connect(control.database_path) as connection:
        connection.execute("UPDATE project_runs SET schema_version = 'future.v99' WHERE project_run_id = 'project-1'")
    with pytest.raises(ProjectControlError) as unsupported:
        control.get_project("project-1")
    assert unsupported.value.code == ProjectControlErrorCode.UNSUPPORTED_STORED_STATE


def test_incomplete_manifest_blocks_planning(control: ProjectControlPlane) -> None:
    run = control.get_project("project-1")
    spec_hash = content_hash({"spec": 1})
    control.execute(command(ProjectCommandType.ATTACH_SPECIFICATION, run, "spec-incomplete", payload={
        "task_specification_id": "spec", "specification_hash": spec_hash,
        "included_paths": ["app.py"], "allowed_operations": ["read"],
    }))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.REGISTER_MANIFEST, run, "manifest-incomplete", payload={
        "manifest_hash": content_hash({"partial": True}), "complete": False,
    }))
    assert control.get_project(run.project_run_id).lifecycle_status == ProjectLifecycle.BLOCKED


def test_execution_verification_handoff_and_finalize_end_to_end(control: ProjectControlPlane) -> None:
    run, criterion = approved(control)
    control.execute(command(ProjectCommandType.BEGIN_WORK_UNIT, run, "begin-work", payload={"work_unit_id": "work-1"}, authority={"work_unit_id": "work-1"}))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.RECORD_PATCH_PREVIEW, run, "patch-preview", payload={"patch_id": "patch-1"}))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.APPROVE_PATCH, run, "patch-approval", payload={"patch_id": "patch-1"}, authority={"patch_id": "patch-1", "operation": "apply_exact_patch"}))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.BEGIN_PATCH_APPLICATION, run, "patch-start", payload={"patch_id": "patch-1"}, authority={"patch_id": "patch-1"}))
    run = control.get_project(run.project_run_id)
    resulting_manifest = content_hash({"files": {"backend/app.py": "changed"}})
    control.execute(command(ProjectCommandType.RECORD_PATCH_RESULT, run, "patch-result", payload={
        "patch_id": "patch-1", "succeeded": True, "resulting_manifest_hash": resulting_manifest,
        "result_reference": {"patch_id": "patch-1"},
    }, authority={"patch_id": "patch-1"}))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.REQUEST_VERIFICATION, run, "verification-start", authority={"criterion_id": "criterion-1"}))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.RECORD_VERIFIER_RESULT, run, "verification-result", payload={
        "criterion_id": "criterion-1", "outcome": "passed", "result_hash": content_hash({"passed": True}),
        "criterion_hash": content_hash(criterion), "plan_revision_id": run.current_plan_revision_id,
        "scope_revision_id": run.current_scope_revision_id, "manifest_hash": run.current_manifest_hash,
        "result_reference": {"verifier_result_id": "verifier-1"},
    }))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.COMPLETE_WORK_UNIT, run, "complete-work", payload={"work_unit_id": "work-1"}))
    run = control.get_project(run.project_run_id)
    assert control.get_read_model(run.project_run_id).handoff_eligible is True
    control.execute(command(ProjectCommandType.REQUEST_HANDOFF, run, "handoff-request", payload={"final_manifest_hash": run.current_manifest_hash}))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.FINALIZE_PROJECT, run, "handoff-finalize"))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.FINALIZE_PROJECT, run, "project-finalize"))
    assert control.get_project(run.project_run_id).lifecycle_status == ProjectLifecycle.COMPLETED
    attempts = control.list_attempts(run.project_run_id)
    verification_attempts = [item for item in attempts if item.attempt_type.value == "verification"]
    patch_attempts = [item for item in attempts if item.attempt_type.value == "patch_application"]
    assert len(verification_attempts) == 1 and verification_attempts[0].status.value == "completed"
    assert len(patch_attempts) == 1 and patch_attempts[0].status.value == "completed"


def test_manual_criterion_cannot_pass_automatically(control: ProjectControlPlane) -> None:
    run = control.get_project("project-1")
    specification_hash = content_hash({"manual": True})
    control.execute(command(ProjectCommandType.ATTACH_SPECIFICATION, run, "manual-spec", payload={
        "task_specification_id": "manual-spec", "specification_hash": specification_hash,
        "included_paths": ["app.py"], "allowed_operations": ["read", "verify"],
    }))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.REGISTER_MANIFEST, run, "manual-manifest", payload={"manifest_hash": content_hash({"app.py": "one"}), "complete": True}))
    run = control.get_project(run.project_run_id)
    criterion = {"criterion_id": "manual", "required": True, "verification_mode": "manual_user_verification_required"}
    control.execute(command(ProjectCommandType.PROPOSE_PLAN_REVISION, run, "manual-plan", payload={"acceptance_criteria": [criterion], "work_units": [{"work_unit_id": "work-1"}]}))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "manual-approve", authority={"operation": "prepare"}))
    run = control.get_project(run.project_run_id)
    control.execute(command(ProjectCommandType.REQUEST_VERIFICATION, run, "manual-start"))
    run = control.get_project(run.project_run_id)
    with pytest.raises(ProjectControlError) as caught:
        control.execute(command(ProjectCommandType.RECORD_VERIFIER_RESULT, run, "manual-auto-pass", payload={
            "criterion_id": "manual", "outcome": "passed", "result_hash": content_hash({"claim": "passed"}),
            "criterion_hash": content_hash(criterion), "plan_revision_id": run.current_plan_revision_id,
            "scope_revision_id": run.current_scope_revision_id, "manifest_hash": run.current_manifest_hash,
        }))
    assert caught.value.code == ProjectControlErrorCode.STALE_VERIFICATION


def test_legacy_reconciliation_discards_approval_and_is_deduplicated(tmp_path: Path) -> None:
    control = ProjectControlPlane(tmp_path / "legacy.db")
    control.initialize()
    specification_hash = content_hash({"legacy": "spec"})
    manifest_hash = content_hash({"legacy": "manifest"})
    legacy = {
        "delivery_job_id": "legacy-1", "conversation_id": "conversation-1",
        "folder_access_id": "workspace-1", "root_fingerprint": "root-fingerprint",
        "specification": {"specification_id": "spec-1", "specification_hash": specification_hash,
                          "acceptance_criteria": []},
        "project_state_manifest": {"manifest_hash": manifest_hash, "complete": True},
        "plan_revision": {"work_units": [{"work_unit_id": "work-1", "expected_files": ["app.py"]}]},
        "plan_approval": {"approval_id": "untrusted-legacy-approval"},
    }
    first = control.reconcile_legacy_delivery(legacy, repository_root="canonical-root")
    second = control.reconcile_legacy_delivery(legacy, repository_root="canonical-root")
    assert second == first
    assert first.approval_fresh is False and first.approval_state == "reapproval_required"
    assert control.list_approvals("legacy-1") == []
    events = control.list_events("legacy-1")
    assert len(events) == 5
    assert events[-1].event_type == "reconcile_legacy"
