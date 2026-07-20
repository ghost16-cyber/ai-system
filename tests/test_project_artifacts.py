from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.project_artifacts import (
    MAX_ARTIFACT_PAYLOAD_BYTES,
    ProjectArtifact,
    ProjectArtifactBinding,
    ProjectArtifactStore,
    ProjectArtifactStoreError,
    ProjectArtifactType,
    build_project_artifact,
)
from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlPlane,
)
from backend.app.project_control.contracts import content_hash


def _initialize_project(database: Path) -> None:
    control = ProjectControlPlane(database)
    control.initialize()
    control.execute(
        ProjectCommand(
            command_type=ProjectCommandType.INITIALIZE_PROJECT,
            project_run_id="project-1",
            conversation_id="conversation-1",
            workspace_id="workspace-1",
            repository_root="canonical-root",
            repository_root_fingerprint="root-fingerprint",
            actor_id="local-user",
            expected_state_version=0,
            idempotency_key="initialize",
        )
    )


@pytest.fixture
def store(tmp_path: Path) -> ProjectArtifactStore:
    database = tmp_path / "artifacts.db"
    _initialize_project(database)
    value = ProjectArtifactStore(database)
    value.initialize()
    return value


def _artifact(payload: dict | None = None):
    return build_project_artifact(
        artifact_type=ProjectArtifactType.PATCH_PREVIEW,
        binding=ProjectArtifactBinding(
            project_run_id="project-1",
            plan_revision_id="plan-1",
            scope_revision_id="scope-1",
            manifest_hash="a" * 64,
        ),
        payload=payload
        or {"operations": [{"path": "backend/app.py", "kind": "replace"}]},
        evidence_references=({"artifact_id": "manifest-1", "content_hash": "b" * 64},),
    )


def test_immutable_artifact_put_get_list_verify_and_exact_replay(
    store: ProjectArtifactStore,
) -> None:
    artifact = _artifact()
    assert store.put(artifact) == artifact
    assert store.put(artifact) == artifact
    assert store.get(artifact.artifact_id) == artifact
    assert store.list_for_project("project-1", artifact_type="patch_preview") == [
        artifact
    ]
    assert (
        store.verify(
            artifact.artifact_id,
            expected_binding_hash=artifact.binding_hash,
            expected_content_hash=artifact.content_hash,
        )
        == artifact
    )


def test_rebuilt_identical_artifact_returns_backend_record(
    store: ProjectArtifactStore,
) -> None:
    first = store.put(_artifact())
    rebuilt = first.model_copy(
        update={"created_at": first.created_at + timedelta(seconds=5)}
    )
    assert store.put(rebuilt) == first


def test_artifact_binding_and_content_tampering_fail_validation(
    store: ProjectArtifactStore,
) -> None:
    artifact = _artifact()
    store.put(artifact)
    with sqlite3.connect(store.database_path) as connection:
        raw = json.loads(artifact.model_dump_json())
        raw["payload"] = {"operations": []}
        connection.execute(
            "UPDATE project_artifacts SET artifact_json = ? WHERE artifact_id = ?",
            (json.dumps(raw), artifact.artifact_id),
        )
    with pytest.raises(ProjectArtifactStoreError, match="integrity"):
        store.get(artifact.artifact_id)

    values = artifact.model_dump()
    values["binding_hash"] = "0" * 64
    with pytest.raises(ValidationError, match="binding hash"):
        type(artifact).model_validate(values)


def test_normalized_artifact_hash_tampering_fails_closed(
    store: ProjectArtifactStore,
) -> None:
    artifact = store.put(_artifact())
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE project_artifacts SET content_hash = ? WHERE artifact_id = ?",
            ("0" * 64, artifact.artifact_id),
        )
    with pytest.raises(ProjectArtifactStoreError, match="normalized fields"):
        store.get(artifact.artifact_id)


def test_artifact_verification_rejects_stale_expected_hash(
    store: ProjectArtifactStore,
) -> None:
    artifact = store.put(_artifact())
    with pytest.raises(ProjectArtifactStoreError, match="stale"):
        store.verify(artifact.artifact_id, expected_binding_hash="0" * 64)


def test_artifact_payload_is_bounded_before_persistence() -> None:
    with pytest.raises(ValidationError, match="byte limit"):
        _artifact({"value": "x" * (MAX_ARTIFACT_PAYLOAD_BYTES + 1)})


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"content_hash": "0" * 64},
        {"payload": {"value": "x" * (MAX_ARTIFACT_PAYLOAD_BYTES + 1)}},
        {"binding": ProjectArtifactBinding.model_construct(project_run_id="")},
        {"schema_version": "astra.project-artifacts.artifact.v999"},
    ],
    ids=("content-hash", "payload-limit", "binding", "schema"),
)
def test_store_revalidates_constructed_artifact_before_any_insert(
    store: ProjectArtifactStore, invalid_update: dict
) -> None:
    values = dict(_artifact().__dict__)
    values.update(invalid_update)
    invalid = ProjectArtifact.model_construct(**values)

    with pytest.raises((ValidationError, TypeError, ValueError)):
        store.put(invalid)

    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_artifacts").fetchone()[0] == 0


def test_unknown_artifact_schema_is_rejected() -> None:
    values = _artifact().model_dump()
    values["schema_version"] = "astra.project-artifacts.artifact.v999"
    with pytest.raises(ValidationError, match="schema_version"):
        type(_artifact()).model_validate(values)


def test_artifact_identity_cannot_be_rebound(store: ProjectArtifactStore) -> None:
    artifact = store.put(_artifact())
    changed = _artifact(
        {"operations": [{"path": "backend/other.py", "kind": "replace"}]}
    )
    rebound = changed.model_copy(update={"artifact_id": artifact.artifact_id})
    with pytest.raises(ProjectArtifactStoreError, match="different content"):
        store.put(rebound)


def test_artifact_hash_is_deterministic() -> None:
    first = _artifact()
    second = _artifact()
    assert first.artifact_id == second.artifact_id
    assert first.content_hash == content_hash(
        {
            "artifact_type": first.artifact_type.value,
            "binding": first.binding.model_dump(mode="json"),
            "payload": first.payload,
            "evidence_references": first.evidence_references,
        }
    )
