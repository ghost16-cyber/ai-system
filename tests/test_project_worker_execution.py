from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import pytest

from backend.app.folders import project_root_fingerprint
from backend.app.project_control import ProjectCommand, ProjectCommandType, ProjectControlPlane, ProjectLifecycle
from backend.app.project_control.contracts import content_hash
from backend.app.project_workers import (
    ExecutionInputArtifact,
    ProjectWorkerQueue,
    ProjectWorkerService,
    WorkerCommandAction,
    WorkerCompletion,
    WorkerCompletionOutcome,
    WorkerEnqueueCommand,
    WorkerLimits,
    WorkerRequestStatus,
    build_execution_spec,
)
from backend.app.project_workers.execution import ProjectSubprocessExecutor


def _project_command(kind: ProjectCommandType, run, key: str, *, payload=None, authority=None) -> ProjectCommand:
    return ProjectCommand(
        command_type=kind,
        project_run_id=run.project_run_id,
        conversation_id=run.conversation_id,
        workspace_id=run.workspace_id,
        repository_root=run.repository_root,
        repository_root_fingerprint=run.repository_root_fingerprint,
        actor_id=run.actor_id,
        expected_state_version=run.state_version,
        idempotency_key=key,
        plan_revision_id=run.current_plan_revision_id,
        scope_revision_id=run.current_scope_revision_id,
        manifest_hash=run.current_manifest_hash,
        payload=payload or {},
        authority_scope=authority or {},
    )


def _runtime(
    tmp_path: Path,
    script_text: str,
    *,
    target: str = "check.py",
    limits: WorkerLimits | None = None,
    image_digest: str | None = None,
    through_outbox: bool = False,
):
    root = tmp_path / "project"
    root.mkdir()
    script = root / "check.py"
    script.write_text(script_text, encoding="utf-8")
    fingerprint = project_root_fingerprint(root)
    database = tmp_path / "control.db"
    control = ProjectControlPlane(database)
    control.initialize()
    base = {
        "project_run_id": "project-1",
        "conversation_id": "conversation-1",
        "workspace_id": "workspace-1",
        "repository_root": str(root),
        "repository_root_fingerprint": fingerprint,
        "actor_id": "local-user",
    }
    control.execute(ProjectCommand(
        **base,
        command_type=ProjectCommandType.INITIALIZE_PROJECT,
        expected_state_version=0,
        idempotency_key="initialize",
    ))
    run = control.get_project("project-1")
    control.execute(_project_command(ProjectCommandType.ATTACH_SPECIFICATION, run, "specification", payload={
        "task_specification_id": "specification-1",
        "specification_hash": content_hash({"specification": "bounded execution"}),
        "included_paths": ["check.py"],
        "allowed_operations": ["read", "approved_command", "verification"],
    }))
    run = control.get_project("project-1")
    manifest_hash = content_hash({"files": {"check.py": hashlib.sha256(script.read_bytes()).hexdigest()}})
    control.execute(_project_command(ProjectCommandType.REGISTER_MANIFEST, run, "manifest", payload={
        "manifest_hash": manifest_hash,
        "complete": True,
    }))
    run = control.get_project("project-1")
    criterion = {
        "criterion_id": "criterion-1",
        "required": True,
        "verification_mode": "structural_code_inspection",
    }
    control.execute(_project_command(ProjectCommandType.PROPOSE_PLAN_REVISION, run, "plan", payload={
        "acceptance_criteria": [criterion],
        "work_units": [{"work_unit_id": "work-1", "objective": "Run bounded validation."}],
    }))
    run = control.get_project("project-1")
    control.execute(_project_command(ProjectCommandType.APPROVE_PLAN, run, "approve-plan", authority={
        "operation": "prepare_work_units",
        "work_unit_ids": ["work-1"],
    }))
    run = control.get_project("project-1")
    control.execute(_project_command(ProjectCommandType.BEGIN_WORK_UNIT, run, "begin-work", payload={
        "work_unit_id": "work-1",
    }, authority={"work_unit_id": "work-1"}))

    artifact = ExecutionInputArtifact(
        relative_path="check.py",
        sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
    )
    spec = build_execution_spec(
        action=WorkerCommandAction.PYTHON_SCRIPT,
        command_id="command-1",
        target=target,
        input_artifacts=[artifact],
        **({"image_digest": image_digest} if image_digest is not None else {}),
    )
    authority = {
        "command_id": "command-1",
        "operation": "execute_exact_command",
        "execution_hash": spec.execution_hash,
    }
    run = control.get_project("project-1")
    control.execute(_project_command(ProjectCommandType.RECORD_COMMAND_PREVIEW, run, "command-preview", payload={
        "command_id": "command-1",
    }))
    run = control.get_project("project-1")
    control.execute(_project_command(ProjectCommandType.APPROVE_COMMAND, run, "command-approval", payload={
        "command_id": "command-1",
    }, authority=authority))
    run = control.get_project("project-1")
    execution_limits = limits or WorkerLimits(
        lease_seconds=5,
        timeout_seconds=5,
        max_output_bytes=4096,
    )
    start_payload = {
        "command_id": "command-1",
        "execution_hash": spec.execution_hash,
    }
    if through_outbox:
        start_payload["worker_dispatch"] = {
            "payload": {"execution": spec.model_dump(mode="json")},
            "limits": execution_limits.model_dump(mode="json"),
            "priority": 0,
        }
    control.execute(_project_command(
        ProjectCommandType.BEGIN_COMMAND_EXECUTION,
        run,
        "command-start",
        payload=start_payload,
        authority=authority,
    ))
    run = control.get_project("project-1")
    attempt = control.list_attempts(run.project_run_id)[-1]

    queue = ProjectWorkerQueue(database)
    queue.initialize()
    service = ProjectWorkerService(control, queue)
    if through_outbox:
        dispatch = service.dispatch_pending()
        assert len(dispatch.dispatched_request_ids) == 1
        request = queue.get(dispatch.dispatched_request_ids[0])
    else:
        request = queue.enqueue(WorkerEnqueueCommand(
            project_run_id=run.project_run_id,
            execution_attempt_id=attempt.execution_attempt_id,
            attempt_type=attempt.attempt_type,
            conversation_id=run.conversation_id,
            workspace_id=run.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=run.repository_root_fingerprint,
            actor_id=run.actor_id,
            plan_revision_id=str(run.current_plan_revision_id),
            scope_revision_id=str(run.current_scope_revision_id),
            manifest_hash=str(run.current_manifest_hash),
            expected_project_state_version=run.state_version,
            authority=attempt.authority,
            payload={"execution": spec.model_dump(mode="json")},
            idempotency_key="enqueue-command",
            limits=execution_limits,
        ))
    executor = ProjectSubprocessExecutor(service, tmp_path / "evidence", poll_interval_seconds=0.02)
    return root, script, control, queue, service, request, executor


