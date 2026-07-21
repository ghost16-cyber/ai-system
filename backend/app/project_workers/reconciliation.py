from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.project_artifacts import (
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import (
    ExecutionAttemptStatus,
    ExecutionAttemptType,
    ProjectCommand,
    ProjectCommandType,
    ProjectControlError,
    ProjectControlErrorCode,
    ProjectControlPlane,
    ExecutionCancellationStatus,
)
from backend.app.project_control.contracts import TERMINAL_LIFECYCLES, content_hash
from backend.app.project_workers.contracts import (
    ProjectWorkerRequest,
    WorkerCompletionOutcome,
)
from backend.app.project_workers.queue import ProjectWorkerQueue
from backend.app.project_repair import CanonicalRepairService

if TYPE_CHECKING:
    from backend.app.project_coordinator import ProjectCoordinatorService
    from backend.app.project_projection.service import ProjectProjectionService


class TerminalResultReconciler:
    """Converges durable queue evidence into one canonical command exactly once."""

    def __init__(
        self,
        control: ProjectControlPlane,
        queue: ProjectWorkerQueue,
        artifact_store: ProjectArtifactStore,
        *,
        projector: ProjectProjectionService | None = None,
        coordinator: ProjectCoordinatorService | None = None,
    ) -> None:
        self.control = control
        self.queue = queue
        self.artifact_store = artifact_store
        self.projector = projector
        self.coordinator = coordinator

    def reconcile(self, worker_request_id: str) -> bool:
        request = self.queue.get(worker_request_id)
        if request.canonical_reconciled_at is not None:
            self._project_and_coordinate(request.project_run_id)
            return True
        if request.finished_at is None:
            return False

        run = self.control.get_project(request.project_run_id)
        attempt = self._attempt(request)
        if attempt is None:
            return False

        pending_cancellation = self.control.get_execution_cancellation_for_attempt(
            request.execution_attempt_id
        )
        if pending_cancellation is not None and pending_cancellation.status in {
            ExecutionCancellationStatus.PENDING,
            ExecutionCancellationStatus.DISPATCHED,
            ExecutionCancellationStatus.FAILED,
        }:
            return False
        artifact = self._terminal_artifact(request)

        if run.lifecycle_status not in TERMINAL_LIFECYCLES:
            command = self._canonical_command(request, run, artifact)
            if command is None:
                return False
            for retry in range(2):
                try:
                    self.control.execute(command)
                    break
                except ProjectControlError as error:
                    if error.code == ProjectControlErrorCode.STALE_STATE_VERSION:
                        return False
                    if error.code in {
                        ProjectControlErrorCode.ILLEGAL_TRANSITION,
                        ProjectControlErrorCode.TERMINAL_PROJECT,
                    }:
                        refreshed = self._attempt(request)
                        if refreshed is None or refreshed.status in {
                            ExecutionAttemptStatus.PENDING,
                            ExecutionAttemptStatus.ACTIVE,
                            ExecutionAttemptStatus.CANCELLING,
                        }:
                            return False
                        break
                    raise

        self.queue.mark_canonical_reconciled(worker_request_id)
        self._project_and_coordinate(request.project_run_id)
        return True

    def _canonical_command(self, request, run, artifact):
        succeeded = request.status.value == WorkerCompletionOutcome.SUCCEEDED.value
        domain_failure = request.failure_classification == "process_exit_nonzero"
        execution = request.payload.get("execution")
        execution_spec = execution if isinstance(execution, dict) else {}
        authority: dict[str, Any]
        payload: dict[str, Any]

        if succeeded or domain_failure:
            if request.attempt_type == ExecutionAttemptType.COMMAND:
                command_id = str(
                    request.authority.get("command_id")
                    or execution_spec.get("command_id")
                    or ""
                )
                if not command_id:
                    return None
                kind = ProjectCommandType.RECORD_COMMAND_RESULT
                authority = {"command_id": command_id}
                payload = {"command_id": command_id}
            elif request.attempt_type == ExecutionAttemptType.VERIFICATION:
                criterion_id = str(
                    request.authority.get("criterion_id")
                    or execution_spec.get("criterion_id")
                    or ""
                )
                criterion_hash = str(execution_spec.get("criterion_hash") or "")
                if not criterion_id or len(criterion_hash) != 64:
                    return None
                reference = request.result_reference or {}
                kind = ProjectCommandType.RECORD_VERIFIER_RESULT
                authority = {"criterion_id": criterion_id}
                payload = {
                    "criterion_id": criterion_id,
                    "outcome": "passed" if succeeded else "failed",
                    "result_hash": str(reference.get("result_hash") or content_hash(reference)),
                    "criterion_hash": criterion_hash,
                    "plan_revision_id": run.current_plan_revision_id,
                    "scope_revision_id": run.current_scope_revision_id,
                    "manifest_hash": run.current_manifest_hash,
                }
            elif request.attempt_type == ExecutionAttemptType.PATCH:
                mutation = request.payload.get("file_mutation")
                mutation_spec = mutation if isinstance(mutation, dict) else {}
                patch_id = str(
                    request.authority.get("patch_id")
                    or mutation_spec.get("authority_id")
                    or ""
                )
                if not patch_id:
                    return None
                kind = ProjectCommandType.RECORD_PATCH_RESULT
                authority = {"patch_id": patch_id}
                payload = {"patch_id": patch_id}
            elif request.attempt_type == ExecutionAttemptType.ROLLBACK:
                mutation = request.payload.get("file_mutation")
                mutation_spec = mutation if isinstance(mutation, dict) else {}
                rollback_id = str(
                    request.authority.get("rollback_id")
                    or mutation_spec.get("authority_id")
                    or ""
                )
                if not rollback_id:
                    return None
                kind = ProjectCommandType.RECORD_ROLLBACK
                authority = {"rollback_id": rollback_id}
                payload = {"rollback_id": rollback_id}
            else:
                return None
            payload.update({
                "execution_attempt_id": request.execution_attempt_id,
                "succeeded": succeeded,
                "resulting_manifest_hash": (
                    request.resulting_manifest_hash or run.current_manifest_hash
                ),
                "result_reference": request.result_reference or {},
            })
            if not succeeded:
                payload["failure_classification"] = (
                    request.failure_classification or "process_exit_nonzero"
                )
                failure_reference = next(
                    (
                        item for item in artifact.evidence_references
                        if item.get("artifact_type") == ProjectArtifactType.FAILURE_EVIDENCE.value
                    ),
                    None,
                )
                if failure_reference is not None:
                    payload["failure_artifact_id"] = failure_reference["artifact_id"]
        else:
            kind = ProjectCommandType.RECOVER_ATTEMPT
            authority = {
                "worker_request_id": request.worker_request_id,
                "execution_attempt_id": request.execution_attempt_id,
            }
            payload = {
                "execution_attempt_id": request.execution_attempt_id,
                "reason": request.failure_classification or request.status.value,
            }

        return ProjectCommand(
            command_type=kind,
            project_run_id=run.project_run_id,
            conversation_id=run.conversation_id,
            workspace_id=run.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=run.repository_root_fingerprint,
            actor_id=run.actor_id,
            expected_state_version=request.expected_project_state_version,
            idempotency_key=f"worker-result:{request.worker_request_id}:{request.status.value}",
            plan_revision_id=request.plan_revision_id,
            scope_revision_id=request.scope_revision_id,
            manifest_hash=request.manifest_hash,
            authority_scope={
                **authority,
                "execution_attempt_id": request.execution_attempt_id,
            },
            payload=payload,
            artifact_id=artifact.artifact_id,
            artifact_type=artifact.artifact_type.value,
            artifact_hash=artifact.content_hash,
            artifact_binding_hash=artifact.binding_hash,
        )

    def _terminal_artifact(self, request: ProjectWorkerRequest):
        artifact_type = (
            ProjectArtifactType.VERIFIER_RESULT
            if request.attempt_type == ExecutionAttemptType.VERIFICATION
            else ProjectArtifactType.EXECUTION_RESULT
        )
        evidence_references: tuple[dict[str, Any], ...] = ()
        if request.failure_classification == "process_exit_nonzero":
            repair = CanonicalRepairService(
                self.artifact_store.database_path,
                self.control,
                self.artifact_store,
            )
            failure = repair.capture_failure(
                project_run_id=request.project_run_id,
                execution_attempt_id=request.execution_attempt_id,
                failure_classification=request.failure_classification,
                result_reference=request.result_reference or {},
                authority=request.authority,
                plan_revision_id=request.plan_revision_id,
                scope_revision_id=request.scope_revision_id,
                manifest_hash=request.manifest_hash,
            )
            evidence_references = ({
                "artifact_id": failure.artifact_id,
                "artifact_type": failure.artifact_type.value,
                "content_hash": failure.content_hash,
            },)
        payload = {
            "reconciliation_key": f"worker-result:{request.worker_request_id}",
            "worker_request_id": request.worker_request_id,
            "execution_attempt_id": request.execution_attempt_id,
            "attempt_type": request.attempt_type.value,
            "outcome": request.status.value,
            "failure_classification": request.failure_classification,
            "resulting_manifest_hash": request.resulting_manifest_hash,
            "result_reference": request.result_reference or {},
            "request_hash": request.request_hash,
            "authority": request.authority,
        }
        return self.artifact_store.put(build_project_artifact(
            artifact_type=artifact_type,
            binding=ProjectArtifactBinding(
                project_run_id=request.project_run_id,
                plan_revision_id=request.plan_revision_id,
                scope_revision_id=request.scope_revision_id,
                manifest_hash=request.manifest_hash,
                execution_attempt_id=request.execution_attempt_id,
                authority_hash=content_hash(request.authority),
            ),
            payload=payload,
            evidence_references=evidence_references,
        ))

    def _attempt(self, request: ProjectWorkerRequest):
        return next((
            item
            for item in self.control.list_attempts(request.project_run_id)
            if item.execution_attempt_id == request.execution_attempt_id
        ), None)

    def _project_and_coordinate(self, project_run_id: str) -> None:
        if self.projector is not None:
            try:
                self.projector.rebuild_project(project_run_id)
            except Exception:
                pass
        if self.coordinator is not None:
            try:
                self.coordinator.reconcile(project_run_id)
            except Exception:
                pass


__all__ = ["TerminalResultReconciler"]
