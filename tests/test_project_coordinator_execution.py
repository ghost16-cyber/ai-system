from __future__ import annotations

from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import ProjectCommand, ProjectCommandType, ProjectControlPlane
from backend.app.project_control.contracts import ExecutionAttemptType, content_hash
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_coordinator import (
    CoordinatorIntentStatus,
    ProjectCoordinatorExecutor,
    ProjectCoordinatorService,
)


def _command(run, kind, key, *, payload=None, authority=None, artifact=None):
    return ProjectCommand(
        command_type=kind,
        project_run_id=run.project_run_id,
        conversation_id=run.conversation_id,
        workspace_id=run.workspace_id,
        repository_root=run.repository_root,
        repository_root_fingerprint=run.repository_root_fingerprint,
        actor_id=run.actor_id,
        expected_state_version=run.state_version,
        idempotency_key=key,
        plan_revision_id=run.current_plan_revision_id,
        scope_revision_id=run.current_scope_revision_id,
        manifest_hash=run.current_manifest_hash,
        authority_scope=authority or {},
        payload=payload or {},
        artifact_id=artifact.artifact_id if artifact else None,
        artifact_type=artifact.artifact_type.value if artifact else None,
        artifact_hash=artifact.content_hash if artifact else None,
        artifact_binding_hash=artifact.binding_hash if artifact else None,
    )


def _runtime(
    tmp_path,
    *,
    include_patch_operations: bool = True,
    include_repair_operations: bool = True,
):
    database = tmp_path / "astra.db"
    root = tmp_path / "workspace"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    service = CanonicalProjectService(control, artifacts)
    project = service.create_project(
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        repository_root=str(root),
        repository_root_fingerprint="root-fingerprint-1",
        actor_id="local-user",
        idempotency_key="create-project",
        folder_authority={
            "status": "completed",
            "action_id": "workspace-1",
            "conversation_id": "conversation-1",
            "workspace_id": "workspace-1",
            "repository_root_fingerprint": "root-fingerprint-1",
        },
        specification={
            "specification_id": "spec-1",
            "specification_hash": "1" * 64,
            "revision": 1,
            "included_paths": ["src/app.py"],
            "allowed_operations": ["read", "approved_patch", "verification"],
        },
        manifest={
            "manifest_hash": "2" * 64,
            "complete": True,
            "revision": 1,
            "entries": [{"path": "src/app.py", "sha256": "3" * 64}],
        },
        plan={
            "revision": 1,
            "acceptance_criteria": [{
                "criterion_id": "criterion-1",
                "required": True,
                "verification_mode": "structural_code_inspection",
            }],
            "work_units": [{
                "work_unit_id": "work-1",
                "expected_files": ["src/app.py"],
                "acceptance_criteria_ids": ["criterion-1"],
                "patch_operations": ([{
                    "operation": "replace",
                    "path": "src/app.py",
                    "expected_sha256": "3" * 64,
                    "content": "VALUE = 2\n",
                }] if include_patch_operations else []),
                "repair_operations": ([{
                    "operation": "replace",
                    "path": "src/app.py",
                    "expected_sha256": "4" * 64,
                    "content": "VALUE = 3\n",
                }] if include_repair_operations else []),
            }],
            "configured_limits": {"max_work_units": 2, "max_verifications": 2},
        },
    )
    run = control.get_project(project.project_run_id)
    control.execute(_command(
        run,
        ProjectCommandType.APPROVE_PLAN,
        "approve-plan",
        authority={"operation": "prepare_work_units", "work_unit_ids": ["work-1"]},
        artifact=artifacts.get(run.current_artifact_ids["plan"]),
    ))
    coordinator = ProjectCoordinatorService(database, control)
    coordinator.initialize()
    coordinator.reconcile(project.project_run_id)
    executor = ProjectCoordinatorExecutor(coordinator, control, artifacts)
    return control, artifacts, coordinator, executor, project.project_run_id


