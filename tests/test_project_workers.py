from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlPlane,
    ProjectLifecycle,
)
from backend.app.project_control.contracts import ExecutionAttemptType, content_hash
from backend.app.project_workers import (
    ProjectWorkerError,
    ProjectWorkerErrorCode,
    ProjectWorkerQueue,
    ProjectWorkerService,
    WorkerCompletion,
    WorkerCompletionOutcome,
    WorkerEnqueueCommand,
    WorkerRequestStatus,
)


def base() -> dict[str, str]:
    return {
        "project_run_id": "project-1",
        "conversation_id": "conversation-1",
        "workspace_id": "workspace-1",
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
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.ATTACH_SPECIFICATION, run, "specification", payload={
        "task_specification_id": "specification-1",
        "specification_hash": content_hash({"specification": "one"}),
        "included_paths": ["backend/app.py"],
        "allowed_operations": ["read", "patch", "verify"],
    }))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.REGISTER_MANIFEST, run, "manifest", payload={
        "manifest_hash": content_hash({"files": {"backend/app.py": "abc"}}),
        "complete": True,
    }))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.PROPOSE_PLAN_REVISION, run, "plan", payload={
        "acceptance_criteria": [{
            "criterion_id": "criterion-1",
            "required": True,
            "verification_mode": "structural_code_inspection",
        }],
        "work_units": [{"work_unit_id": "work-1", "objective": "Change the bounded file."}],
    }))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.APPROVE_PLAN, run, "approve-plan", authority={
        "operation": "prepare_work_units",
        "work_unit_ids": ["work-1"],
    }))
    run = control.get_project("project-1")
    control.execute(command(ProjectCommandType.BEGIN_WORK_UNIT, run, "begin-work", payload={
        "work_unit_id": "work-1",
    }, authority={"work_unit_id": "work-1"}))

    queue = ProjectWorkerQueue(database)
    queue.initialize()
    service = ProjectWorkerService(control, queue)
    return control, queue, service


def enqueue_command(control: ProjectControlPlane, *, key: str = "worker-enqueue", payload=None) -> WorkerEnqueueCommand:
    run = control.get_project("project-1")
    attempt = control.list_attempts(run.project_run_id)[-1]
    return WorkerEnqueueCommand(
        project_run_id=run.project_run_id,
        execution_attempt_id=attempt.execution_attempt_id,
        attempt_type=attempt.attempt_type,
        conversation_id=run.conversation_id,
        workspace_id=run.workspace_id,
        repository_root=run.repository_root,
        repository_root_fingerprint=run.repository_root_fingerprint,
        actor_id=run.actor_id,
        plan_revision_id=str(run.current_plan_revision_id),
        scope_revision_id=str(run.current_scope_revision_id),
        manifest_hash=str(run.current_manifest_hash),
        expected_project_state_version=run.state_version,
        authority=attempt.authority,
        payload=payload or {"work_unit_id": "work-1"},
        idempotency_key=key,
    )


def test_enqueue_is_exactly_bound_and_idempotent(runtime) -> None:
    control, queue, _service = runtime
    request = enqueue_command(control)
    first = queue.enqueue(request)
    second = queue.enqueue(request)
    assert second == first
    assert first.status == WorkerRequestStatus.QUEUED
    assert first.execution_attempt_id == control.list_attempts("project-1")[-1].execution_attempt_id
    assert [event.event_type.value for event in queue.list_events(first.worker_request_id)] == ["enqueued"]


def test_changed_payload_with_same_idempotency_key_conflicts(runtime) -> None:
    control, queue, _service = runtime
    queue.enqueue(enqueue_command(control))
    with pytest.raises(ProjectWorkerError) as caught:
        queue.enqueue(enqueue_command(control, payload={"work_unit_id": "different"}))
    assert caught.value.code == ProjectWorkerErrorCode.IDEMPOTENCY_CONFLICT


def test_stale_project_version_and_manifest_fail_closed(runtime) -> None:
    control, queue, _service = runtime
    current = enqueue_command(control)
    with pytest.raises(ProjectWorkerError) as stale:
        queue.enqueue(current.model_copy(update={"expected_project_state_version": current.expected_project_state_version - 1}))
    assert stale.value.code == ProjectWorkerErrorCode.STALE_PROJECT_STATE
    with pytest.raises(ProjectWorkerError) as manifest:
        queue.enqueue(current.model_copy(update={"idempotency_key": "wrong-manifest", "manifest_hash": "0" * 64}))
    assert manifest.value.code == ProjectWorkerErrorCode.INVALID_BINDING


