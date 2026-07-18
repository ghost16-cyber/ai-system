from __future__ import annotations

from datetime import datetime
from typing import Iterable

from backend.app.project_control import (
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
    content_hash,
)
from backend.app.project_workers.contracts import (
    ProjectWorkerRequest,
    WorkerCompletion,
    WorkerCompletionOutcome,
    WorkerEnqueueCommand,
    WorkerLease,
    WorkerRecoveryReport,
)
from backend.app.project_workers.queue import ProjectWorkerQueue


class ProjectWorkerService:
    """Coordinates worker durability without becoming a lifecycle authority."""

    def __init__(self, control: ProjectControlPlane, queue: ProjectWorkerQueue) -> None:
        self.control = control
        self.queue = queue

    def enqueue(self, command: WorkerEnqueueCommand | dict) -> ProjectWorkerRequest:
        return self.queue.enqueue(command)

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
        request = self.queue.request_cancel(worker_request_id, requested_by=requested_by)
        if request.status.value == "cancelled" and self._reconcile_terminal(request):
            request = self.queue.mark_canonical_reconciled(request.worker_request_id)
        return request

    def complete(self, completion: WorkerCompletion | dict, *, now: datetime | None = None) -> ProjectWorkerRequest:
        request = self.queue.complete(completion, now=now)
        if self._reconcile_terminal(request):
            request = self.queue.mark_canonical_reconciled(request.worker_request_id, now=now)
        return request

    def recover_expired_leases(self, *, now: datetime | None = None) -> WorkerRecoveryReport:
        recovered = self.queue.recover_expired_leases(now=now)
        canonical: list[str] = []
        skipped: list[str] = []
        for request in self.queue.list_unreconciled_failures():
            if self._reconcile_terminal(request):
                self.queue.mark_canonical_reconciled(request.worker_request_id, now=now)
                canonical.append(request.worker_request_id)
            else:
                skipped.append(request.worker_request_id)
        return WorkerRecoveryReport(
            recovered_request_ids=tuple(item.worker_request_id for item in recovered),
            canonical_recovery_ids=tuple(canonical),
            skipped_request_ids=tuple(skipped),
        )

    def _reconcile_terminal(self, request: ProjectWorkerRequest) -> bool:
        if request.status.value == WorkerCompletionOutcome.SUCCEEDED.value:
            return self._reconcile_success(request)
        return self._recover_canonical_attempt(request)

    def _reconcile_success(self, request: ProjectWorkerRequest) -> bool:
        for retry in range(2):
            run = self.control.get_project(request.project_run_id)
            if run.lifecycle_status in TERMINAL_LIFECYCLES:
                return True
            if (
                run.current_plan_revision_id != request.plan_revision_id
                or run.current_scope_revision_id != request.scope_revision_id
                or run.current_manifest_hash != request.manifest_hash
            ):
                return False
            attempt = self._attempt(request)
            if attempt is None:
                return False
            if attempt.status == ExecutionAttemptStatus.COMPLETED:
                return True
            if attempt.status not in {
                ExecutionAttemptStatus.PENDING,
                ExecutionAttemptStatus.ACTIVE,
            }:
                return False

            execution = request.payload.get("execution")
            execution_spec = execution if isinstance(execution, dict) else {}
            if request.attempt_type == ExecutionAttemptType.COMMAND:
                command_id = str(request.authority.get("command_id") or execution_spec.get("command_id") or "")
                if not command_id:
                    return False
                command_type = ProjectCommandType.RECORD_COMMAND_RESULT
                payload = {
                    "command_id": command_id,
                    "succeeded": True,
                    "resulting_manifest_hash": request.resulting_manifest_hash or run.current_manifest_hash,
                    "result_reference": request.result_reference or {},
                }
                authority = {"command_id": command_id}
            elif request.attempt_type == ExecutionAttemptType.VERIFICATION:
                criterion_id = str(request.authority.get("criterion_id") or execution_spec.get("criterion_id") or "")
                criterion_hash = str(execution_spec.get("criterion_hash") or "")
                if not criterion_id or len(criterion_hash) != 64:
                    return False
                reference = request.result_reference or {}
                result_hash = str(reference.get("result_hash") or content_hash(reference))
                command_type = ProjectCommandType.RECORD_VERIFIER_RESULT
                payload = {
                    "criterion_id": criterion_id,
                    "outcome": "passed",
                    "result_hash": result_hash,
                    "criterion_hash": criterion_hash,
                    "plan_revision_id": run.current_plan_revision_id,
                    "scope_revision_id": run.current_scope_revision_id,
                    "manifest_hash": run.current_manifest_hash,
                    "result_reference": reference,
                }
                authority = {"criterion_id": criterion_id}
            else:
                return False

            try:
                self.control.execute(ProjectCommand(
                    command_type=command_type,
                    project_run_id=run.project_run_id,
                    conversation_id=run.conversation_id,
                    workspace_id=run.workspace_id,
                    repository_root=run.repository_root,
                    repository_root_fingerprint=run.repository_root_fingerprint,
                    actor_id=run.actor_id,
                    expected_state_version=run.state_version,
                    idempotency_key=f"worker-success:{request.worker_request_id}:{request.status.value}",
                    plan_revision_id=run.current_plan_revision_id,
                    scope_revision_id=run.current_scope_revision_id,
                    manifest_hash=run.current_manifest_hash,
                    authority_scope=authority,
                    payload=payload,
                ))
                return True
            except ProjectControlError as error:
                if error.code == ProjectControlErrorCode.STALE_STATE_VERSION and retry == 0:
                    continue
                if error.code in {
                    ProjectControlErrorCode.ILLEGAL_TRANSITION,
                    ProjectControlErrorCode.TERMINAL_PROJECT,
                }:
                    refreshed = self._attempt(request)
                    return refreshed is not None and refreshed.status == ExecutionAttemptStatus.COMPLETED
                raise
        return False

    def _recover_canonical_attempt(self, request: ProjectWorkerRequest) -> bool:
        for retry in range(2):
            run = self.control.get_project(request.project_run_id)
            if run.lifecycle_status in TERMINAL_LIFECYCLES:
                return True
            attempt = self._attempt(request)
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
                        idempotency_key=f"worker-recovery:{request.worker_request_id}:{request.status.value}",
                        plan_revision_id=run.current_plan_revision_id,
                        scope_revision_id=run.current_scope_revision_id,
                        manifest_hash=run.current_manifest_hash,
                        authority_scope={
                            "worker_request_id": request.worker_request_id,
                            "execution_attempt_id": request.execution_attempt_id,
                        },
                        payload={
                            "execution_attempt_id": request.execution_attempt_id,
                            "reason": request.failure_classification or request.status.value,
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
