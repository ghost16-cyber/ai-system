from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from backend.app.project_control.contracts import ExecutionAttemptType, content_hash
from backend.app.project_workers.contracts import (
    ProjectWorkerRequest,
    WorkerCompletion,
    WorkerCompletionOutcome,
    WorkerRequestStatus,
)
from backend.app.project_workers.isolation import (
    IsolationBackend,
    container_identity_for,
    create_workspace_snapshot,
)
from backend.app.project_workers.policy import (
    PreparedExecution,
    ProjectExecutionPolicyError,
    prepare_execution,
)
from backend.app.project_workers.runtime_contracts import IsolationExecutionResult
from backend.app.project_workers.service import ProjectWorkerService


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|access[_-]?key)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


class ProjectIsolatedExecutor:
    """Executes exact approved commands in a disposable isolation snapshot."""

    def __init__(
        self,
        service: ProjectWorkerService,
        backend: IsolationBackend,
        evidence_root: str | Path,
    ) -> None:
        self.service = service
        self.backend = backend
        self.evidence_root = Path(evidence_root).expanduser().resolve()

    def run_once(self, worker_id: str) -> bool:
        lease = self.service.claim_next(
            worker_id,
            [ExecutionAttemptType.COMMAND, ExecutionAttemptType.VERIFICATION],
        )
        if lease is None:
            return False

        request = lease.request
        lease_token = lease.lease_token
        temporary_root: Path | None = None
        result: IsolationExecutionResult | None = None
        failure_classification: str | None = None
        failure_message: str | None = None
        try:
            prepared = prepare_execution(request)
            self.evidence_root.mkdir(parents=True, exist_ok=True)
            temporary_root = Path(tempfile.mkdtemp(
                prefix=f"snapshot-{request.worker_request_id[:32]}-",
                dir=self.evidence_root,
            ))
            snapshot = create_workspace_snapshot(
                prepared.repository_root,
                temporary_root / "workspace",
            )
            prepared = prepare_execution(request)
            _validate_snapshot_artifacts(prepared, snapshot)
            result = self.backend.execute(
                request,
                prepared,
                snapshot,
                cancel_requested=lambda: self._cancel_requested(request),
                heartbeat=lambda: self.service.heartbeat(
                    request.worker_request_id,
                    worker_id,
                    lease_token,
                ),
            )
        except ProjectExecutionPolicyError as error:
            failure_classification = "execution_policy_rejected"
            failure_message = _bounded_message(str(error))
            self.backend.cancel(container_identity_for(request.worker_request_id))
        except Exception as error:
            failure_classification = "container_failure"
            failure_message = _bounded_message(f"{type(error).__name__}: {error}")
            self.backend.cancel(container_identity_for(request.worker_request_id))

        cleanup_failure: str | None = None
        if temporary_root is not None:
            try:
                snapshot_path = temporary_root / "workspace"
                if snapshot_path.exists():
                    self.backend.prepare_snapshot_cleanup(snapshot_path)
                shutil.rmtree(temporary_root)
            except (OSError, ProjectExecutionPolicyError) as error:
                cleanup_failure = _bounded_message(str(error))

        if cleanup_failure is not None:
            result = None
            failure_classification = "snapshot_cleanup_failed"
            failure_message = cleanup_failure

        if result is None:
            return self._complete_infrastructure_failure(
                request,
                worker_id,
                lease_token,
                classification=failure_classification or "container_failure",
                message=failure_message or "The isolated execution did not produce a result.",
            )

        try:
            evidence = self._write_evidence(request, result)
        except OSError as error:
            return self._complete_infrastructure_failure(
                request,
                worker_id,
                lease_token,
                classification="isolation_evidence_persistence_failed",
                message=_bounded_message(str(error)),
            )

        outcome, classification = _completion_outcome(result)
        self.service.complete(WorkerCompletion(
            worker_request_id=request.worker_request_id,
            worker_id=worker_id,
            lease_token=lease_token,
            idempotency_key=(
                f"isolated-complete:{request.worker_request_id}:{request.delivery_count}"
            ),
            outcome=outcome,
            result_reference={
                "evidence_id": evidence["evidence_id"],
                "evidence_hash": evidence["evidence_hash"],
                "container_identity": result.container_identity,
                "image_digest": result.image_digest,
                "exit_code": result.exit_code,
                "stdout_excerpt": _redact(result.stdout),
                "stderr_excerpt": _redact(result.stderr),
                "output_truncated": result.output_truncated,
                "effective_policy": result.effective_policy,
                "result_hash": content_hash(result.model_dump(mode="json")),
            },
            output_summary={
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
                "cancelled": result.cancelled,
                "output_truncated": result.output_truncated,
                "isolation_backend": result.backend.value,
                "isolation_profile_id": result.profile_id,
            },
            resulting_manifest_hash=(
                request.manifest_hash
                if outcome == WorkerCompletionOutcome.SUCCEEDED
                else None
            ),
            failure_classification=classification,
        ))
        return True

    def _cancel_requested(self, request: ProjectWorkerRequest) -> bool:
        current = self.service.queue.get(request.worker_request_id)
        return current.status == WorkerRequestStatus.CANCEL_REQUESTED

    def _complete_infrastructure_failure(
        self,
        request: ProjectWorkerRequest,
        worker_id: str,
        lease_token: str,
        *,
        classification: str,
        message: str,
    ) -> bool:
        try:
            self.service.complete(WorkerCompletion(
                worker_request_id=request.worker_request_id,
                worker_id=worker_id,
                lease_token=lease_token,
                idempotency_key=(
                    f"isolated-failure:{request.worker_request_id}:"
                    f"{request.delivery_count}:{classification}"
                ),
                outcome=WorkerCompletionOutcome.FAILED,
                failure_classification=classification,
                result_reference={"message": _bounded_message(message)},
            ))
        except Exception:
            # A lost lease is reconciled by the durable startup recovery path.
            return True
        return True

    def _write_evidence(
        self,
        request: ProjectWorkerRequest,
        result: IsolationExecutionResult,
    ) -> dict[str, str]:
        evidence_id = f"isolation-evidence-{request.worker_request_id}"
        body = {
            "schema_version": "astra.project-workers.isolation-evidence.v1",
            "evidence_id": evidence_id,
            "worker_request_id": request.worker_request_id,
            "project_run_id": request.project_run_id,
            "execution_attempt_id": request.execution_attempt_id,
            "plan_revision_id": request.plan_revision_id,
            "scope_revision_id": request.scope_revision_id,
            "manifest_hash": request.manifest_hash,
            "execution": {
                **result.model_dump(mode="json"),
                "stdout": _redact(result.stdout),
                "stderr": _redact(result.stderr),
            },
        }
        evidence_hash = content_hash(body)
        body["evidence_hash"] = evidence_hash
        target = self.evidence_root / f"{evidence_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(body, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(target)
        return {"evidence_id": evidence_id, "evidence_hash": evidence_hash}


