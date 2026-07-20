from __future__ import annotations

import hashlib
from pathlib import Path

from backend.app.folders import project_root_fingerprint
from backend.app.project_analysis.state_manifest import build_project_state_manifest
from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlPlane,
    ProjectLifecycle,
)
from backend.app.project_control.contracts import (
    ExecutionAttemptStatus,
    ExecutionAttemptType,
    content_hash,
)
from backend.app.project_workers import (
    FileMutationEngine,
    FileMutationKind,
    FileOperationKind,
    FileOperationSpec,
    ProjectMutationExecutor,
    ProjectWorkerQueue,
    ProjectWorkerService,
    WorkerRequestStatus,
    build_file_mutation_spec,
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def command(kind, run, key, *, payload=None, authority=None):
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
        payload=payload or {},
        authority_scope=authority or {},
    )


def predicted_attempt_id(run, attempt_type: ExecutionAttemptType, key: str) -> str:
    return f"attempt-{content_hash([run.project_run_id, attempt_type.value, key])[:24]}"


def setup_project(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    target = root / "app.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    workspace_id = "workspace-mutation-worker"
    before = build_project_state_manifest(root, workspace_id=workspace_id).manifest_hash
    target.write_text("VALUE = 2\n", encoding="utf-8")
    after = build_project_state_manifest(root, workspace_id=workspace_id).manifest_hash
    target.write_text("VALUE = 1\n", encoding="utf-8")

    database = tmp_path / "control.db"
    control = ProjectControlPlane(database)
    control.initialize()
    base = {
        "project_run_id": "project-mutation-worker",
        "conversation_id": "conversation-mutation-worker",
        "workspace_id": workspace_id,
        "repository_root": str(root),
        "repository_root_fingerprint": project_root_fingerprint(root),
        "actor_id": "local-user",
    }
    control.execute(ProjectCommand(
        **base,
        command_type=ProjectCommandType.INITIALIZE_PROJECT,
        expected_state_version=0,
        idempotency_key="initialize",
    ))
    run = control.get_project(base["project_run_id"])
    control.execute(command(ProjectCommandType.ATTACH_SPECIFICATION, run, "specification", payload={
        "task_specification_id": "specification-1",
        "specification_hash": content_hash({"task": "mutate app.py"}),
        "included_paths": ["app.py"],
        "allowed_operations": ["read", "patch", "rollback", "verify"],
    }))
    run = control.get_project(base["project_run_id"])
    control.execute(command(ProjectCommandType.REGISTER_MANIFEST, run, "manifest", payload={
        "manifest_hash": before,
        "complete": True,
    }))
    run = control.get_project(base["project_run_id"])
    control.execute(command(ProjectCommandType.PROPOSE_PLAN_REVISION, run, "plan", payload={
        "acceptance_criteria": [{
            "criterion_id": "criterion-1",
            "required": True,
            "verification_mode": "structural_code_inspection",
        }],
        "work_units": [{"work_unit_id": "work-1", "objective": "Update app.py."}],
    }))
    run = control.get_project(base["project_run_id"])
    control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "approve-plan", authority={
        "operation": "prepare_work_units",
        "work_unit_ids": ["work-1"],
    }))
    run = control.get_project(base["project_run_id"])
    control.execute(command(ProjectCommandType.BEGIN_WORK_UNIT, run, "begin-work", payload={
        "work_unit_id": "work-1",
    }, authority={"work_unit_id": "work-1"}))

    queue = ProjectWorkerQueue(database)
    queue.initialize()
    service = ProjectWorkerService(control, queue)
    engine = FileMutationEngine(database, tmp_path / "mutation-journals")
    engine.initialize()
    executor = ProjectMutationExecutor(service, engine)
    return root, target, before, after, control, queue, service, executor


