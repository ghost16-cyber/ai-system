from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.project_control import (
    ExecutionDispatchStatus,
    ProjectCommand,
    ProjectCommandType,
    ProjectControlError,
    ProjectControlErrorCode,
    ProjectControlPlane,
)
from backend.app.project_control.contracts import ExecutionAttemptStatus, content_hash
from backend.app.project_workers import (
    ProjectWorkerQueue,
    ProjectWorkerService,
    WorkerEnqueueCommand,
    WorkerLimits,
)


def base() -> dict[str, str]:
    return {
        "project_run_id": "project-dispatch",
        "conversation_id": "conversation-dispatch",
        "workspace_id": "workspace-dispatch",
        "repository_root": "canonical-root",
        "repository_root_fingerprint": "root-fingerprint",
        "actor_id": "local-user",
    }


def command(kind: ProjectCommandType, run, key: str, *, payload=None, authority=None) -> ProjectCommand:
    return ProjectCommand(
        **base(),
        command_type=kind,
        expected_state_version=run.state_version,
        idempotency_key=key,
        plan_revision_id=run.current_plan_revision_id,
        scope_revision_id=run.current_scope_revision_id,
        manifest_hash=run.current_manifest_hash,
        payload=payload or {},
        authority_scope=authority or {},
    )


@pytest.fixture
def runtime(tmp_path: Path):
    database = tmp_path / "control.db"
    control = ProjectControlPlane(database)
    control.initialize()
    control.execute(ProjectCommand(
        **base(),
        command_type=ProjectCommandType.INITIALIZE_PROJECT,
        expected_state_version=0,
        idempotency_key="initialize",
    ))
    run = control.get_project("project-dispatch")
    control.execute(command(ProjectCommandType.ATTACH_SPECIFICATION, run, "specification", payload={
        "task_specification_id": "specification-dispatch",
        "specification_hash": content_hash({"specification": "dispatch"}),
        "included_paths": ["backend/app.py"],
        "allowed_operations": ["read", "patch", "verify"],
    }))
    run = control.get_project("project-dispatch")
    control.execute(command(ProjectCommandType.REGISTER_MANIFEST, run, "manifest", payload={
        "manifest_hash": content_hash({"files": {"backend/app.py": "abc"}}),
        "complete": True,
    }))
    run = control.get_project("project-dispatch")
    control.execute(command(ProjectCommandType.PROPOSE_PLAN_REVISION, run, "plan", payload={
        "acceptance_criteria": [{
            "criterion_id": "criterion-1",
            "required": True,
            "verification_mode": "structural_code_inspection",
        }],
        "work_units": [{"work_unit_id": "work-1", "objective": "Change the bounded file."}],
    }))
    run = control.get_project("project-dispatch")
    control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "approve-plan", authority={
        "operation": "prepare_work_units",
        "work_unit_ids": ["work-1"],
    }))
    queue = ProjectWorkerQueue(database)
    queue.initialize()
    return control, queue, ProjectWorkerService(control, queue)


def begin_dispatched(control: ProjectControlPlane, *, limits=None):
    run = control.get_project("project-dispatch")
    control.execute(command(ProjectCommandType.BEGIN_WORK_UNIT, run, "begin-work", payload={
        "work_unit_id": "work-1",
        "worker_dispatch": {
            "payload": {"work_unit_id": "work-1", "action": "prepare_patch"},
            "limits": limits or {},
            "idempotency_key": "queue-work-1",
        },
    }, authority={"work_unit_id": "work-1"}))
    return control.get_project("project-dispatch")


def enqueue_from_dispatch(dispatch) -> WorkerEnqueueCommand:
    return WorkerEnqueueCommand(
        project_run_id=dispatch.project_run_id,
        execution_attempt_id=dispatch.execution_attempt_id,
        attempt_type=dispatch.attempt_type,
        conversation_id=dispatch.conversation_id,
        workspace_id=dispatch.workspace_id,
        repository_root=dispatch.repository_root,
        repository_root_fingerprint=dispatch.repository_root_fingerprint,
        actor_id=dispatch.actor_id,
        plan_revision_id=dispatch.plan_revision_id,
        scope_revision_id=dispatch.scope_revision_id,
        manifest_hash=dispatch.manifest_hash,
        expected_project_state_version=dispatch.expected_project_state_version,
        authority=dispatch.authority,
        payload=dispatch.payload,
        idempotency_key=dispatch.enqueue_idempotency_key,
        priority=dispatch.priority,
        available_at=dispatch.available_at,
        limits=WorkerLimits.model_validate(dispatch.limits),
    )


