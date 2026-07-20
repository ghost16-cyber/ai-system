from __future__ import annotations

import sqlite3

import pytest

from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control import ProjectControlError, ProjectLifecycle
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_control.service import ProjectControlPlane


SPEC_HASH = "1" * 64
MANIFEST_HASH = "2" * 64


def _service(tmp_path):
    database = tmp_path / "astra.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    return database, control, artifacts, CanonicalProjectService(control, artifacts)


def _create(service: CanonicalProjectService, tmp_path, **overrides):
    values = {
        "conversation_id": "conversation-1",
        "workspace_id": "folder-action-1",
        "repository_root": str(tmp_path),
        "repository_root_fingerprint": "root-fingerprint-1",
        "actor_id": "local-user",
        "idempotency_key": "request-1",
        "folder_authority": {
            "status": "completed",
            "action_id": "folder-action-1",
            "conversation_id": "conversation-1",
            "workspace_id": "folder-action-1",
            "repository_root_fingerprint": "root-fingerprint-1",
        },
        "specification": {
            "specification_id": "spec-1",
            "specification_hash": SPEC_HASH,
            "revision": 1,
            "included_paths": ["src/app.py"],
            "allowed_operations": ["read", "approved_patch", "verification"],
        },
        "manifest": {
            "manifest_hash": MANIFEST_HASH,
            "complete": True,
            "revision": 1,
            "entries": [{"path": "src/app.py", "sha256": "3" * 64}],
        },
        "plan": {
            "revision": 1,
            "acceptance_criteria": [
                {
                    "criterion_id": "criterion-1",
                    "requirement": "The bounded change is verified.",
                    "required": True,
                    "verification_mode": "deterministic",
                }
            ],
            "work_units": [
                {"work_unit_id": "work-1", "expected_files": ["src/app.py"]}
            ],
            "configured_limits": {"max_work_units": 2},
        },
    }
    values.update(overrides)
    return service.create_project(**values)


def _counts(database):
    with sqlite3.connect(database) as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "project_runs",
                "project_events",
                "project_artifacts",
                "project_execution_attempts",
                "project_model_invocations",
            )
        }


def test_create_project_records_run_and_bound_immutable_artifacts(tmp_path):
    database, control, artifacts, service = _service(tmp_path)

    result = _create(service, tmp_path)

    assert result.lifecycle_state == ProjectLifecycle.AWAITING_PLAN_APPROVAL
    assert result.project_run_id.startswith("project-")
    assert set(result.artifact_references) == {"specification", "manifest", "plan"}
    assert set(result.artifact_hashes) == {"specification", "manifest", "plan"}
    assert len(artifacts.list_for_project(result.project_run_id)) == 3
    assert control.get_project(result.project_run_id).canonical_generation == "canonical"
    assert _counts(database) == {
        "project_runs": 1,
        "project_events": 4,
        "project_artifacts": 3,
        "project_execution_attempts": 0,
        "project_model_invocations": 0,
    }


def test_duplicate_create_is_an_exact_idempotent_replay(tmp_path):
    database, _control, _artifacts, service = _service(tmp_path)
    first = _create(service, tmp_path)
    before = _counts(database)

    second = _create(service, tmp_path)

    assert second.project_run_id == first.project_run_id
    assert second.state_version == first.state_version
    assert _counts(database) == before


