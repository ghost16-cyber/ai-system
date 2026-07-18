from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from backend.app.project_control.contracts import ExecutionAttemptType, canonical_json, content_hash
from backend.app.project_workers.contracts import (
    ProjectWorkerRequest,
    WorkerCompletion,
    WorkerCompletionOutcome,
    WorkerRequestStatus,
)
from backend.app.project_workers.policy import (
    PreparedExecution,
    ProjectExecutionPolicyError,
    minimal_environment,
    prepare_execution,
)
from backend.app.project_workers.runtime_contracts import WorkerProcessResult
from backend.app.project_workers.service import ProjectWorkerService


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|access[_-]?key)\b(\s*[:=]\s*)([^\s,;]+)"
)
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


class ProjectSubprocessExecutor:
    """Executes exact approved commands without becoming a lifecycle authority."""

    def __init__(
        self,
        service: ProjectWorkerService,
        evidence_root: str | Path,
        *,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self.service = service
        self.evidence_root = Path(evidence_root).expanduser().resolve()
        self.poll_interval_seconds = max(0.01, min(float(poll_interval_seconds), 1.0))

    def run_once(self, worker_id: str) -> bool:
        lease = self.service.claim_next(
            worker_id,
            [ExecutionAttemptType.COMMAND, ExecutionAttemptType.VERIFICATION],
        )
        if lease is None:
            return False

        try:
            prepared = prepare_execution(lease.request)
        except ProjectExecutionPolicyError as error:
            self.service.complete(WorkerCompletion(
                worker_request_id=lease.request.worker_request_id,
                worker_id=worker_id,
                lease_token=lease.lease_token,
                idempotency_key=f"policy-rejected:{lease.request.worker_request_id}:{lease.request.delivery_count}",
                outcome=WorkerCompletionOutcome.FAILED,
                failure_classification="execution_policy_rejected",
                result_reference={"message": _bounded_message(str(error))},
            ))
            return True

        try:
            result = self._execute(lease.request, prepared, worker_id, lease.lease_token)
        except ProjectExecutionPolicyError as error:
            self.service.complete(WorkerCompletion(
                worker_request_id=lease.request.worker_request_id,
                worker_id=worker_id,
                lease_token=lease.lease_token,
                idempotency_key=f"runtime-policy-rejected:{lease.request.worker_request_id}:{lease.request.delivery_count}",
                outcome=WorkerCompletionOutcome.FAILED,
                failure_classification="execution_policy_rejected",
                result_reference={"message": _bounded_message(str(error))},
            ))
            return True
        except Exception as error:
            self.service.complete(WorkerCompletion(
                worker_request_id=lease.request.worker_request_id,
                worker_id=worker_id,
                lease_token=lease.lease_token,
                idempotency_key=f"executor-failed:{lease.request.worker_request_id}:{lease.request.delivery_count}",
                outcome=WorkerCompletionOutcome.FAILED,
                failure_classification="executor_internal_failure",
                result_reference={"message": _bounded_message(f"{type(error).__name__}: {error}")},
            ))
            return True

        outcome = WorkerCompletionOutcome(result.outcome)
        self.service.complete(WorkerCompletion(
            worker_request_id=lease.request.worker_request_id,
            worker_id=worker_id,
            lease_token=lease.lease_token,
            idempotency_key=f"execution-complete:{lease.request.worker_request_id}:{lease.request.delivery_count}",
            outcome=outcome,
            result_reference={
                "evidence_id": result.evidence_id,
                "evidence_hash": result.evidence_hash,
                "result_hash": result.result_hash,
                "action": result.action.value,
                "exit_code": result.exit_code,
                "stdout_excerpt": result.stdout,
                "stderr_excerpt": result.stderr,
                "output_truncated": result.output_truncated,
            },
            output_summary={
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
                "cancelled": result.cancelled,
                "output_truncated": result.output_truncated,
            },
            resulting_manifest_hash=(
                lease.request.manifest_hash
                if outcome == WorkerCompletionOutcome.SUCCEEDED
                else None
            ),
            failure_classification=(
                None
                if outcome == WorkerCompletionOutcome.SUCCEEDED
                else "execution_timed_out"
                if outcome == WorkerCompletionOutcome.TIMED_OUT
                else "execution_cancelled"
                if outcome == WorkerCompletionOutcome.CANCELLED
                else "process_exit_nonzero"
            ),
        ))
        return True

    def _execute(
        self,
        request: ProjectWorkerRequest,
        prepared: PreparedExecution,
        worker_id: str,
        lease_token: str,
    ) -> WorkerProcessResult:
        started_monotonic = time.monotonic()
        started_at = datetime.now(timezone.utc)
        timed_out = False
        cancelled = False
        output_limit = request.limits.max_output_bytes
        stream_limit = max(128, min(65_536, (output_limit - 1024) // 2))
        stdout_buffer = _BoundedBuffer(stream_limit)
        stderr_buffer = _BoundedBuffer(stream_limit)

        with tempfile.TemporaryDirectory(prefix="astra-worker-runtime-") as temporary:
            runtime_root = Path(temporary)
            environment = minimal_environment(runtime_root)
            preexec = _resource_limiter(request)
            process = subprocess.Popen(
                list(prepared.argv),
                cwd=prepared.working_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=os.name != "nt",
                preexec_fn=preexec,
            )
            stdout_thread = threading.Thread(
                target=_drain,
                args=(process.stdout, stdout_buffer),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=_drain,
                args=(process.stderr, stderr_buffer),
                daemon=True,
            )
            stdout_thread.start()
            stderr_thread.start()

            heartbeat_interval = max(1.0, min(5.0, request.limits.lease_seconds / 3))
            next_heartbeat = time.monotonic() + heartbeat_interval
            deadline = started_monotonic + request.limits.timeout_seconds

            while process.poll() is None:
                now_monotonic = time.monotonic()
                current = self.service.queue.get(request.worker_request_id)
                if current.status == WorkerRequestStatus.CANCEL_REQUESTED:
                    cancelled = True
                    _terminate_process(process)
                    break
                if now_monotonic >= deadline:
                    timed_out = True
                    _terminate_process(process)
                    break
                if now_monotonic >= next_heartbeat:
                    self.service.heartbeat(
                        request.worker_request_id,
                        worker_id,
                        lease_token,
                    )
                    next_heartbeat = now_monotonic + heartbeat_interval
                time.sleep(self.poll_interval_seconds)

            if process.poll() is None:
                _terminate_process(process)
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

        current_request = self.service.queue.get(request.worker_request_id)
        if current_request.status == WorkerRequestStatus.CANCEL_REQUESTED:
            cancelled = True
        duration_ms = max(0, int((time.monotonic() - started_monotonic) * 1000))
        stdout = _redact(stdout_buffer.text())
        stderr = _redact(stderr_buffer.text())
        exit_code = process.returncode
        if cancelled:
            outcome = WorkerCompletionOutcome.CANCELLED
        elif timed_out:
            outcome = WorkerCompletionOutcome.TIMED_OUT
        elif exit_code in prepared.spec.expected_exit_codes:
            outcome = WorkerCompletionOutcome.SUCCEEDED
        else:
            outcome = WorkerCompletionOutcome.FAILED

        evidence_payload = {
            "schema_version": "astra.project-workers.execution-evidence.v1",
            "worker_request_id": request.worker_request_id,
            "project_run_id": request.project_run_id,
            "execution_attempt_id": request.execution_attempt_id,
            "execution_hash": prepared.spec.execution_hash,
            "action": prepared.spec.action.value,
            "argv_display": _display_argv(prepared.argv, prepared.repository_root),
            "working_directory": _relative_display(prepared.working_directory, prepared.repository_root),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "exit_code": exit_code,
            "outcome": outcome.value,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": stdout_buffer.truncated or stderr_buffer.truncated,
            "limits": request.limits.model_dump(mode="json"),
            "network_isolation": "not_enforced",
        }
        evidence_hash = content_hash(evidence_payload)
        evidence_id = f"worker-evidence-{evidence_hash[:24]}"
        self._write_evidence(evidence_id, evidence_payload, evidence_hash)

        result_base = {
            "worker_request_id": request.worker_request_id,
            "action": prepared.spec.action,
            "outcome": outcome.value,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": stdout_buffer.truncated or stderr_buffer.truncated,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "duration_ms": duration_ms,
            "evidence_id": evidence_id,
            "evidence_hash": evidence_hash,
        }
        result_hash = content_hash({**result_base, "result_hash": None})
        return WorkerProcessResult(result_hash=result_hash, **result_base)

    def _write_evidence(
        self,
        evidence_id: str,
        payload: dict,
        evidence_hash: str,
    ) -> None:
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        destination = self.evidence_root / f"{evidence_id}.json"
        record = {**payload, "evidence_hash": evidence_hash}
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)


class _BoundedBuffer:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.value = bytearray()
        self.truncated = False
        self.lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self.lock:
            remaining = self.limit - len(self.value)
            if remaining > 0:
                self.value.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.truncated = True

    def text(self) -> str:
        with self.lock:
            return bytes(self.value).decode("utf-8", errors="replace")


def _drain(stream: BinaryIO | None, buffer: _BoundedBuffer) -> None:
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            buffer.append(chunk)
    finally:
        stream.close()


def _resource_limiter(request: ProjectWorkerRequest):
    memory_limit = request.limits.memory_limit_mb
    cpu_limit = request.limits.cpu_time_seconds
    if os.name != "posix":
        if memory_limit is not None or cpu_limit is not None:
            raise ProjectExecutionPolicyError(
                "CPU and memory process limits require a POSIX worker runtime."
            )
        return None

    def apply_limits() -> None:
        import resource

        nofile_soft, nofile_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        nofile_limit = min(value for value in (nofile_soft, nofile_hard, 256) if value >= 0)
        resource.setrlimit(resource.RLIMIT_NOFILE, (nofile_limit, nofile_limit))
        if hasattr(resource, "RLIMIT_NPROC"):
            nproc_soft, nproc_hard = resource.getrlimit(resource.RLIMIT_NPROC)
            nproc_limit = min(value for value in (nproc_soft, nproc_hard, 64) if value >= 0)
            resource.setrlimit(resource.RLIMIT_NPROC, (nproc_limit, nproc_limit))
        if memory_limit is not None:
            bytes_limit = memory_limit * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
        if cpu_limit is not None:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))

    return apply_limits


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


def _display_argv(argv: tuple[str, ...], root: Path) -> list[str]:
    return [_relative_display(Path(item), root) if _looks_absolute(item) else item[:500] for item in argv]


def _relative_display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix() or "."
    except (OSError, ValueError):
        return path.name or "."


def _looks_absolute(value: str) -> bool:
    try:
        return Path(value).is_absolute()
    except (OSError, ValueError):
        return False


def _redact(value: str) -> str:
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1\2<redacted>", value)
    return _OPENAI_KEY_RE.sub("<redacted-api-key>", redacted)


def _bounded_message(value: str) -> str:
    return " ".join(value.split())[:600]


__all__ = ["ProjectSubprocessExecutor"]
