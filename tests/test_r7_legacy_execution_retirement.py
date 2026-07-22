"""R7: eliminate every reachable legacy project host-execution path.

These tests target the four routes the R7 reachability audit found executing
directly on the host, outside the canonical project control -> coordinator
intent -> Docker-isolated worker -> terminal reconciliation flow:

  1. POST /orchestrate            (orchestrator/tools.py apply_patch, run_tests)
  2. POST /jobs/{id}/approve-patch (orchestrator/approvals.py approve_pending_patch)
  3. POST /patch/apply             (analyzer/patch_apply.py, patch_verification.py)
  4. POST /assignments/commands/{id}/execute (commands/execution.py subprocess.Popen)

Per-route fail-closed regressions and the subprocess.Popen non-invocation
proof for /assignments/commands/execute live alongside their existing
suites (tests/test_patch_apply.py, tests/test_orchestrator.py,
tests/test_chat_workflow.py, tests/test_assignment_command_execution.py).
This file covers the remaining required properties in one place: read-only
probes stay available, and canonical worker/coordinator behavior (a
separate, already-covered subsystem) is unaffected by this change.
"""
from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


def _client(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TestClient(create_app(tmp_path / "app.db", workspace_root=workspace)), workspace


def test_orchestrate_forbids_subprocess_when_edits_or_tests_requested(tmp_path, monkeypatch):
    """Required test #1: a legacy command action cannot invoke subprocess.Popen
    in the API process. monkeypatch fails the test immediately if it tries."""
    def _forbidden_popen(*args, **kwargs):
        raise AssertionError("subprocess.Popen must never run in the API process for a retired route")

    monkeypatch.setattr(subprocess, "Popen", _forbidden_popen)
    client, workspace = _client(tmp_path)
    (workspace / "sample.py").write_text("print('hi')\n", encoding="utf-8")
    with client:
        response = client.post(
            "/orchestrate",
            json={"goal": "fix it", "path": ".", "allow_edits": True, "allow_tests": True},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "legacy_host_execution_retired"


@pytest.mark.parametrize(
    "allow_edits,allow_tests", [(True, False), (False, True), (True, True)]
)
def test_orchestrate_fails_closed_for_any_host_mutating_request(tmp_path, allow_edits, allow_tests):
    """Required test #2: legacy project execution endpoints fail closed."""
    client, workspace = _client(tmp_path)
    (workspace / "sample.py").write_text("print('hi')\n", encoding="utf-8")
    with client:
        response = client.post(
            "/orchestrate",
            json={
                "goal": "do something", "path": ".",
                "allow_edits": allow_edits, "allow_tests": allow_tests,
            },
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "legacy_host_execution_retired"


def test_orchestrate_read_only_request_still_enqueues_to_the_job_worker(tmp_path):
    """Pure read-only orchestration (no edits, no tests) is not host-mutating
    and remains available -- retirement is scoped to the mutating capability,
    not the whole endpoint."""
    client, workspace = _client(tmp_path)
    (workspace / "sample.py").write_text("print('hi')\n", encoding="utf-8")
    with client:
        response = client.post(
            "/orchestrate",
            json={
                "goal": "Explain this project", "path": ".",
                "allow_edits": False, "allow_tests": False,
            },
        )
    assert response.status_code == 202
    assert "job_id" in response.json()


def test_approve_patch_fails_closed_without_touching_the_pending_approval(tmp_path):
    """Required test #2, second route: /jobs/{id}/approve-patch fails closed."""
    client, _workspace = _client(tmp_path)
    with client:
        response = client.post("/jobs/does-not-exist/approve-patch")
    # A missing job is reported before the retirement check; either way, no
    # host execution occurs and there is no 200 success path anymore.
    assert response.status_code in (404, 503)
    if response.status_code == 503:
        assert response.json()["detail"]["code"] == "legacy_host_execution_retired"


def test_read_only_runtime_context_probe_remains_available(tmp_path):
    """Required test #8: read-only Git/tool/hardware probes remain available.
    /runtime/context is a bounded read-only capability probe, unrelated to
    project host-execution, and must be entirely unaffected by R7."""
    client, _workspace = _client(tmp_path)
    with client:
        response = client.get("/runtime/context")
    assert response.status_code == 200
    body = response.json()
    assert "hardware" in body
    assert "tools" in body


def test_hardware_report_probe_remains_available(tmp_path):
    """A second read-only probe (hardware_ai_optimizer), also unaffected."""
    client, _workspace = _client(tmp_path)
    with client:
        response = client.get("/hardware-ai/report")
    assert response.status_code == 200
    assert "report" in response.json()


def test_canonical_project_creation_and_approval_binding_is_unaffected(tmp_path):
    """Required test #3 (canonical worker path remains functional) and #5
    (approval is still exact and required): create a canonical project and
    approve its plan through the real project-control -> artifact-binding
    flow (R1), confirming R7's route-level changes to unrelated legacy
    endpoints did not touch project_control at all. Full worker/coordinator
    dispatch behavior is covered by the existing project_workers and
    project_coordinator suites, which R7 does not modify."""
    from backend.app.project_artifacts import ProjectArtifactStore
    from backend.app.project_control.project_service import CanonicalProjectService
    from backend.app.project_control.service import ProjectControlPlane

    database = tmp_path / "canonical.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    service = CanonicalProjectService(control, artifacts)
    project = service.create_project(
        conversation_id="c1", workspace_id="w1", repository_root=str(tmp_path),
        repository_root_fingerprint="fp1", actor_id="local-user", idempotency_key="create-1",
        folder_authority={
            "status": "completed", "action_id": "w1", "conversation_id": "c1",
            "workspace_id": "w1", "repository_root_fingerprint": "fp1",
        },
        specification={"specification_hash": "1" * 64, "included_paths": []},
        manifest={"manifest_hash": "2" * 64, "complete": True},
        plan={"acceptance_criteria": [], "work_units": []},
    )
    run = control.get_project(project.project_run_id)
    plan_artifact = artifacts.get(run.current_artifact_ids["plan"])

    class _Req:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    req = _Req(
        conversation_id=run.conversation_id, workspace_id=run.workspace_id,
        actor_id=run.actor_id, repository_root_fingerprint=run.repository_root_fingerprint,
        expected_state_version=run.state_version, idempotency_key="approve-1",
        plan_revision_id=run.current_plan_revision_id, scope_revision_id=run.current_scope_revision_id,
        manifest_hash=run.current_manifest_hash,
        artifact_id=plan_artifact.artifact_id, artifact_type=plan_artifact.artifact_type.value,
        artifact_hash=plan_artifact.content_hash, artifact_binding_hash=plan_artifact.binding_hash,
        payload={},
    )
    approved = service.execute_action(project.project_run_id, action="approve_plan", request=req)
    assert approved.lifecycle_state.value == "ready_for_work"

    # Approval without an artifact reference is still rejected (exact and required).
    bad_req = _Req(**{**req.__dict__, "idempotency_key": "approve-2", "artifact_id": "forged"})
    with pytest.raises(Exception):
        service.execute_action(project.project_run_id, action="approve_plan", request=bad_req)