def test_coordinator_prepares_one_exact_patch_preview(tmp_path) -> None:
    control, artifacts, coordinator, executor, project_id = _runtime(tmp_path)

    assert executor.run_once("coordinator-worker") is True

    run = control.get_project(project_id)
    assert run.pending_user_action and run.pending_user_action.startswith("approve_patch:")
    previews = artifacts.list_for_project(
        project_id, artifact_type=ProjectArtifactType.PATCH_PREVIEW
    )
    assert len(previews) == 1
    assert previews[0].binding.coordinator_intent_id
    intents = coordinator.list_for_project(project_id)
    assert len(intents) == 1
    assert intents[0].status == CoordinatorIntentStatus.COMPLETED
    assert executor.run_once("coordinator-worker") is False


def test_canonical_happy_path_advances_to_exact_handoff(tmp_path) -> None:
    control, artifacts, coordinator, executor, project_id = _runtime(tmp_path)
    executor.run_once("coordinator-worker")
    run = control.get_project(project_id)
    patch_id = str(run.pending_user_action).split(":", 1)[1]
    control.execute(_command(
        run,
        ProjectCommandType.APPROVE_PATCH,
        "approve-patch",
        payload={"patch_id": patch_id, "work_unit_id": "work-1"},
        authority={"patch_id": patch_id, "work_unit_id": "work-1", "operation": "apply_exact_patch"},
        artifact=artifacts.get(run.current_artifact_ids["patch_preview"]),
    ))
    run = control.get_project(project_id)
    control.execute(_command(
        run,
        ProjectCommandType.BEGIN_PATCH_APPLICATION,
        "begin-patch",
        payload={"patch_id": patch_id},
        authority={"patch_id": patch_id},
    ))
    run = control.get_project(project_id)
    patch_attempt = next(
        item for item in control.list_attempts(project_id)
        if item.attempt_type == ExecutionAttemptType.PATCH
    )
    result_artifact = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.EXECUTION_RESULT,
        binding=ProjectArtifactBinding(
            project_run_id=project_id,
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            execution_attempt_id=patch_attempt.execution_attempt_id,
            authority_hash=content_hash({"patch_id": patch_id}),
        ),
        payload={"patch_id": patch_id, "succeeded": True},
    ))
    resulting_manifest = content_hash({"src/app.py": "VALUE = 2"})
    control.execute(_command(
        run,
        ProjectCommandType.RECORD_PATCH_RESULT,
        "record-patch",
        payload={
            "patch_id": patch_id,
            "execution_attempt_id": patch_attempt.execution_attempt_id,
            "succeeded": True,
            "resulting_manifest_hash": resulting_manifest,
            "result_reference": {"artifact_id": result_artifact.artifact_id},
        },
        authority={"patch_id": patch_id},
        artifact=result_artifact,
    ))
    coordinator.reconcile(project_id)

    assert executor.run_once("coordinator-worker") is True
    run = control.get_project(project_id)
    assert run.work_unit_state["work-1"]["status"] == "completed"
    assert run.pending_user_action == "request_handoff"

    assert executor.run_once("coordinator-worker") is True
    run = control.get_project(project_id)
    assert run.pending_user_action == "finalize_project"
    handoff = artifacts.get(run.current_artifact_ids["handoff"])
    assert handoff is not None

    control.execute(_command(
        run,
        ProjectCommandType.FINALIZE_PROJECT,
        "finalize-project",
        payload={"handoff_artifact_id": handoff.artifact_id},
        authority={"handoff_artifact_id": handoff.artifact_id},
        artifact=handoff,
    ))
    assert control.get_project(project_id).lifecycle_status.value == "completed"
    assert [item.status for item in coordinator.list_for_project(project_id)] == [
        CoordinatorIntentStatus.COMPLETED,
        CoordinatorIntentStatus.COMPLETED,
        CoordinatorIntentStatus.COMPLETED,
    ]


def test_missing_deterministic_patch_blocks_without_mutation(tmp_path) -> None:
    control, artifacts, coordinator, executor, project_id = _runtime(
        tmp_path, include_patch_operations=False
    )

    assert executor.run_once("coordinator-worker") is True

    run = control.get_project(project_id)
    assert run.lifecycle_status.value == "blocked"
    assert run.pending_user_action == "revise_plan"
    assert artifacts.list_for_project(
        project_id, artifact_type=ProjectArtifactType.COORDINATOR_DECISION
    )
    assert coordinator.list_for_project(project_id)[0].status == CoordinatorIntentStatus.COMPLETED
