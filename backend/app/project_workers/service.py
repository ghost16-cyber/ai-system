from __future__ import annotations

from datetime import datetime
from typing import Iterable

from backend.app.project_control import (
    ExecutionDispatch,
    ProjectCommand,
    ProjectCommandType,
    ProjectControlError,
    ProjectControlErrorCode,
    ProjectControlPlane,
)
from backend.app.project_control.contracts import (
    ExecutionAttemptStatus,
    ExecutionAttemptType,
    TERMINAL_LIFECYCLES,
)
from backend.app.project_workers.contracts import (
    ProjectWorkerRequest,
    WorkerCompletion,
    WorkerDispatchReport,
    WorkerEnqueueCommand,
    WorkerLease,
    WorkerLimits,
    WorkerRecoveryReport,
    WorkerRuntimeInstance,
)
from backend.app.project_workers.errors import ProjectWorkerError, ProjectWorkerErrorCode
from backend.app.project_workers.queue import ProjectWorkerQueue
from backend.app.project_workers.reconciliation import TerminalResultReconciler
from backend.app.project_artifacts import ProjectArtifactStore


class ProjectWorkerService:
    """Coordinates worker durability without becoming a lifecycle authority."""

    def __init__(
        self,
        control: ProjectControlPlane,
        queue: ProjectWorkerQueue,
        *,
        terminal_reconciler: TerminalResultReconciler | None = None,
    ) -> None:
        self.control = control
        self.queue = queue
        artifact_store = control.artifact_store or ProjectArtifactStore(control.database_path)
        if control.artifact_store is None:
            control.artifact_store = artifact_store
        self.terminal_reconciler = terminal_reconciler or TerminalResultReconciler(
            control, queue, artifact_store
        )

    def enqueue(self, command: WorkerEnqueueCommand | dict) -> ProjectWorkerRequest:
        return self.queue.enqueue(command)

    def record_runtime_heartbeat(
        self,
        worker_id: str,
        *,
        execution_backend: str,
        supported_attempt_types: Iterable[ExecutionAttemptType | str],
        supported_toolchains: Iterable[str] = (),
    ) -> WorkerRuntimeInstance:
        return self.queue.record_runtime_heartbeat(
            worker_id,
            execution_backend=execution_backend,
            supported_attempt_types=supported_attempt_types,
            supported_toolchains=supported_toolchains,
        )

    def list_active_runtime_instances(self) -> list[WorkerRuntimeInstance]:
        return self.queue.list_active_runtime_instances()

    def mark_runtime_stopped(self, worker_id: str) -> None:
        self.queue.mark_runtime_stopped(worker_id)

    def dispatch_pending(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> WorkerDispatchReport:
        dispatched: list[str] = []
        recovered: list[str] = []
        deferred: list[str] = []
        for dispatch in self.control.list_pending_execution_dispatches(now=now, limit=limit):
            try:
                command = WorkerEnqueueCommand(
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
                request = self.queue.enqueue(command)
                self.control.mark_execution_dispatch_dispatched(
                    dispatch.execution_dispatch_id,
                    request.worker_request_id,
                )
                dispatched.append(request.worker_request_id)
            except ProjectWorkerError as error:
                if error.code == ProjectWorkerErrorCode.PERSISTENCE_CONFLICT:
                    deferred.append(dispatch.execution_dispatch_id)
                    continue
                if self._cancel_dispatch(dispatch, error.code.value):
                    recovered.append(dispatch.execution_dispatch_id)
                else:
                    deferred.append(dispatch.execution_dispatch_id)
            except ValueError:
                if self._cancel_dispatch(dispatch, "invalid_dispatch_contract"):
                    recovered.append(dispatch.execution_dispatch_id)
                else:
                    deferred.append(dispatch.execution_dispatch_id)
            except ProjectControlError as error:
                if error.code == ProjectControlErrorCode.PERSISTENCE_CONFLICT:
                    current = self.control.get_execution_dispatch(dispatch.execution_dispatch_id)
                    if current.worker_request_id == request.worker_request_id:
                        dispatched.append(request.worker_request_id)
                    else:
                        deferred.append(dispatch.execution_dispatch_id)
                    continue
                deferred.append(dispatch.execution_dispatch_id)
        return WorkerDispatchReport(
            dispatched_request_ids=tuple(dispatched),
            recovered_dispatch_ids=tuple(recovered),
            deferred_dispatch_ids=tuple(deferred),
        )

    def claim_next(
        self,
        worker_id: str,
        supported_attempt_types: Iterable[ExecutionAttemptType | str],
        *,
        lease_seconds: int | None = None,
        now: datetime | None = None,
    ) -> WorkerLease | None:
        return self.queue.claim_next(
            worker_id,
            supported_attempt_types,
            lease_seconds=lease_seconds,
            now=now,
        )

    def heartbeat(
        self,
        worker_request_id: str,
        worker_id: str,
        lease_token: str,
        *,
        extend_seconds: int | None = None,
        now: datetime | None = None,
    ) -> ProjectWorkerRequest:
        return self.queue.heartbeat(
            worker_request_id,
            worker_id,
            lease_token,
            extend_seconds=extend_seconds,
            now=now,
        )

    def request_cancel(self, worker_request_id: str, *, requested_by: str = "local-user") -> ProjectWorkerRequest:
        request = self.queue.request_cancel(
            worker_request_id, requested_by=requested_by
        )
        if request.status.value == "cancelled" and self._reconcile_terminal(request):
            request = self.queue.get(worker_request_id)
        return request

    def complete(self, completion: WorkerCompletion | dict, *, now: datetime | None = None) -> ProjectWorkerRequest:
        request = self.queue.complete(completion, now=now)
        if self._reconcile_terminal(request):
            request = self.queue.get(request.worker_request_id)
        return request

    def recover_expired_leases(self, *, now: datetime | None = None) -> WorkerRecoveryReport:
        recovered = self.queue.recover_expired_leases(now=now)
        canonical: list[str] = []
        skipped: list[str] = []
        for request in self.queue.list_unreconciled_failures():
            if self._reconcile_terminal(request):
                canonical.append(request.worker_request_id)
            else:
                skipped.append(request.worker_request_id)
        return WorkerRecoveryReport(
            recovered_request_ids=tuple(item.worker_request_id for item in recovered),
            canonical_recovery_ids=tuple(canonical),
            skipped_request_ids=tuple(skipped),
        )

    def _cancel_dispatch(self, dispatch: ExecutionDispatch, classification: str) -> bool:
        try:
            self.control.mark_execution_dispatch_cancelled(
                dispatch.execution_dispatch_id,
                failure_classification=classification,
            )
        except ProjectControlError as error:
            if error.code != ProjectControlErrorCode.PERSISTENCE_CONFLICT:
                return False
            current = self.control.get_execution_dispatch(dispatch.execution_dispatch_id)
            if current.status.value != "cancelled":
                return False
        try:
            return self._recover_dispatch_attempt(dispatch, classification)
        except ProjectControlError:
            return False

    def _recover_dispatch_attempt(self, dispatch: ExecutionDispatch, reason: str) -> bool:
        for retry in range(2):
            run = self.control.get_project(dispatch.project_run_id)
            if run.lifecycle_status in TERMINAL_LIFECYCLES:
                return True
            attempt = next(
                (
                    item
                    for item in self.control.list_attempts(dispatch.project_run_id)
                    if item.execution_attempt_id == dispatch.execution_attempt_id
                ),
                None,
            )
            if attempt is None:
                return False
            if attempt.status not in {
                ExecutionAttemptStatus.PENDING,
                ExecutionAttemptStatus.ACTIVE,
            }:
                return True
            try:
                self.control.execute(
                    ProjectCommand(
                        command_type=ProjectCommandType.RECOVER_ATTEMPT,
                        project_run_id=run.project_run_id,
                        conversation_id=run.conversation_id,
                        workspace_id=run.workspace_id,
                        repository_root=run.repository_root,
                        repository_root_fingerprint=run.repository_root_fingerprint,
                        actor_id=run.actor_id,
                        expected_state_version=run.state_version,
                        idempotency_key=f"dispatch-recovery:{dispatch.execution_dispatch_id}:{reason}",
                        plan_revision_id=run.current_plan_revision_id,
                        scope_revision_id=run.current_scope_revision_id,
                        manifest_hash=run.current_manifest_hash,
                        authority_scope={
                            "execution_dispatch_id": dispatch.execution_dispatch_id,
                            "execution_attempt_id": dispatch.execution_attempt_id,
                        },
                        payload={
                            "execution_attempt_id": dispatch.execution_attempt_id,
                            "reason": reason,
                        },
                    )
                )
                return True
            except ProjectControlError as error:
                if error.code == ProjectControlErrorCode.STALE_STATE_VERSION and retry == 0:
                    continue
                if error.code in {
                    ProjectControlErrorCode.ILLEGAL_TRANSITION,
                    ProjectControlErrorCode.TERMINAL_PROJECT,
                }:
                    return True
                raise
        return False

    def _reconcile_terminal(self, request: ProjectWorkerRequest) -> bool:
        return self.terminal_reconciler.reconcile(request.worker_request_id)

    def _reconcile_result(self, request: ProjectWorkerRequest, *, succeeded: bool) -> bool:
        del succeeded
        return self.terminal_reconciler.reconcile(request.worker_request_id)

    def _recover_canonical_attempt(self, request: ProjectWorkerRequest) -> bool:
        return self.terminal_reconciler.reconcile(request.worker_request_id)

    def _attempt(self, request: ProjectWorkerRequest):
        return next(
            (
                item
                for item in self.control.list_attempts(request.project_run_id)
                if item.execution_attempt_id == request.execution_attempt_id
            ),
            None,
        )


__all__ = ["ProjectWorkerService"]
