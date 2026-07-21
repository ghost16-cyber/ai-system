from __future__ import annotations

from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import ProjectCommandType
from backend.app.project_control.contracts import ExecutionAttemptType, content_hash
from backend.app.project_repair import CanonicalRepairService, RepairCycleStatus
from tests.test_project_coordinator_execution import _command, _runtime


def _record_initial_domain_failure(tmp_path):
    control, artifacts, coordinator, executor, project_id = _runtime(tmp_path)
    executor.run_once("coordinator-worker")
    run = control.get_project(project_id)
    patch_id = str(run.pending_user_action).split(":", 1)[1]
    control.execute(_command(
        run,
        ProjectCommandType.APPROVE_PATCH,
        "approve-initial-patch",
        payload={"patch_id": patch_id},
        authority={"patch_id": patch_id, "operation": "apply_exact_patch"},
    ))
    run = control.get_project(project_id)
    control.execute(_command(
        run,
        ProjectCommandType.BEGIN_PATCH_APPLICATION,
        "begin-initial-patch",
        payload={"patch_id": patch_id},
        authority={"patch_id": patch_id},
    ))
    run = control.get_project(project_id)
    attempt = max(
        (
            item for item in control.list_attempts(project_id)
            if item.attempt_type == ExecutionAttemptType.PATCH
        ),
        key=lambda item: item.attempt_number,
    )
    repair = CanonicalRepairService(control.database_path, control, artifacts)
    repair.initialize()
    failure = repair.capture_failure(
        project_run_id=project_id,
        execution_attempt_id=attempt.execution_attempt_id,
        failure_classification="process_exit_nonzero",
        result_reference={"stderr_hash": "6" * 64},
        authority={"patch_id": patch_id},
        plan_revision_id=str(run.current_plan_revision_id),
        scope_revision_id=str(run.current_scope_revision_id),
        manifest_hash=str(run.current_manifest_hash),
    )
    execution = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.EXECUTION_RESULT,
        binding=ProjectArtifactBinding(
            project_run_id=project_id,
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            execution_attempt_id=attempt.execution_attempt_id,
            authority_hash=content_hash({"patch_id": patch_id}),
        ),
        payload={"patch_id": patch_id, "succeeded": False},
        evidence_references=({
            "artifact_id": failure.artifact_id,
            "artifact_type": failure.artifact_type.value,
            "content_hash": failure.content_hash,
        },),
    ))
    control.execute(_command(
        run,
        ProjectCommandType.RECORD_PATCH_RESULT,
        "record-initial-failure",
        payload={
            "patch_id": patch_id,
            "execution_attempt_id": attempt.execution_attempt_id,
            "succeeded": False,
            "failure_classification": "process_exit_nonzero",
            "failure_artifact_id": failure.artifact_id,
        },
        authority={"patch_id": patch_id},
        artifact=execution,
    ))
    coordinator.reconcile(project_id)
    return control, artifacts, coordinator, executor, repair, project_id


def test_domain_failure_prepares_one_exact_repair_preview(tmp_path) -> None:
    control, artifacts, coordinator, executor, repair, project_id = (
        _record_initial_domain_failure(tmp_path)
    )

    assert executor.run_once("coordinator-worker") is True

    run = control.get_project(project_id)
    assert run.pending_user_action and run.pending_user_action.startswith("approve_patch:")
    assert run.repair_state["cycle_count"] == 1
    assert run.repair_state["status"] == "awaiting_approval"
    assert len(artifacts.list_for_project(
        project_id, artifact_type=ProjectArtifactType.DIAGNOSIS
    )) == 1
    assert len(artifacts.list_for_project(
        project_id, artifact_type=ProjectArtifactType.REPAIR_PREVIEW
    )) == 1
    cycles = repair.list_for_project(project_id)
    assert len(cycles) == 1
    assert cycles[0].status == RepairCycleStatus.PREVIEW_READY
    repair_intents = [
        item for item in coordinator.list_for_project(project_id)
        if item.intent_type.value == "prepare_repair"
    ]
    assert len(repair_intents) == 1


def test_failed_repair_verification_stops_without_second_cycle(tmp_path) -> None:
    control, artifacts, coordinator, executor, repair, project_id = (
        _record_initial_domain_failure(tmp_path)
    )
    executor.run_once("coordinator-worker")
    run = control.get_project(project_id)
    repair_patch_id = str(run.pending_user_action).split(":", 1)[1]
    control.execute(_command(
        run,
        ProjectCommandType.APPROVE_PATCH,
        "approve-repair-patch",
        payload={"patch_id": repair_patch_id},
        authority={"patch_id": repair_patch_id, "operation": "apply_exact_patch"},
    ))
    run = control.get_project(project_id)
    control.execute(_command(
        run,
        ProjectCommandType.BEGIN_PATCH_APPLICATION,
        "begin-repair-patch",
        payload={"patch_id": repair_patch_id},
        authority={"patch_id": repair_patch_id},
    ))
    run = control.get_project(project_id)
    attempt = max(
        (
            item for item in control.list_attempts(project_id)
            if item.attempt_type == ExecutionAttemptType.PATCH
        ),
        key=lambda item: item.attempt_number,
    )
    execution = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.EXECUTION_RESULT,
        binding=ProjectArtifactBinding(
            project_run_id=project_id,
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            execution_attempt_id=attempt.execution_attempt_id,
            authority_hash=content_hash({"patch_id": repair_patch_id}),
        ),
        payload={"patch_id": repair_patch_id, "succeeded": True},
    ))
    control.execute(_command(
        run,
        ProjectCommandType.RECORD_PATCH_RESULT,
        "record-repair-success",
        payload={
            "patch_id": repair_patch_id,
            "execution_attempt_id": attempt.execution_attempt_id,
            "succeeded": True,
            "resulting_manifest_hash": content_hash({"repair": "applied"}),
        },
        authority={"patch_id": repair_patch_id},
        artifact=execution,
    ))
    (tmp_path / "workspace" / "src" / "app.py").write_text(
        "def broken(:\n", encoding="utf-8"
    )
    coordinator.reconcile(project_id)

    assert executor.run_once("coordinator-worker") is True

    run = control.get_project(project_id)
    assert run.lifecycle_status.value == "blocked"
    assert run.pending_user_action == "review_failed_repair"
    assert run.repair_state["status"] == "failed"
    assert len(repair.list_for_project(project_id)) == 1
    assert repair.list_for_project(project_id)[0].status == RepairCycleStatus.FAILED
    assert coordinator.reconcile(project_id) is None
