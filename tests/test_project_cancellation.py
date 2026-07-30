from __future__ import annotations

from backend.app.project_artifacts import ProjectArtifactType
from backend.app.project_control import (
    ExecutionAttemptStatus,
    ExecutionCancellationStatus,
    ExecutionDispatchStatus,
    ProjectCommandType,
    ProjectLifecycle,
)
from backend.app.project_workers import CancellationDispatcher, WorkerRequestStatus
from backend.app.project_workers import WorkerCompletion, WorkerCompletionOutcome
from tests.test_project_worker_execution import _project_command, _runtime


def test_enqueued_cancellation_waits_for_worker_ack_and_converges_once(tmp_path) -> None:
    _root, _script, control, queue, service, request, _executor = _runtime(
        tmp_path,
        "print('never-run')\n",
        through_outbox=True,
    )
    run = control.get_project(request.project_run_id)
    result = control.execute(_project_command(
        ProjectCommandType.CANCEL_PROJECT,
        run,
        "cancel-running-command",
        payload={"reason": "user_cancelled"},
    ))

    cancellation = control.list_execution_cancellations()[0]
    assert result.read_model["pending_user_action"] == "cancelling"
    assert control.get_project(run.project_run_id).lifecycle_status == ProjectLifecycle.WORK_IN_PROGRESS
    assert control.list_attempts(run.project_run_id)[-1].status == ExecutionAttemptStatus.CANCELLING
    assert cancellation.status == ExecutionCancellationStatus.PENDING
    assert cancellation.worker_request_id == request.worker_request_id

    dispatcher = CancellationDispatcher(control, service, control.artifact_store)
    report = dispatcher.recover()

    assert report.acknowledged_cancellation_ids == (cancellation.cancellation_id,)
    assert queue.get(request.worker_request_id).status == WorkerRequestStatus.CANCELLED
    assert queue.get(request.worker_request_id).canonical_reconciled_at is not None
    assert control.get_project(run.project_run_id).lifecycle_status == ProjectLifecycle.CANCELLED
    assert control.get_execution_cancellation(cancellation.cancellation_id).status == ExecutionCancellationStatus.ACKNOWLEDGED
    assert control.list_attempts(run.project_run_id)[-1].status == ExecutionAttemptStatus.CANCELLED
    artifacts = control.artifact_store.list_for_project(
        run.project_run_id, artifact_type=ProjectArtifactType.EXECUTION_RESULT
    )
    assert len(artifacts) == 1
    before_events = len(control.list_events(run.project_run_id))
    assert dispatcher.recover().acknowledged_cancellation_ids == ()
    assert len(control.list_events(run.project_run_id)) == before_events


def test_pre_dispatch_cancellation_is_terminal_without_worker_delivery(tmp_path) -> None:
    _root, _script, control, queue, _service, request, _executor = _runtime(
        tmp_path,
        "print('never-enqueued')\n",
        through_outbox=True,
    )
    # Recreate the crash boundary before queue delivery.
    with queue._connect() as connection:
        connection.execute(
            "DELETE FROM project_worker_events WHERE worker_request_id = ?",
            (request.worker_request_id,),
        )
        connection.execute(
            "DELETE FROM project_worker_idempotency WHERE project_run_id = ?",
            (request.project_run_id,),
        )
        connection.execute(
            "DELETE FROM project_worker_requests WHERE worker_request_id = ?",
            (request.worker_request_id,),
        )
    dispatch = control.list_execution_dispatches(request.project_run_id)[0]
    with control._connect() as connection:
        stored = dispatch.model_copy(update={
            "status": ExecutionDispatchStatus.PENDING,
            "worker_request_id": None,
            "dispatched_at": None,
        })
        connection.execute(
            "UPDATE project_execution_dispatches SET status = 'pending', worker_request_id = NULL, "
            "dispatched_at = NULL, dispatch_json = ? WHERE execution_dispatch_id = ?",
            (stored.model_dump_json(), dispatch.execution_dispatch_id),
        )
    run = control.get_project(request.project_run_id)
    control.execute(_project_command(
        ProjectCommandType.CANCEL_PROJECT,
        run,
        "cancel-before-dispatch",
    ))
    assert control.get_project(run.project_run_id).lifecycle_status == ProjectLifecycle.CANCELLED
    assert control.list_execution_cancellations() == []
    assert queue.list_for_project(run.project_run_id) == []


def test_leased_cancellation_stays_cancelling_until_worker_acknowledges(tmp_path) -> None:
    _root, _script, control, queue, service, request, _executor = _runtime(
        tmp_path,
        "print('cancel-running')\n",
        through_outbox=True,
    )
    lease = queue.claim_next("worker-cancel", [request.attempt_type])
    assert lease is not None
    run = control.get_project(request.project_run_id)
    control.execute(_project_command(
        ProjectCommandType.CANCEL_PROJECT,
        run,
        "cancel-leased",
    ))
    dispatcher = CancellationDispatcher(control, service, control.artifact_store)

    first = dispatcher.dispatch_pending()
    assert first.acknowledged_cancellation_ids == ()
    assert queue.get(request.worker_request_id).status == WorkerRequestStatus.CANCEL_REQUESTED
    assert control.get_project(run.project_run_id).lifecycle_status == ProjectLifecycle.WORK_IN_PROGRESS

    service.complete(WorkerCompletion(
        worker_request_id=request.worker_request_id,
        worker_id="worker-cancel",
        lease_token=lease.lease_token,
        idempotency_key="cancel-ack",
        outcome=WorkerCompletionOutcome.CANCELLED,
        failure_classification="worker_acknowledged_cancel",
    ))
    second = dispatcher.recover()
    assert len(second.acknowledged_cancellation_ids) == 1
    assert control.get_project(run.project_run_id).lifecycle_status == ProjectLifecycle.CANCELLED


def test_cancellation_recovers_after_canonical_ack_before_queue_reconciliation(
    tmp_path, monkeypatch
) -> None:
    _root, _script, control, queue, service, request, _executor = _runtime(
        tmp_path,
        "print('cancel-crash')\n",
        through_outbox=True,
    )
    run = control.get_project(request.project_run_id)
    control.execute(_project_command(
        ProjectCommandType.CANCEL_PROJECT,
        run,
        "cancel-crash-boundary",
    ))
    dispatcher = CancellationDispatcher(control, service, control.artifact_store)
    original_mark = queue.mark_canonical_reconciled
    monkeypatch.setattr(
        queue,
        "mark_canonical_reconciled",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("crash before queue reconciliation")
        ),
    )
    try:
        dispatcher.dispatch_pending()
    except RuntimeError:
        pass
    cancellation = control.list_execution_cancellations()[0]
    assert cancellation.status == ExecutionCancellationStatus.ACKNOWLEDGED
    assert queue.get(request.worker_request_id).canonical_reconciled_at is None

    monkeypatch.setattr(queue, "mark_canonical_reconciled", original_mark)
    recovered = dispatcher.recover()
    assert recovered.acknowledged_cancellation_ids == (
        cancellation.cancellation_id,
    )
    assert queue.get(request.worker_request_id).canonical_reconciled_at is not None