def test_refresh_during_creation_resumes_same_project_without_duplicate_work(tmp_path):
    database, control, artifacts, service = _service(tmp_path)
    run_id = "project-refresh"
    base = {
        "conversation_id": "conversation-1",
        "workspace_id": "folder-action-1",
        "repository_root": str(tmp_path),
        "repository_root_fingerprint": "root-fingerprint-1",
        "actor_id": "local-user",
    }
    from backend.app.project_control.contracts import ProjectCommand, ProjectCommandType

    control.execute(ProjectCommand(
        command_type=ProjectCommandType.INITIALIZE_PROJECT,
        project_run_id=run_id,
        expected_state_version=0,
        idempotency_key="request-refresh:initialize",
        payload={"canonical_generation": "canonical"},
        **base,
    ))
    service.record_specification(
        project_run_id=run_id,
        specification={
            "specification_id": "spec-1",
            "specification_hash": SPEC_HASH,
            "revision": 1,
            "included_paths": ["src/app.py"],
        },
        idempotency_key="request-refresh:specification",
        expected_state_version=1,
    )
    refreshed = CanonicalProjectService(control, artifacts)
    refreshed.record_manifest(
        project_run_id=run_id,
        manifest={"manifest_hash": MANIFEST_HASH, "complete": True, "revision": 1},
        idempotency_key="request-refresh:manifest",
        expected_state_version=2,
    )
    refreshed.record_plan(
        project_run_id=run_id,
        plan={"revision": 1, "acceptance_criteria": [], "work_units": []},
        idempotency_key="request-refresh:plan",
        expected_state_version=3,
    )

    assert refreshed.get_project(run_id).project_run_id == run_id
    assert _counts(database)["project_runs"] == 1
    assert _counts(database)["project_artifacts"] == 3


def test_folder_authority_mismatch_fails_before_project_creation(tmp_path):
    database, _control, _artifacts, service = _service(tmp_path)
    authority = {
        "status": "completed",
        "action_id": "folder-action-1",
        "conversation_id": "another-conversation",
        "workspace_id": "folder-action-1",
        "repository_root_fingerprint": "root-fingerprint-1",
    }

    with pytest.raises(ProjectControlError):
        _create(service, tmp_path, folder_authority=authority)

    assert _counts(database)["project_runs"] == 0


def test_canonical_transition_requires_exact_artifact_but_never_uses_its_payload_as_authority(tmp_path):
    _database, control, artifacts, _service_instance = _service(tmp_path)
    from backend.app.project_artifacts import (
        ProjectArtifactBinding,
        ProjectArtifactType,
        build_project_artifact,
    )
    from backend.app.project_control.contracts import ProjectCommand, ProjectCommandType

    base = {
        "project_run_id": "project-artifact-authority",
        "conversation_id": "conversation-1",
        "workspace_id": "folder-action-1",
        "repository_root": str(tmp_path),
        "repository_root_fingerprint": "root-fingerprint-1",
        "actor_id": "local-user",
    }
    control.execute(ProjectCommand(
        command_type=ProjectCommandType.INITIALIZE_PROJECT,
        expected_state_version=0,
        idempotency_key="artifact-authority:init",
        payload={"canonical_generation": "canonical"},
        **base,
    ))
    transition_payload = {
        "task_specification_id": "spec-authority",
        "specification_hash": SPEC_HASH,
        "included_paths": ["src/app.py"],
        "reason": "Exact command payload owns the transition.",
    }
    with pytest.raises(ProjectControlError, match="immutable artifact"):
        control.execute(ProjectCommand(
            command_type=ProjectCommandType.ATTACH_SPECIFICATION,
            expected_state_version=1,
            idempotency_key="artifact-authority:missing",
            payload=transition_payload,
            **base,
        ))

    artifact = artifacts.put(build_project_artifact(
        artifact_type=ProjectArtifactType.SPECIFICATION,
        binding=ProjectArtifactBinding(project_run_id=base["project_run_id"]),
        payload={"requested_lifecycle": "completed"},
        revision_number=1,
    ))
    control.execute(ProjectCommand(
        command_type=ProjectCommandType.ATTACH_SPECIFICATION,
        expected_state_version=1,
        idempotency_key="artifact-authority:bound",
        payload=transition_payload,
        artifact_id=artifact.artifact_id,
        artifact_type=artifact.artifact_type.value,
        artifact_hash=artifact.content_hash,
        artifact_binding_hash=artifact.binding_hash,
        **base,
    ))

    assert control.get_project(base["project_run_id"]).lifecycle_status == ProjectLifecycle.MANIFEST_REQUIRED
