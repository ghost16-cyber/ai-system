from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app.database.migrations import assert_schema_compatible
from backend.app.project_artifacts import (
    ProjectArtifactStore,
    ProjectArtifactStoreError,
    ProjectArtifactType,
)
from backend.app.project_artifacts.contracts import artifact_content_hash
from backend.app.project_control import ProjectControlError, ProjectControlPlane
from backend.app.project_control.contracts import content_hash
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_scaffolding.service import (
    ProjectScaffoldingService,
    ScaffoldPersistenceService,
)
from backend.app.project_scaffolding.validators import RenderIntegrityError


def _project(control, artifacts, tmp_path: Path, *, suffix: str) -> str:
    root = tmp_path / f"workspace-{suffix}"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    service = CanonicalProjectService(control, artifacts)
    project = service.create_project(
        conversation_id=f"conversation-{suffix}",
        workspace_id=f"workspace-{suffix}",
        repository_root=str(root),
        repository_root_fingerprint=f"root-fingerprint-{suffix}",
        actor_id="local-user",
        idempotency_key=f"create-project-{suffix}",
        folder_authority={
            "status": "completed",
            "action_id": f"workspace-{suffix}",
            "conversation_id": f"conversation-{suffix}",
            "workspace_id": f"workspace-{suffix}",
            "repository_root_fingerprint": f"root-fingerprint-{suffix}",
        },
        specification={
            "specification_id": f"spec-{suffix}",
            "specification_hash": "1" * 64,
            "revision": 1,
            "included_paths": ["src/app.py"],
            "allowed_operations": ["read", "approved_patch", "verification"],
        },
        manifest={
            "manifest_hash": "2" * 64,
            "complete": True,
            "revision": 1,
            "entries": [{"path": "src/app.py", "sha256": "3" * 64}],
        },
        plan={
            "revision": 1,
            "acceptance_criteria": [{
                "criterion_id": "criterion-1",
                "required": True,
                "verification_mode": "structural_code_inspection",
            }],
            "work_units": [{
                "work_unit_id": "work-1",
                "expected_files": ["src/app.py"],
                "acceptance_criteria_ids": ["criterion-1"],
            }],
            "configured_limits": {"max_work_units": 2, "max_verifications": 2},
        },
    )
    return project.project_run_id


def _runtime(tmp_path: Path, *, suffix: str = "a"):
    database = tmp_path / "astra.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    project_run_id = _project(control, artifacts, tmp_path, suffix=suffix)
    persistence = ScaffoldPersistenceService(database, control, artifacts)
    persistence.initialize()
    return database, control, artifacts, persistence, project_run_id


def _counts(database: Path, project_run_id: str) -> dict[str, int]:
    with sqlite3.connect(database) as connection:
        return {
            "approval_grants": connection.execute(
                "SELECT COUNT(*) FROM project_approval_grants WHERE project_run_id = ?",
                (project_run_id,),
            ).fetchone()[0],
            "worker_requests": connection.execute(
                "SELECT COUNT(*) FROM project_worker_requests WHERE project_run_id = ?",
                (project_run_id,),
            ).fetchone()[0],
            "execution_dispatches": connection.execute(
                "SELECT COUNT(*) FROM project_execution_dispatches WHERE project_run_id = ?",
                (project_run_id,),
            ).fetchone()[0],
        }


