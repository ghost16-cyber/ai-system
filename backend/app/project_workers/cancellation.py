from __future__ import annotations

from dataclasses import dataclass

from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import (
    ExecutionCancellation,
    ExecutionCancellationStatus,
    ProjectCommand,
    ProjectCommandType,
    ProjectControlError,
    ProjectControlPlane,
)
from backend.app.project_control.contracts import content_hash
from backend.app.project_workers.contracts import TERMINAL_WORKER_STATUSES
from backend.app.project_workers.errors import ProjectWorkerError


@dataclass(frozen=True)
class CancellationDispatchReport:
    dispatched_cancellation_ids: tuple[str, ...] = ()
    acknowledged_cancellation_ids: tuple[str, ...] = ()
    deferred_cancellation_ids: tuple[str, ...] = ()


class CancellationDispatcher:
    """Delivers canonical cancellation requests and records worker acknowledgement."""

    def __init__(
        self,
        control: ProjectControlPlane,
        worker_service,
        artifact_store: ProjectArtifactStore,
    ) -> None:
        self.control = control
        self.worker_service = worker_service
        self.artifact_store = artifact_store

    def dispatch_pending(self, *, limit: int = 100) -> CancellationDispatchReport:
        dispatched: list[str] = []
        acknowledged: list[str] = []
        deferred: list[str] = []
        cancellations = self.control.list_execution_cancellations(
            statuses=(
                ExecutionCancellationStatus.PENDING,
                ExecutionCancellationStatus.FAILED,
                ExecutionCancellationStatus.DISPATCHED,
            ),
            limit=limit,
        )
        for cancellation in cancellations:
            try:
                current = cancellation
                if cancellation.status != ExecutionCancellationStatus.DISPATCHED:
                    if cancellation.worker_request_id:
                        self.worker_service.request_cancel(
                            cancellation.worker_request_id,
                            requested_by=cancellation.requested_by,
                        )
                    current = self.control.mark_execution_cancellation_dispatched(
                        cancellation.cancellation_id
                    )
                    dispatched.append(current.cancellation_id)
                if self.acknowledge(current.cancellation_id):
                    acknowledged.append(current.cancellation_id)
            except (ProjectWorkerError, ProjectControlError, LookupError, ValueError):
                self.control.mark_execution_cancellation_failed(
                    cancellation.cancellation_id,
                    failure_classification="cancellation_delivery_failed",
                )
                deferred.append(cancellation.cancellation_id)
        return CancellationDispatchReport(
            dispatched_cancellation_ids=tuple(dispatched),
            acknowledged_cancellation_ids=tuple(acknowledged),
            deferred_cancellation_ids=tuple(deferred),
        )

    def acknowledge(self, cancellation_id: str) -> bool:
        cancellation = self.control.get_execution_cancellation(cancellation_id)
        if cancellation.status == ExecutionCancellationStatus.ACKNOWLEDGED:
            if cancellation.worker_request_id:
                request = self.worker_service.queue.get(
                    cancellation.worker_request_id
                )
                if (
                    request.status in TERMINAL_WORKER_STATUSES
                    and request.canonical_reconciled_at is None
                ):
                    self.worker_service.queue.mark_canonical_reconciled(
                        request.worker_request_id
                    )
            return True
        request = None
        if cancellation.worker_request_id:
            request = self.worker_service.queue.get(cancellation.worker_request_id)
            if request.status not in TERMINAL_WORKER_STATUSES:
                return False
        run = self.control.get_project(cancellation.project_run_id)
        attempt = next((
            item
            for item in self.control.list_attempts(cancellation.project_run_id)
            if item.execution_attempt_id == cancellation.execution_attempt_id
        ), None)
        if attempt is None:
            return False
        worker_status = request.status.value if request is not None else "cancelled"
        failure = (
            request.failure_classification if request is not None else None
        ) or "execution_cancelled"
        artifact = self.artifact_store.put(build_project_artifact(
            artifact_type=ProjectArtifactType.EXECUTION_RESULT,
            binding=ProjectArtifactBinding(
                project_run_id=run.project_run_id,
                plan_revision_id=run.current_plan_revision_id,
                scope_revision_id=run.current_scope_revision_id,
                manifest_hash=run.current_manifest_hash,
                execution_attempt_id=attempt.execution_attempt_id,
                authority_hash=content_hash({
                    "cancellation_id": cancellation.cancellation_id,
                    "request_hash": cancellation.request_hash,
                }),
            ),
            payload={
                "reconciliation_key": f"cancellation:{cancellation.cancellation_id}",
                "cancellation_id": cancellation.cancellation_id,
                "worker_request_id": cancellation.worker_request_id,
                "execution_attempt_id": attempt.execution_attempt_id,
                "worker_status": worker_status,
                "failure_classification": failure,
                "result_reference": request.result_reference if request else {},
            },
        ))
        result = self.control.execute(ProjectCommand(
            command_type=ProjectCommandType.ACKNOWLEDGE_EXECUTION_CANCELLATION,
            project_run_id=run.project_run_id,
            conversation_id=run.conversation_id,
            workspace_id=run.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=run.repository_root_fingerprint,
            actor_id=cancellation.requested_by,
            expected_state_version=run.state_version,
            idempotency_key=f"cancellation-ack:{cancellation.cancellation_id}:{worker_status}",
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            authority_scope={
                "cancellation_id": cancellation.cancellation_id,
                "execution_attempt_id": attempt.execution_attempt_id,
                "worker_request_id": cancellation.worker_request_id,
            },
            payload={
                "cancellation_id": cancellation.cancellation_id,
                "execution_attempt_id": attempt.execution_attempt_id,
                "worker_request_id": cancellation.worker_request_id,
                "worker_status": worker_status,
                "failure_classification": failure,
            },
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type.value,
            artifact_hash=artifact.content_hash,
            artifact_binding_hash=artifact.binding_hash,
        ))
        del result
        if request is not None:
            self.worker_service.queue.mark_canonical_reconciled(
                request.worker_request_id
            )
        return True

    def recover(self, *, limit: int = 100) -> CancellationDispatchReport:
        report = self.dispatch_pending(limit=limit)
        acknowledged = list(report.acknowledged_cancellation_ids)
        deferred = list(report.deferred_cancellation_ids)
        for cancellation in self.control.list_execution_cancellations(
            statuses=(ExecutionCancellationStatus.ACKNOWLEDGED,),
            limit=limit,
        ):
            if not cancellation.worker_request_id:
                continue
            try:
                request = self.worker_service.queue.get(
                    cancellation.worker_request_id
                )
                if request.canonical_reconciled_at is None:
                    self.acknowledge(cancellation.cancellation_id)
                    acknowledged.append(cancellation.cancellation_id)
            except (ProjectWorkerError, ProjectControlError, LookupError, ValueError):
                deferred.append(cancellation.cancellation_id)
        return CancellationDispatchReport(
            dispatched_cancellation_ids=report.dispatched_cancellation_ids,
            acknowledged_cancellation_ids=tuple(dict.fromkeys(acknowledged)),
            deferred_cancellation_ids=tuple(dict.fromkeys(deferred)),
        )


__all__ = ["CancellationDispatcher", "CancellationDispatchReport"]