def _completion_outcome(
    result: IsolationExecutionResult,
) -> tuple[WorkerCompletionOutcome, str | None]:
    if result.outcome == "succeeded":
        return WorkerCompletionOutcome.SUCCEEDED, None
    if result.outcome == "failed":
        return WorkerCompletionOutcome.FAILED, "process_exit_nonzero"
    if result.outcome == "timed_out":
        return WorkerCompletionOutcome.TIMED_OUT, "execution_timed_out"
    if result.outcome == "cancelled":
        return WorkerCompletionOutcome.CANCELLED, "execution_cancelled"
    return WorkerCompletionOutcome.FAILED, "container_failure"


def _validate_snapshot_artifacts(
    prepared: PreparedExecution,
    snapshot: Path,
) -> None:
    for artifact in prepared.spec.input_artifacts:
        try:
            target = (snapshot / artifact.relative_path).resolve(strict=True)
        except OSError as error:
            raise ProjectExecutionPolicyError(
                "An approved artifact is missing from the workspace snapshot."
            ) from error
        try:
            target.relative_to(snapshot)
        except ValueError as error:
            raise ProjectExecutionPolicyError(
                "An approved artifact escaped the workspace snapshot."
            ) from error
        if not target.is_file():
            raise ProjectExecutionPolicyError(
                "An approved artifact is missing from the workspace snapshot."
            )
        if hashlib.sha256(target.read_bytes()).hexdigest() != artifact.sha256:
            raise ProjectExecutionPolicyError(
                "An approved artifact changed while the snapshot was created."
            )


def _redact(value: str) -> str:
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1\2<redacted>", value)
    return _OPENAI_KEY_RE.sub("<redacted-api-key>", redacted)


def _bounded_message(value: str) -> str:
    return " ".join(str(value).split())[:600]


__all__ = ["ProjectIsolatedExecutor"]