def test_exact_python_command_executes_and_reconciles_canonical_success(tmp_path: Path) -> None:
    _root, _script, control, queue, _service, request, executor = _runtime(
        tmp_path,
        "print('bounded-ok')\n",
    )
    assert executor.run_once("worker-a") is True
    finished = queue.get(request.worker_request_id)
    assert finished.status == WorkerRequestStatus.SUCCEEDED
    assert finished.canonical_reconciled_at is not None
    assert finished.result_reference["stdout_excerpt"].strip() == "bounded-ok"
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.VERIFICATION_PENDING
    assert control.list_attempts("project-1")[-1].status.value == "completed"
    evidence = list((tmp_path / "evidence").glob("worker-evidence-*.json"))
    assert len(evidence) == 1


def test_nonzero_exit_records_domain_failure_and_enters_repair(tmp_path: Path) -> None:
    _root, _script, control, queue, _service, request, executor = _runtime(
        tmp_path,
        "raise SystemExit(3)\n",
    )

    assert executor.run_once("worker-a") is True

    finished = queue.get(request.worker_request_id)
    assert finished.status == WorkerRequestStatus.FAILED
    assert finished.failure_classification == "process_exit_nonzero"
    assert finished.canonical_reconciled_at is not None
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.REPAIR_REQUIRED
    attempt = control.list_attempts("project-1")[-1]
    assert attempt.status.value == "failed"
    assert attempt.failure_classification == "process_exit_nonzero"


