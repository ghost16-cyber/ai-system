from __future__ import annotations

from backend.app.project_control.contracts import ExecutionAttemptType, content_hash
from backend.app.project_workers.contracts import (
    ProjectWorkerRequest,
    WorkerCompletion,
    WorkerCompletionOutcome,
    WorkerRequestStatus,
)
from backend.app.project_workers.mutation_contracts import FileMutationSpec
from backend.app.project_workers.mutations import (
    FileMutationEngine,
    FileMutationError,
    FileMutationErrorCode,
)
from backend.app.project_workers.service import ProjectWorkerService


class ProjectMutationExecutor:
    """Executes trusted host-side file mutations without invoking a shell."""

    def __init__(self, service: ProjectWorkerService, engine: FileMutationEngine) -> None:
        self.service = service
        self.engine = engine

    def run_once(self, worker_id: str) -> bool:
        lease = self.service.claim_next(
            worker_id,
            [ExecutionAttemptType.PATCH, ExecutionAttemptType.ROLLBACK],
        )
        if lease is None:
            return False
        request = lease.request
        try:
            spec = FileMutationSpec.model_validate(request.payload.get("file_mutation"))
            self._validate_bindings(request, spec)
            result = self.engine.apply(
                spec,
                cancel_requested=lambda: self._cancel_requested(request),
            )
        except FileMutationError as error:
            outcome = (
                WorkerCompletionOutcome.CANCELLED
                if error.code == FileMutationErrorCode.CANCELLED
                else WorkerCompletionOutcome.FAILED
            )
            self.service.complete(WorkerCompletion(
                worker_request_id=request.worker_request_id,
                worker_id=worker_id,
                lease_token=lease.lease_token,
                idempotency_key=(
                    f"mutation-failure:{request.worker_request_id}:"
                    f"{request.delivery_count}:{error.code.value}"
                ),
                outcome=outcome,
                failure_classification=f"file_mutation_{error.code.value}",
                result_reference={"message": error.message},
            ))
            return True
        except Exception as error:
            self.service.complete(WorkerCompletion(
                worker_request_id=request.worker_request_id,
                worker_id=worker_id,
                lease_token=lease.lease_token,
                idempotency_key=(
                    f"mutation-invalid:{request.worker_request_id}:"
                    f"{request.delivery_count}"
                ),
                outcome=WorkerCompletionOutcome.FAILED,
                failure_classification="file_mutation_invalid_spec",
                result_reference={
                    "message": " ".join(f"{type(error).__name__}: {error}".split())[:600]
                },
            ))
            return True

        self.service.complete(WorkerCompletion(
            worker_request_id=request.worker_request_id,
            worker_id=worker_id,
            lease_token=lease.lease_token,
            idempotency_key=(
                f"mutation-complete:{request.worker_request_id}:{request.delivery_count}"
            ),
            outcome=WorkerCompletionOutcome.SUCCEEDED,
            result_reference={
                "file_mutation_id": result.file_mutation_id,
                "evidence_hash": result.evidence_hash,
                "resulting_file_hashes": result.resulting_file_hashes,
                "exact_replay": result.exact_replay,
                "recovered": result.recovered,
                "result_hash": content_hash(result.model_dump(mode="json")),
            },
            output_summary={
                "changed_files": len(result.resulting_file_hashes),
                "mutation_kind": spec.mutation_kind.value,
            },
            resulting_manifest_hash=result.resulting_manifest_hash,
        ))
        return True

    def _cancel_requested(self, request: ProjectWorkerRequest) -> bool:
        current = self.service.queue.get(request.worker_request_id)
        return current.status == WorkerRequestStatus.CANCEL_REQUESTED

    @staticmethod
    def _validate_bindings(request: ProjectWorkerRequest, spec: FileMutationSpec) -> None:
        expected_authority_key = (
            "patch_id" if request.attempt_type == ExecutionAttemptType.PATCH else "rollback_id"
        )
        bindings = {
            "project_run_id": (spec.project_run_id, request.project_run_id),
            "execution_attempt_id": (spec.execution_attempt_id, request.execution_attempt_id),
            "repository_root": (spec.repository_root, request.repository_root),
            "repository_root_fingerprint": (
                spec.repository_root_fingerprint,
                request.repository_root_fingerprint,
            ),
            "workspace_id": (spec.workspace_id, request.workspace_id),
            "plan_revision_id": (spec.plan_revision_id, request.plan_revision_id),
            "scope_revision_id": (spec.scope_revision_id, request.scope_revision_id),
            "manifest_hash": (spec.manifest_hash, request.manifest_hash),
            expected_authority_key: (
                spec.authority_id,
                str(request.authority.get(expected_authority_key) or ""),
            ),
        }
        mismatched = [name for name, values in bindings.items() if values[0] != values[1]]
        if mismatched:
            raise FileMutationError(
                FileMutationErrorCode.INVALID_SPEC,
                "The file mutation no longer matches its canonical authority: "
                + ", ".join(mismatched[:4]),
            )


class CompositeProjectExecutor:
    def __init__(self, *executors) -> None:
        self.executors = tuple(executors)

    def run_once(self, worker_id: str) -> bool:
        for executor in self.executors:
            if executor.run_once(worker_id):
                return True
        return False


__all__ = ["CompositeProjectExecutor", "ProjectMutationExecutor"]