def test_attempt_and_dispatch_are_persisted_before_queue_delivery(runtime) -> None:
    control, queue, service = runtime
    run = begin_dispatched(control)

    attempts = control.list_attempts(run.project_run_id)
    dispatches = control.list_execution_dispatches(run.project_run_id)
    assert len(attempts) == 1
    assert len(dispatches) == 1
    assert dispatches[0].execution_attempt_id == attempts[0].execution_attempt_id
    assert dispatches[0].status == ExecutionDispatchStatus.PENDING
    assert dispatches[0].expected_project_state_version == run.state_version
    assert queue.list_for_project(run.project_run_id) == []

    report = service.dispatch_pending()
    requests = queue.list_for_project(run.project_run_id)
    assert report.dispatched_request_ids == (requests[0].worker_request_id,)
    assert len(requests) == 1
    assert control.get_execution_dispatch(dispatches[0].execution_dispatch_id).status == ExecutionDispatchStatus.DISPATCHED


def test_crash_after_enqueue_before_outbox_ack_reuses_same_worker_request(runtime, monkeypatch) -> None:
    control, queue, service = runtime
    run = begin_dispatched(control)
    dispatch = control.list_execution_dispatches(run.project_run_id)[0]
    original = control.mark_execution_dispatch_dispatched

    def fail_ack(*args, **kwargs):
        raise ProjectControlError(ProjectControlErrorCode.PERSISTENCE_CONFLICT, "simulated crash boundary")

    monkeypatch.setattr(control, "mark_execution_dispatch_dispatched", fail_ack)
    first = service.dispatch_pending()
    first_request = queue.list_for_project(run.project_run_id)[0]
    assert first.deferred_dispatch_ids == (dispatch.execution_dispatch_id,)
    assert control.get_execution_dispatch(dispatch.execution_dispatch_id).status == ExecutionDispatchStatus.PENDING

    monkeypatch.setattr(control, "mark_execution_dispatch_dispatched", original)
    second = service.dispatch_pending()
    requests = queue.list_for_project(run.project_run_id)
    assert second.dispatched_request_ids == (first_request.worker_request_id,)
    assert len(requests) == 1
    assert requests[0].worker_request_id == first_request.worker_request_id


def test_invalid_outbox_contract_is_cancelled_and_attempt_recovers(runtime) -> None:
    control, queue, service = runtime
    run = begin_dispatched(control, limits={"timeout_seconds": 0})
    dispatch = control.list_execution_dispatches(run.project_run_id)[0]

    report = service.dispatch_pending()

    assert report.recovered_dispatch_ids == (dispatch.execution_dispatch_id,)
    assert queue.list_for_project(run.project_run_id) == []
    cancelled = control.get_execution_dispatch(dispatch.execution_dispatch_id)
    assert cancelled.status == ExecutionDispatchStatus.CANCELLED
    assert cancelled.failure_classification == "invalid_dispatch_contract"
    attempt = control.list_attempts(run.project_run_id)[0]
    assert attempt.status == ExecutionAttemptStatus.INTERRUPTED


def test_project_cancellation_cancels_pending_dispatch_without_enqueue(runtime) -> None:
    control, queue, service = runtime
    run = begin_dispatched(control)
    dispatch = control.list_execution_dispatches(run.project_run_id)[0]

    control.execute(command(ProjectCommandType.CANCEL_PROJECT, run, "cancel-project", payload={
        "reason": "user_cancelled",
    }))

    cancelled = control.get_execution_dispatch(dispatch.execution_dispatch_id)
    assert cancelled.status == ExecutionDispatchStatus.CANCELLED
    assert service.dispatch_pending().dispatched_request_ids == ()
    assert queue.list_for_project(run.project_run_id) == []


def test_invalid_dispatch_rolls_back_attempt_event_and_outbox(runtime) -> None:
    control, _, _ = runtime
    before = control.get_project("project-dispatch")

    with pytest.raises(ProjectControlError) as error:
        control.execute(command(ProjectCommandType.BEGIN_WORK_UNIT, before, "invalid-dispatch", payload={
            "work_unit_id": "work-1",
            "worker_dispatch": {"payload": {}, "limits": "not-an-object"},
        }, authority={"work_unit_id": "work-1"}))

    assert error.value.code == ProjectControlErrorCode.INVALID_COMMAND
    after = control.get_project(before.project_run_id)
    assert after.state_version == before.state_version
    assert control.list_attempts(before.project_run_id) == []
    assert control.list_execution_dispatches(before.project_run_id) == []