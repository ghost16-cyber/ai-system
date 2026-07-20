from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Protocol

from backend.app.project_control import ProjectControlPlane
from backend.app.project_workers.execution import ProjectSubprocessExecutor
from backend.app.project_workers.isolated_execution import ProjectIsolatedExecutor
from backend.app.project_workers.isolation import (
    DockerIsolationBackend,
    default_isolation_profile,
)
from backend.app.project_workers.mutation_execution import (
    CompositeProjectExecutor,
    ProjectMutationExecutor,
)
from backend.app.project_workers.mutations import FileMutationEngine
from backend.app.project_workers.queue import ProjectWorkerQueue
from backend.app.project_workers.service import ProjectWorkerService


class WorkerExecutor(Protocol):
    def run_once(self, worker_id: str) -> bool: ...


def worker_cycle(
    service: ProjectWorkerService,
    *,
    worker_id: str,
    executor: WorkerExecutor | None,
    execution_backend: str | None = None,
) -> dict[str, object]:
    if execution_backend is not None:
        service.record_runtime_heartbeat(
            worker_id,
            execution_backend=execution_backend,
            supported_attempt_types=("patch_application", "rollback", "command_execution", "verification"),
            supported_toolchains=("python", "node"),
        )
    recovery = service.recover_expired_leases()
    dispatch = service.dispatch_pending()
    executed = executor.run_once(worker_id) if executor is not None else False
    return {
        "worker_id": worker_id,
        "dispatched_request_ids": list(dispatch.dispatched_request_ids),
        "recovered_dispatch_ids": list(dispatch.recovered_dispatch_ids),
        "deferred_dispatch_ids": list(dispatch.deferred_dispatch_ids),
        "recovered_request_ids": list(recovery.recovered_request_ids),
        "canonical_recovery_ids": list(recovery.canonical_recovery_ids),
        "executed": executed,
    }


def build_runtime(
    *,
    database_path: Path,
    workspace_root: Path,
    execution_backend: str,
) -> tuple[ProjectWorkerService, WorkerExecutor | None]:
    control = ProjectControlPlane(database_path)
    control.initialize()
    queue = ProjectWorkerQueue(database_path)
    queue.initialize()
    service = ProjectWorkerService(control, queue)

    backend = execution_backend.strip().lower()
    if backend == "disabled":
        return service, None
    if backend == "legacy":
        if os.getenv("ASTRA_ALLOW_LEGACY_PROJECT_EXECUTION", "").strip() != "1":
            raise RuntimeError(
                "Legacy host execution requires ASTRA_ALLOW_LEGACY_PROJECT_EXECUTION=1."
            )
        return service, ProjectSubprocessExecutor(
            service,
            workspace_root / "data" / "project_worker_results",
        )
    if backend == "docker":
        isolation = DockerIsolationBackend(default_isolation_profile())
        capability = isolation.probe()
        if not capability.available:
            raise RuntimeError(
                f"Docker isolation is unavailable ({capability.failure_code}): "
                f"{capability.detail or 'capability probe failed'}. "
                "Astra will not fall back to host execution."
            )
        isolation.cleanup_orphans()
        mutation_engine = FileMutationEngine(
            database_path,
            workspace_root / "data" / "project_mutation_journals",
        )
        mutation_engine.initialize()
        mutation_engine.recover_incomplete()
        isolated_executor = ProjectIsolatedExecutor(
            service,
            isolation,
            workspace_root / "data" / "project_worker_results",
        )
        mutation_executor = ProjectMutationExecutor(service, mutation_engine)
        return service, CompositeProjectExecutor(
            mutation_executor,
            isolated_executor,
        )
    raise RuntimeError(f"Unsupported project execution backend: {execution_backend!r}.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Astra's durable project worker.")
    parser.add_argument("--once", action="store_true", help="Run one recovery/dispatch/execution cycle.")
    parser.add_argument(
        "--dispatch-only",
        action="store_true",
        help="Recover and dispatch durable work without executing project code.",
    )
    parser.add_argument("--worker-id", default="", help="Stable local worker identity.")
    parser.add_argument("--idle-seconds", type=float, default=1.0)
    parser.add_argument("--database-path", default="")
    parser.add_argument("--workspace-root", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    database_path = Path(
        args.database_path
        or os.getenv("AI_SYSTEM_DB_PATH", "data/app/ai_system.db")
    ).expanduser().resolve()
    workspace_root = Path(
        args.workspace_root
        or os.getenv("AI_SYSTEM_WORKSPACE_ROOT", str(Path.cwd()))
    ).expanduser().resolve()
    execution_backend = (
        "disabled"
        if args.dispatch_only
        else os.getenv("ASTRA_PROJECT_EXECUTION_BACKEND", "docker")
    )
    service, executor = build_runtime(
        database_path=database_path,
        workspace_root=workspace_root,
        execution_backend=execution_backend,
    )
    worker_id = (
        args.worker_id.strip()
        or os.getenv("ASTRA_PROJECT_WORKER_ID", "").strip()
        or f"local-{socket.gethostname()}-{os.getpid()}"
    )[:160]
    idle_seconds = clamp_idle_seconds(args.idle_seconds)
    stopping = threading.Event()
    last_runtime_heartbeat: float | None = None
    printed_initial_report = False

    def stop(_signum=None, _frame=None) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        while not stopping.is_set():
            now = time.monotonic()
            heartbeat_due = (
                last_runtime_heartbeat is None
                or now - last_runtime_heartbeat >= 5.0
            )
            report = worker_cycle(
                service,
                worker_id=worker_id,
                executor=executor,
                execution_backend=(
                    execution_backend.strip().lower() if heartbeat_due else None
                ),
            )
            if heartbeat_due:
                last_runtime_heartbeat = time.monotonic()
            if not printed_initial_report or report_has_activity(report):
                print(json.dumps(report, sort_keys=True), flush=True)
                printed_initial_report = True
            if args.once:
                break
            if not report["executed"] and not report["dispatched_request_ids"]:
                stopping.wait(idle_seconds)
    finally:
        service.mark_runtime_stopped(worker_id)
    return 0


def clamp_idle_seconds(value: float) -> float:
    return max(1.0, min(float(value), 30.0))


def report_has_activity(report: dict[str, object]) -> bool:
    return bool(
        report.get("executed")
        or report.get("dispatched_request_ids")
        or report.get("recovered_dispatch_ids")
        or report.get("deferred_dispatch_ids")
        or report.get("recovered_request_ids")
        or report.get("canonical_recovery_ids")
    )


if __name__ == "__main__":
    raise SystemExit(main())
