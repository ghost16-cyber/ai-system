from __future__ import annotations

from pathlib import Path

from backend.app.project_control import ProjectLifecycle
from backend.app.project_workers import (
    IsolationBackendKind,
    IsolationExecutionResult,
    ProjectIsolatedExecutor,
    WorkerRequestStatus,
)
from tests.test_project_worker_execution import _runtime


class FakeIsolationBackend:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.snapshot: Path | None = None
        self.cancelled_identities: list[str] = []

    def execute(
        self,
        request,
        prepared,
        workspace_snapshot,
        *,
        cancel_requested,
        heartbeat,
    ) -> IsolationExecutionResult:
        self.snapshot = workspace_snapshot
        if self.fail:
            raise OSError("simulated container failure")
        (workspace_snapshot / "check.py").write_text(
            "changed only in snapshot\n",
            encoding="utf-8",
        )
        return IsolationExecutionResult(
            backend=IsolationBackendKind.DOCKER,
            profile_id=prepared.spec.isolation_profile_id,
            container_identity=f"container-{request.worker_request_id}",
            image_reference="astra-project-runtime:test",
            image_digest=prepared.spec.image_digest,
            argv_display=("python", "check.py"),
            working_directory=".",
            outcome="succeeded",
            exit_code=0,
            stdout="isolated-ok",
            stderr="",
            output_truncated=False,
            timed_out=False,
            cancelled=False,
            infrastructure_failure=False,
            duration_ms=1,
            effective_policy={"network": "none", "filesystem": "snapshot"},
        )

    def cancel(self, container_identity: str) -> bool:
        self.cancelled_identities.append(container_identity)
        return True

    def prepare_snapshot_cleanup(self, workspace_snapshot: Path) -> None:
        return None

    def cleanup_orphans(self, active_container_identities=()):
        return ()


def test_isolated_executor_mutates_only_snapshot_and_persists_evidence(tmp_path: Path) -> None:
    _root, script, control, queue, service, request, _legacy = _runtime(
        tmp_path,
        "print('original')\n",
    )
    backend = FakeIsolationBackend()
    executor = ProjectIsolatedExecutor(service, backend, tmp_path / "isolated-evidence")

    assert executor.run_once("isolated-worker") is True

    assert script.read_text(encoding="utf-8") == "print('original')\n"
    assert backend.snapshot is not None
    assert not backend.snapshot.exists()
    finished = queue.get(request.worker_request_id)
    assert finished.status == WorkerRequestStatus.SUCCEEDED
    assert finished.canonical_reconciled_at is not None
    assert finished.result_reference["stdout_excerpt"] == "isolated-ok"
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.VERIFICATION_PENDING
    evidence = list((tmp_path / "isolated-evidence").glob("isolation-evidence-*.json"))
    assert len(evidence) == 1


def test_container_failure_never_falls_back_and_blocks_canonical_attempt(tmp_path: Path) -> None:
    _root, script, control, queue, service, request, _legacy = _runtime(
        tmp_path,
        "print('must-not-run-on-host')\n",
    )
    backend = FakeIsolationBackend(fail=True)
    executor = ProjectIsolatedExecutor(service, backend, tmp_path / "isolated-evidence")

    assert executor.run_once("isolated-worker") is True

    assert script.read_text(encoding="utf-8") == "print('must-not-run-on-host')\n"
    finished = queue.get(request.worker_request_id)
    assert finished.status == WorkerRequestStatus.FAILED
    assert finished.failure_classification == "container_failure"
    assert finished.canonical_reconciled_at is not None
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.BLOCKED
    assert len(backend.cancelled_identities) == 1
