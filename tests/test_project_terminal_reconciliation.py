from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from backend.app.project_artifacts import ProjectArtifactType
from backend.app.project_control import ProjectLifecycle
from backend.app.project_coordinator import (
    CoordinatorIntentError,
    ProjectCoordinatorService,
)
from backend.app.project_workers import (
    WorkerCompletion,
    WorkerCompletionOutcome,
)
from tests.test_project_worker_execution import _runtime


def test_terminal_result_artifact_and_canonical_command_recover_exactly_once(
    tmp_path, monkeypatch
) -> None:
    _root, _script, control, queue, service, request, _executor = _runtime(
        tmp_path, "print('terminal')\n"
    )
    lease = queue.claim_next("worker-terminal", [request.attempt_type])
    assert lease is not None
    original_mark = queue.mark_canonical_reconciled
    failures = 0

    def crash_after_canonical(worker_request_id, **kwargs):
        nonlocal failures
        failures += 1
        if failures == 1:
            raise RuntimeError("simulated crash after canonical command")
        return original_mark(worker_request_id, **kwargs)

    monkeypatch.setattr(queue, "mark_canonical_reconciled", crash_after_canonical)
    completion = WorkerCompletion(
        worker_request_id=request.worker_request_id,
        worker_id="worker-terminal",
        lease_token=lease.lease_token,
        idempotency_key="terminal-result",
        outcome=WorkerCompletionOutcome.SUCCEEDED,
        result_reference={"exit_code": 0, "result_hash": "a" * 64},
    )
    try:
        service.complete(completion)
    except RuntimeError as error:
        assert "simulated crash" in str(error)
    before_events = len(control.list_events(request.project_run_id))
    assert control.get_project(request.project_run_id).lifecycle_status == ProjectLifecycle.VERIFICATION_PENDING
    assert queue.get(request.worker_request_id).canonical_reconciled_at is None

    monkeypatch.setattr(queue, "mark_canonical_reconciled", original_mark)
    report = service.recover_expired_leases()

    assert report.canonical_recovery_ids == (request.worker_request_id,)
    assert queue.get(request.worker_request_id).canonical_reconciled_at is not None
    assert len(control.list_events(request.project_run_id)) == before_events
    artifacts = control.artifact_store.list_for_project(
        request.project_run_id, artifact_type=ProjectArtifactType.EXECUTION_RESULT
    )
    assert len(artifacts) == 1
    assert artifacts[0].payload["worker_request_id"] == request.worker_request_id
    read = control.get_read_model(request.project_run_id)
    assert read.current_execution_result_artifact_id == artifacts[0].artifact_id
    assert read.current_execution_result_artifact_hash == artifacts[0].content_hash


def test_duplicate_terminal_reconciliation_never_creates_another_result(
    tmp_path,
) -> None:
    _root, _script, control, queue, service, request, _executor = _runtime(
        tmp_path, "print('once')\n"
    )
    lease = queue.claim_next("worker-concurrent", [request.attempt_type])
    assert lease is not None
    queue.complete(WorkerCompletion(
        worker_request_id=request.worker_request_id,
        worker_id="worker-concurrent",
        lease_token=lease.lease_token,
        idempotency_key="concurrent-result",
        outcome=WorkerCompletionOutcome.SUCCEEDED,
        result_reference={"exit_code": 0},
    ))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _index: service.terminal_reconciler.reconcile(
                request.worker_request_id
            ),
            range(2),
        ))

    assert results == [True, True]
    before_events = len(control.list_events(request.project_run_id))
    before_artifacts = control.artifact_store.list_for_project(
        request.project_run_id,
        artifact_type=ProjectArtifactType.EXECUTION_RESULT,
    )

    assert service.terminal_reconciler.reconcile(request.worker_request_id) is True
    assert service.terminal_reconciler.reconcile(request.worker_request_id) is True

    assert len(control.list_events(request.project_run_id)) == before_events
    assert control.artifact_store.list_for_project(
        request.project_run_id, artifact_type=ProjectArtifactType.EXECUTION_RESULT
    ) == before_artifacts


def test_failed_intent_creation_recovers_without_worker_reexecution(
    tmp_path, monkeypatch
) -> None:
    _root, _script, control, queue, service, request, executor = _runtime(
        tmp_path, "raise SystemExit(2)\n"
    )
    coordinator = ProjectCoordinatorService(control.database_path, control)
    coordinator.initialize()
    service.terminal_reconciler.coordinator = coordinator
    original = coordinator.reconcile
    monkeypatch.setattr(
        coordinator,
        "reconcile",
        lambda _project_run_id: (_ for _ in ()).throw(
            CoordinatorIntentError("intent store unavailable")
        ),
    )

    assert executor.run_once("worker-intent-gap") is True
    assert queue.get(request.worker_request_id).canonical_reconciled_at is not None
    assert coordinator.list_for_project(request.project_run_id) == []

    monkeypatch.setattr(coordinator, "reconcile", original)
    recovered = coordinator.reconcile_all()
    assert len(recovered) == 1
    assert len(coordinator.list_for_project(request.project_run_id)) == 1
    assert executor.run_once("worker-intent-recovery") is False


def test_crash_after_result_artifact_before_canonical_command_reuses_artifact(
    tmp_path, monkeypatch
) -> None:
    _root, _script, control, queue, service, request, _executor = _runtime(
        tmp_path, "print('artifact-boundary')\n"
    )
    lease = queue.claim_next("worker-artifact-gap", [request.attempt_type])
    assert lease is not None
    queue.complete(WorkerCompletion(
        worker_request_id=request.worker_request_id,
        worker_id="worker-artifact-gap",
        lease_token=lease.lease_token,
        idempotency_key="artifact-gap-result",
        outcome=WorkerCompletionOutcome.SUCCEEDED,
        result_reference={"exit_code": 0},
    ))
    original_execute = control.execute
    monkeypatch.setattr(
        control,
        "execute",
        lambda _command: (_ for _ in ()).throw(
            RuntimeError("crash after artifact store")
        ),
    )
    try:
        service.terminal_reconciler.reconcile(request.worker_request_id)
    except RuntimeError as error:
        assert "after artifact" in str(error)
    artifacts = control.artifact_store.list_for_project(
        request.project_run_id,
        artifact_type=ProjectArtifactType.EXECUTION_RESULT,
    )
    assert len(artifacts) == 1
    assert queue.get(request.worker_request_id).canonical_reconciled_at is None

    monkeypatch.setattr(control, "execute", original_execute)
    assert service.terminal_reconciler.reconcile(request.worker_request_id) is True
    assert control.artifact_store.list_for_project(
        request.project_run_id,
        artifact_type=ProjectArtifactType.EXECUTION_RESULT,
    ) == artifacts
