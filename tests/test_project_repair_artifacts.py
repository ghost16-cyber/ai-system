from __future__ import annotations

import pytest

from backend.app.project_artifacts import ProjectArtifactType
from backend.app.project_repair import (
    CanonicalRepairService,
    CanonicalRepairServiceError,
    RepairCycleStatus,
)
from tests.test_project_coordinator_execution import _runtime


def test_repair_artifacts_are_bound_immutable_and_one_cycle_only(tmp_path) -> None:
    control, artifacts, _coordinator, _executor, project_id = _runtime(tmp_path)
    run = control.get_project(project_id)
    failure = CanonicalRepairService(
        control.database_path, control, artifacts
    ).capture_failure(
        project_run_id=project_id,
        execution_attempt_id="attempt-domain-failure",
        failure_classification="process_exit_nonzero",
        result_reference={"stderr_hash": "5" * 64},
        authority={"patch_id": "patch-1"},
        plan_revision_id=str(run.current_plan_revision_id),
        scope_revision_id=str(run.current_scope_revision_id),
        manifest_hash=str(run.current_manifest_hash),
    )
    repair = CanonicalRepairService(control.database_path, control, artifacts)
    repair.initialize()

    cycle = repair.begin_diagnosis(
        project_run_id=project_id,
        failure_artifact_id=failure.artifact_id,
        work_unit_id="work-1",
    )
    replay = repair.begin_diagnosis(
        project_run_id=project_id,
        failure_artifact_id=failure.artifact_id,
        work_unit_id="work-1",
    )
    assert replay.repair_cycle_id == cycle.repair_cycle_id
    diagnosis = repair.record_diagnosis(
        cycle,
        coordinator_intent_id="intent-repair",
        root_causes=({"reason_code": "failed_assertion", "evidence": failure.artifact_id},),
        repair_scope=("src/app.py",),
    )
    preview = repair.record_preview(
        cycle,
        coordinator_intent_id="intent-repair",
        diagnosis_artifact_id=diagnosis.artifact_id,
        patch_id="repair-patch-1",
        operations=({"operation": "replace", "path": "src/app.py", "content": "VALUE = 3\n"},),
    )
    assert diagnosis.artifact_type == ProjectArtifactType.DIAGNOSIS
    assert preview.artifact_type == ProjectArtifactType.REPAIR_PREVIEW
    assert preview.evidence_references == (
        {"artifact_id": failure.artifact_id},
        {"artifact_id": diagnosis.artifact_id},
    )

    second_failure = repair.capture_failure(
        project_run_id=project_id,
        execution_attempt_id="attempt-second-failure",
        failure_classification="process_exit_nonzero",
        result_reference={},
        authority={"patch_id": "patch-2"},
        plan_revision_id=str(run.current_plan_revision_id),
        scope_revision_id=str(run.current_scope_revision_id),
        manifest_hash=str(run.current_manifest_hash),
    )
    with pytest.raises(CanonicalRepairServiceError, match="budget"):
        repair.begin_diagnosis(
            project_run_id=project_id,
            failure_artifact_id=second_failure.artifact_id,
            work_unit_id="work-1",
        )

    finished = repair.finish_cycle(
        cycle.repair_cycle_id, status=RepairCycleStatus.COMPLETED
    )
    assert finished.status == RepairCycleStatus.COMPLETED
    assert finished.completed_at is not None