def test_valid_render_persists_as_canonical_scaffold_artifact(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    scaffolding = ProjectScaffoldingService()
    inputs = {"package_name": "billing"}
    render_result = scaffolding.render(category="python_package", inputs=inputs)

    artifact = persistence.persist(
        render_result, inputs, project_run_id=project_run_id, category="python_package"
    )

    assert artifact.artifact_type == ProjectArtifactType.SCAFFOLD_MANIFEST
    assert artifact.binding.project_run_id == project_run_id
    stored = artifacts.get(artifact.artifact_id)
    assert stored == artifact


def test_persisted_content_hash_matches_the_deterministic_manifest(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    scaffolding = ProjectScaffoldingService()
    inputs = {"package_name": "billing"}
    render_result = scaffolding.render(category="python_package", inputs=inputs)

    artifact = persistence.persist(
        render_result, inputs, project_run_id=project_run_id, category="python_package"
    )

    assert artifact.payload["manifest"]["template_hash"] == render_result.manifest.template_hash
    assert artifact.payload["input_hash"] == content_hash(inputs)
    assert artifact.content_hash == artifact_content_hash(
        ProjectArtifactType.SCAFFOLD_MANIFEST,
        artifact.binding,
        artifact.payload,
        artifact.evidence_references,
        revision_number=1,
    )


def test_identical_persistence_request_is_idempotent(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    scaffolding = ProjectScaffoldingService()
    inputs = {"package_name": "billing"}
    render_result = scaffolding.render(category="python_package", inputs=inputs)

    first = persistence.persist(
        render_result, inputs, project_run_id=project_run_id, category="python_package"
    )
    second = persistence.persist(
        render_result, inputs, project_run_id=project_run_id, category="python_package"
    )

    assert first == second
    rows = artifacts.list_for_project(
        project_run_id, artifact_type=ProjectArtifactType.SCAFFOLD_MANIFEST
    )
    assert len(rows) == 1


def test_same_binding_with_different_content_fails_closed(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    scaffolding = ProjectScaffoldingService()
    declared_inputs = {"package_name": "billing"}
    genuine_render = scaffolding.render(category="python_package", inputs=declared_inputs)
    different_render = scaffolding.render(
        category="python_package", inputs={"package_name": "invoicing"}
    )

    persistence.persist(
        genuine_render, declared_inputs, project_run_id=project_run_id, category="python_package"
    )
    # Same declared inputs (same authority_hash/binding) but genuinely different
    # render content -- this must fail closed, not silently overwrite or
    # duplicate.
    with pytest.raises(ProjectArtifactStoreError, match="already bound to different content"):
        persistence.persist(
            different_render, declared_inputs,
            project_run_id=project_run_id, category="python_package",
        )

    rows = artifacts.list_for_project(
        project_run_id, artifact_type=ProjectArtifactType.SCAFFOLD_MANIFEST
    )
    assert len(rows) == 1


def test_changed_inputs_produce_a_distinct_artifact_and_hash(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    scaffolding = ProjectScaffoldingService()

    billing_inputs = {"package_name": "billing"}
    billing_render = scaffolding.render(category="python_package", inputs=billing_inputs)
    billing_artifact = persistence.persist(
        billing_render, billing_inputs, project_run_id=project_run_id, category="python_package"
    )

    invoicing_inputs = {"package_name": "invoicing"}
    invoicing_render = scaffolding.render(category="python_package", inputs=invoicing_inputs)
    invoicing_artifact = persistence.persist(
        invoicing_render, invoicing_inputs,
        project_run_id=project_run_id, category="python_package",
    )

    assert billing_artifact.artifact_id != invoicing_artifact.artifact_id
    assert billing_artifact.content_hash != invoicing_artifact.content_hash
    rows = artifacts.list_for_project(
        project_run_id, artifact_type=ProjectArtifactType.SCAFFOLD_MANIFEST
    )
    assert len(rows) == 2


def test_artifact_is_bound_to_the_correct_project_and_revision_identities(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    run = control.get_project(project_run_id)
    scaffolding = ProjectScaffoldingService()
    inputs = {"package_name": "billing"}
    render_result = scaffolding.render(category="python_package", inputs=inputs)

    artifact = persistence.persist(
        render_result, inputs, project_run_id=project_run_id,
        category="python_package", work_unit_id="work-1",
    )

    assert artifact.binding.project_run_id == project_run_id
    assert artifact.binding.plan_revision_id == run.current_plan_revision_id
    assert artifact.binding.scope_revision_id == run.current_scope_revision_id
    assert artifact.binding.manifest_hash == run.current_manifest_hash
    assert artifact.binding.work_unit_id == "work-1"


def test_cross_project_artifact_reuse_is_rejected(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_a = _runtime(tmp_path, suffix="a")
    project_b = _project(control, artifacts, tmp_path, suffix="b")
    scaffolding = ProjectScaffoldingService()
    inputs = {"package_name": "billing"}
    render_result = scaffolding.render(category="python_package", inputs=inputs)

    artifact_a = persistence.persist(
        render_result, inputs, project_run_id=project_a, category="python_package"
    )
    artifact_b = persistence.persist(
        render_result, inputs, project_run_id=project_b, category="python_package"
    )

    assert artifact_a.artifact_id != artifact_b.artifact_id
    a_rows = artifacts.list_for_project(project_a, artifact_type=ProjectArtifactType.SCAFFOLD_MANIFEST)
    b_rows = artifacts.list_for_project(project_b, artifact_type=ProjectArtifactType.SCAFFOLD_MANIFEST)
    assert [item.artifact_id for item in a_rows] == [artifact_a.artifact_id]
    assert [item.artifact_id for item in b_rows] == [artifact_b.artifact_id]

    with pytest.raises(ProjectControlError):
        persistence.persist(
            render_result, inputs, project_run_id="project-does-not-exist",
            category="python_package",
        )


def test_tampered_manifest_hash_is_rejected_before_persistence(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    scaffolding = ProjectScaffoldingService()
    inputs = {"package_name": "billing"}
    render_result = scaffolding.render(category="python_package", inputs=inputs)

    tampered_manifest = render_result.manifest.model_copy(
        update={
            "files": (
                render_result.manifest.files[0].model_copy(update={"content_hash": "f" * 64}),
            )
            + render_result.manifest.files[1:]
        }
    )
    tampered = render_result.model_copy(update={"manifest": tampered_manifest})

    with pytest.raises(RenderIntegrityError):
        persistence.persist(
            tampered, inputs, project_run_id=project_run_id, category="python_package"
        )

    rows = artifacts.list_for_project(
        project_run_id, artifact_type=ProjectArtifactType.SCAFFOLD_MANIFEST
    )
    assert len(rows) == 0


def test_persistence_causes_no_project_run_lifecycle_state_change(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    before = control.get_project(project_run_id)
    scaffolding = ProjectScaffoldingService()
    inputs = {"package_name": "billing"}
    render_result = scaffolding.render(category="python_package", inputs=inputs)

    persistence.persist(
        render_result, inputs, project_run_id=project_run_id, category="python_package"
    )

    after = control.get_project(project_run_id)
    assert after.state_version == before.state_version
    assert after.lifecycle_status == before.lifecycle_status
    assert after.pending_user_action == before.pending_user_action


def test_persistence_creates_no_approval_dispatch_or_filesystem_write(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    run = control.get_project(project_run_id)
    root = Path(run.repository_root)
    before_files = sorted(str(path) for path in root.rglob("*") if path.is_file())
    before_counts = _counts(database, project_run_id)

    scaffolding = ProjectScaffoldingService()
    inputs = {"package_name": "billing"}
    render_result = scaffolding.render(category="python_package", inputs=inputs)
    persistence.persist(
        render_result, inputs, project_run_id=project_run_id, category="python_package"
    )

    after_files = sorted(str(path) for path in root.rglob("*") if path.is_file())
    after_counts = _counts(database, project_run_id)
    assert after_files == before_files
    assert after_counts == before_counts
    previews = artifacts.list_for_project(
        project_run_id, artifact_type=ProjectArtifactType.PATCH_PREVIEW
    )
    assert previews == []


def test_audit_record_is_produced_exactly_once(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    scaffolding = ProjectScaffoldingService()
    inputs = {"package_name": "billing"}
    render_result = scaffolding.render(category="python_package", inputs=inputs)

    for _ in range(3):
        persistence.persist(
            render_result, inputs, project_run_id=project_run_id, category="python_package"
        )

    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM project_artifacts WHERE project_run_id = ? AND artifact_type = ?",
            (project_run_id, ProjectArtifactType.SCAFFOLD_MANIFEST.value),
        ).fetchone()[0]
    assert count == 1


def test_migration_is_idempotent_and_schema_compatibility_remains_green(tmp_path: Path) -> None:
    database, control, artifacts, persistence, project_run_id = _runtime(tmp_path)
    persistence.initialize()
    persistence.initialize()
    assert_schema_compatible(database)