def test_concurrent_claim_has_one_winner(runtime) -> None:
    control, queue, _service = runtime
    request = queue.enqueue(enqueue_command(control))

    def claim(worker_id: str):
        return queue.claim_next(worker_id, [ExecutionAttemptType.WORK_UNIT])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["worker-a", "worker-b"]))
    leases = [item for item in results if item is not None]
    assert len(leases) == 1
    assert leases[0].request.worker_request_id == request.worker_request_id
    assert queue.get(request.worker_request_id).delivery_count == 1


def test_heartbeat_requires_exact_lease_token(runtime) -> None:
    control, queue, _service = runtime
    request = queue.enqueue(enqueue_command(control))
    lease = queue.claim_next("worker-a", [ExecutionAttemptType.WORK_UNIT])
    assert lease is not None
    with pytest.raises(ProjectWorkerError) as caught:
        queue.heartbeat(request.worker_request_id, "worker-a", "x" * 32)
    assert caught.value.code == ProjectWorkerErrorCode.LEASE_MISMATCH
    renewed = queue.heartbeat(request.worker_request_id, "worker-a", lease.lease_token)
    assert renewed.lease_expires_at > lease.request.lease_expires_at


def test_queued_cancel_is_terminal_and_reconciles_canonical_attempt(runtime) -> None:
    control, queue, service = runtime
    request = queue.enqueue(enqueue_command(control))
    cancelled = service.request_cancel(request.worker_request_id)
    assert cancelled.status == WorkerRequestStatus.CANCELLED
    assert cancelled.canonical_reconciled_at is not None
    assert queue.claim_next("worker-a", [ExecutionAttemptType.WORK_UNIT]) is None
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.BLOCKED


def test_leased_cancel_must_be_acknowledged(runtime) -> None:
    control, queue, service = runtime
    request = queue.enqueue(enqueue_command(control))
    lease = queue.claim_next("worker-a", [ExecutionAttemptType.WORK_UNIT])
    assert lease is not None
    cancel_requested = queue.request_cancel(request.worker_request_id)
    assert cancel_requested.status == WorkerRequestStatus.CANCEL_REQUESTED
    with pytest.raises(ProjectWorkerError) as caught:
        service.complete(WorkerCompletion(
            worker_request_id=request.worker_request_id,
            worker_id="worker-a",
            lease_token=lease.lease_token,
            idempotency_key="wrong-success",
            outcome=WorkerCompletionOutcome.SUCCEEDED,
        ))
    assert caught.value.code == ProjectWorkerErrorCode.INVALID_REQUEST
    cancelled = service.complete(WorkerCompletion(
        worker_request_id=request.worker_request_id,
        worker_id="worker-a",
        lease_token=lease.lease_token,
        idempotency_key="cancelled",
        outcome=WorkerCompletionOutcome.CANCELLED,
    ))
    assert cancelled.status == WorkerRequestStatus.CANCELLED
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.BLOCKED


def test_expired_lease_is_interrupted_without_requeue(runtime) -> None:
    control, queue, service = runtime
    request = queue.enqueue(enqueue_command(control))
    lease = queue.claim_next("worker-a", [ExecutionAttemptType.WORK_UNIT], lease_seconds=5)
    assert lease is not None
    report = service.recover_expired_leases(now=lease.request.lease_expires_at + timedelta(seconds=1))
    assert report.recovered_request_ids == (request.worker_request_id,)
    assert report.canonical_recovery_ids == (request.worker_request_id,)
    assert queue.get(request.worker_request_id).status == WorkerRequestStatus.INTERRUPTED
    assert queue.claim_next("worker-b", [ExecutionAttemptType.WORK_UNIT]) is None
    assert control.list_attempts("project-1")[-1].status.value == "interrupted"
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.BLOCKED


def test_failed_completion_is_idempotent_and_blocks_canonical_attempt(runtime) -> None:
    control, queue, service = runtime
    request = queue.enqueue(enqueue_command(control))
    lease = queue.claim_next("worker-a", [ExecutionAttemptType.WORK_UNIT])
    assert lease is not None
    completion = WorkerCompletion(
        worker_request_id=request.worker_request_id,
        worker_id="worker-a",
        lease_token=lease.lease_token,
        idempotency_key="completion-1",
        outcome=WorkerCompletionOutcome.FAILED,
        failure_classification="test_failure",
        result_reference={"log_id": "log-1"},
    )
    first = service.complete(completion)
    second = service.complete(completion)
    assert second == first
    assert first.status == WorkerRequestStatus.FAILED
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.BLOCKED


def test_queue_survives_restart(runtime) -> None:
    control, queue, _service = runtime
    request = queue.enqueue(enqueue_command(control))
    restarted = ProjectWorkerQueue(queue.database_path)
    restarted.initialize()
    assert restarted.get(request.worker_request_id) == request