def test_changed_approved_artifact_fails_closed(tmp_path: Path) -> None:
    _root, script, control, queue, _service, request, executor = _runtime(
        tmp_path,
        "print('original')\n",
    )
    script.write_text("print('changed')\n", encoding="utf-8")
    assert executor.run_once("worker-a") is True
    finished = queue.get(request.worker_request_id)
    assert finished.status == WorkerRequestStatus.FAILED
    assert finished.failure_classification == "execution_policy_rejected"
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.BLOCKED


def test_target_path_escape_is_rejected_without_shell_execution(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    _root, _script, control, queue, _service, request, executor = _runtime(
        tmp_path,
        "print('inside')\n",
        target="../outside.py",
    )
    assert executor.run_once("worker-a") is True
    finished = queue.get(request.worker_request_id)
    assert finished.status == WorkerRequestStatus.FAILED
    assert finished.failure_classification == "execution_policy_rejected"
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.BLOCKED


def test_timeout_terminates_process_and_interrupts_canonical_attempt(tmp_path: Path) -> None:
    _root, _script, control, queue, _service, request, executor = _runtime(
        tmp_path,
        "import time\ntime.sleep(5)\n",
        limits=WorkerLimits(lease_seconds=5, timeout_seconds=1, max_output_bytes=4096),
    )
    assert executor.run_once("worker-a") is True
    finished = queue.get(request.worker_request_id)
    assert finished.status == WorkerRequestStatus.TIMED_OUT
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.BLOCKED
    assert control.list_attempts("project-1")[-1].status.value == "interrupted"


def test_running_process_observes_durable_cancellation(tmp_path: Path) -> None:
    _root, _script, control, queue, service, request, executor = _runtime(
        tmp_path,
        "import time\ntime.sleep(5)\n",
        limits=WorkerLimits(lease_seconds=5, timeout_seconds=10, max_output_bytes=4096),
    )
    thread = threading.Thread(target=executor.run_once, args=("worker-a",), daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while queue.get(request.worker_request_id).status == WorkerRequestStatus.QUEUED:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    service.request_cancel(request.worker_request_id)
    thread.join(timeout=4)
    assert not thread.is_alive()
    finished = queue.get(request.worker_request_id)
    assert finished.status == WorkerRequestStatus.CANCELLED
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.BLOCKED


def test_output_is_bounded_and_secrets_are_redacted(tmp_path: Path) -> None:
    code = "print('api_key=supersecret')\nprint('sk-abcdefghijklmnop')\nprint('x' * 10000)\n"
    _root, _script, _control, queue, _service, request, executor = _runtime(
        tmp_path,
        code,
        limits=WorkerLimits(lease_seconds=5, timeout_seconds=5, max_output_bytes=4096),
    )
    assert executor.run_once("worker-a") is True
    finished = queue.get(request.worker_request_id)
    output = finished.result_reference["stdout_excerpt"]
    assert "supersecret" not in output
    assert "sk-abcdefghijklmnop" not in output
    assert "<redacted>" in output
    assert finished.result_reference["output_truncated"] is True


def test_success_reconciliation_recovers_after_queue_only_completion(tmp_path: Path) -> None:
    _root, _script, control, queue, _service, request, _executor = _runtime(
        tmp_path,
        "print('not-run-in-this-test')\n",
    )
    lease = queue.claim_next("worker-a", [request.attempt_type])
    assert lease is not None
    result_reference = {
        "result_hash": content_hash({"passed": True}),
        "evidence_id": "evidence-1",
    }
    queue.complete(WorkerCompletion(
        worker_request_id=request.worker_request_id,
        worker_id="worker-a",
        lease_token=lease.lease_token,
        idempotency_key="queue-only-success",
        outcome=WorkerCompletionOutcome.SUCCEEDED,
        result_reference=result_reference,
        resulting_manifest_hash=request.manifest_hash,
    ))
    assert queue.get(request.worker_request_id).canonical_reconciled_at is None

    restarted = ProjectWorkerService(control, ProjectWorkerQueue(queue.database_path))
    restarted.queue.initialize()
    report = restarted.recover_expired_leases()
    assert report.canonical_recovery_ids == (request.worker_request_id,)
    assert restarted.queue.get(request.worker_request_id).canonical_reconciled_at is not None
    assert control.get_project("project-1").lifecycle_status == ProjectLifecycle.VERIFICATION_PENDING