def test_queued_patch_and_rollback_reconcile_through_canonical_control_plane(tmp_path: Path) -> None:
    root, target, before, after, control, queue, service, executor = setup_project(tmp_path)
    project_id = "project-mutation-worker"

    run = control.get_project(project_id)
    control.execute(command(ProjectCommandType.RECORD_PATCH_PREVIEW, run, "patch-preview", payload={
        "patch_id": "patch-1",
    }))
    run = control.get_project(project_id)
    control.execute(command(ProjectCommandType.APPROVE_PATCH, run, "patch-approval", payload={
        "patch_id": "patch-1",
    }, authority={"patch_id": "patch-1", "operation": "apply_exact_patch"}))
    run = control.get_project(project_id)
    patch_key = "patch-start"
    patch_attempt_id = predicted_attempt_id(run, ExecutionAttemptType.PATCH, patch_key)
    patch_spec = build_file_mutation_spec(
        project_run_id=project_id,
        execution_attempt_id=patch_attempt_id,
        mutation_kind=FileMutationKind.PATCH,
        authority_id="patch-1",
        repository_root=str(root),
        repository_root_fingerprint=run.repository_root_fingerprint,
        workspace_id=run.workspace_id,
        plan_revision_id=str(run.current_plan_revision_id),
        scope_revision_id=str(run.current_scope_revision_id),
        manifest_hash=before,
        expected_result_manifest_hash=after,
        approved_paths=("app.py",),
        operations=(FileOperationSpec(
            relative_path="app.py",
            operation=FileOperationKind.UPDATE,
            preimage_sha256=sha("VALUE = 1\n"),
            result_sha256=sha("VALUE = 2\n"),
            new_content="VALUE = 2\n",
        ),),
    )
    control.execute(command(ProjectCommandType.BEGIN_PATCH_APPLICATION, run, patch_key, payload={
        "patch_id": "patch-1",
        "worker_dispatch": {
            "payload": {"file_mutation": patch_spec.model_dump(mode="json")},
            "idempotency_key": "dispatch-patch-1",
        },
    }, authority={"patch_id": "patch-1", "operation": "apply_exact_patch"}))

    dispatch_report = service.dispatch_pending()
    assert len(dispatch_report.dispatched_request_ids) == 1
    assert executor.run_once("mutation-worker") is True
    patch_request = queue.get(dispatch_report.dispatched_request_ids[0])
    assert patch_request.status == WorkerRequestStatus.SUCCEEDED
    assert patch_request.canonical_reconciled_at is not None
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"
    run = control.get_project(project_id)
    assert run.current_manifest_hash == after
    assert run.lifecycle_status == ProjectLifecycle.WORK_IN_PROGRESS
    assert control.list_attempts(project_id)[-1].status == ExecutionAttemptStatus.COMPLETED

    control.execute(command(ProjectCommandType.RECORD_ROLLBACK_PREVIEW, run, "rollback-preview", payload={
        "rollback_id": "rollback-1",
    }))
    run = control.get_project(project_id)
    rollback_key = "rollback-start"
    rollback_attempt_id = predicted_attempt_id(run, ExecutionAttemptType.ROLLBACK, rollback_key)
    rollback_spec = build_file_mutation_spec(
        project_run_id=project_id,
        execution_attempt_id=rollback_attempt_id,
        mutation_kind=FileMutationKind.ROLLBACK,
        authority_id="rollback-1",
        repository_root=str(root),
        repository_root_fingerprint=run.repository_root_fingerprint,
        workspace_id=run.workspace_id,
        plan_revision_id=str(run.current_plan_revision_id),
        scope_revision_id=str(run.current_scope_revision_id),
        manifest_hash=after,
        expected_result_manifest_hash=before,
        approved_paths=("app.py",),
        operations=(FileOperationSpec(
            relative_path="app.py",
            operation=FileOperationKind.UPDATE,
            preimage_sha256=sha("VALUE = 2\n"),
            result_sha256=sha("VALUE = 1\n"),
            new_content="VALUE = 1\n",
        ),),
    )
    control.execute(command(ProjectCommandType.APPROVE_ROLLBACK, run, "rollback-approval", payload={
        "rollback_id": "rollback-1",
        "mutation_spec_hash": rollback_spec.spec_hash,
    }, authority={
        "rollback_id": "rollback-1",
        "operation": "apply_exact_rollback",
        "mutation_spec_hash": rollback_spec.spec_hash,
    }))
    run = control.get_project(project_id)
    control.execute(command(ProjectCommandType.BEGIN_ROLLBACK, run, rollback_key, payload={
        "rollback_id": "rollback-1",
        "mutation_spec_hash": rollback_spec.spec_hash,
        "worker_dispatch": {
            "payload": {"file_mutation": rollback_spec.model_dump(mode="json")},
            "idempotency_key": "dispatch-rollback-1",
        },
    }, authority={
        "rollback_id": "rollback-1",
        "operation": "apply_exact_rollback",
        "mutation_spec_hash": rollback_spec.spec_hash,
    }))

    rollback_dispatch = service.dispatch_pending()
    assert len(rollback_dispatch.dispatched_request_ids) == 1
    assert executor.run_once("mutation-worker") is True
    rollback_request = queue.get(rollback_dispatch.dispatched_request_ids[0])
    assert rollback_request.status == WorkerRequestStatus.SUCCEEDED
    assert rollback_request.canonical_reconciled_at is not None
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    restored = control.get_project(project_id)
    assert restored.current_manifest_hash == before
    assert restored.lifecycle_status == ProjectLifecycle.READY_FOR_WORK
    assert control.list_attempts(project_id)[-1].status == ExecutionAttemptStatus.COMPLETED
