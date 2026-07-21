from __future__ import annotations

import sqlite3

from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control.adapters import ProjectDeliveryControlAdapter
from backend.app.project_control.compatibility import CompatibilityClassificationService
from backend.app.project_control.errors import ProjectControlError, ProjectControlErrorCode
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_control.service import ProjectControlPlane


def _setup(tmp_path):
    database = tmp_path / "compat.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    return database, control, artifacts


def _tag_historical(database, delivery_job_id):
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO project_compatibility_records (
                   record_id, project_run_id, source_table, source_id,
                   generation, read_only, tagged_at
               ) VALUES (?, ?, 'project_delivery_jobs', ?, 'legacy', 1, ?)""",
            (f"compat-{delivery_job_id}", delivery_job_id, delivery_job_id, "2026-01-01T00:00:00+00:00"),
        )
        connection.commit()


def _legacy_job(delivery_job_id, root):
    return {
        "delivery_job_id": delivery_job_id,
        "conversation_id": "conversation-h",
        "folder_access_id": "workspace-h",
        "root_fingerprint": "fingerprint-h",
        "canonical_repository_root": str(root),
        "specification": {"specification_id": "spec-h", "specification_hash": "1" * 64},
        "project_state_manifest": {"manifest_hash": "2" * 64, "complete": True},
        "plan": {
            "revision": 1,
            "acceptance_criteria": [],
            "work_units": [{"work_unit_id": "w1", "expected_files": ["app.py"]}],
            "plan_hash": "3" * 64,
        },
    }


def _folder_authority():
    return {
        "status": "completed",
        "action_id": "workspace-h",
        "conversation_id": "conversation-h",
        "workspace_id": "workspace-h",
        "repository_root_fingerprint": "fingerprint-h",
    }


def _raised(callable_):
    try:
        callable_()
    except ProjectControlError as exc:
        return exc
    return None


def test_classification_service_reads_the_ledger(tmp_path):
    database, _control, _artifacts = _setup(tmp_path)
    service = CompatibilityClassificationService(database)
    assert service.classification("unknown-id") == "unclassified"
    assert service.is_historical_read_only("unknown-id") is False
    _tag_historical(database, "delivery-1")
    assert service.classification("delivery-1") == "historical_read_only"
    assert service.is_historical_read_only("delivery-1") is True


def test_tagged_historical_record_cannot_be_mutated_by_any_entrypoint(tmp_path):
    database, control, artifacts = _setup(tmp_path)
    _tag_historical(database, "delivery-1")
    adapter = ProjectDeliveryControlAdapter(control, artifacts)
    job = _legacy_job("delivery-1", tmp_path)
    entrypoints = (
        lambda: adapter.ensure(job, tmp_path, migrated=True),
        # Even when the inferred display flag claims the record is mutable, the
        # ledger classification is authoritative and blocks the transition.
        lambda: adapter.apply_transition(
            {**job, "historical_read_only": False}, job, tmp_path, "plan_approval_granted", {}
        ),
        lambda: adapter.approve_plan_bound(
            job, tmp_path, plan_hash="3" * 64, idempotency_key="k",
            expected_state_version=1, plan_revision_id="p", scope_revision_id="s",
        ),
        lambda: adapter.approve_patch(job, tmp_path, "patch-1"),
        lambda: adapter.approve_command(job, tmp_path, "command-1"),
        lambda: adapter.approve_rollback(job, tmp_path, "rollback-1", mutation_spec_hash="4" * 64),
    )
    for entrypoint in entrypoints:
        error = _raised(entrypoint)
        assert error is not None
        assert error.code == ProjectControlErrorCode.HISTORICAL_RECORD_READ_ONLY


def test_historical_mutation_attempt_creates_no_canonical_run(tmp_path):
    database, control, artifacts = _setup(tmp_path)
    _tag_historical(database, "delivery-1")
    adapter = ProjectDeliveryControlAdapter(control, artifacts)
    job = _legacy_job("delivery-1", tmp_path)
    _raised(lambda: adapter.approve_plan_bound(
        job, tmp_path, plan_hash="3" * 64, idempotency_key="k",
        expected_state_version=1, plan_revision_id="p", scope_revision_id="s",
    ))
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM project_runs WHERE project_run_id = ?", ("delivery-1",)
        ).fetchone()[0]
    assert count == 0


def test_decorate_marks_historical_read_only_and_stays_read_only(tmp_path):
    database, control, artifacts = _setup(tmp_path)
    _tag_historical(database, "delivery-1")
    adapter = ProjectDeliveryControlAdapter(control, artifacts)
    job = _legacy_job("delivery-1", tmp_path)
    decorated = adapter.decorate(job, tmp_path)
    assert decorated["historical_read_only"] is True
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM project_runs WHERE project_run_id = ?", ("delivery-1",)
        ).fetchone()[0]
    assert count == 0


def test_bound_plan_approval_rejects_omitted_exact_bindings(tmp_path):
    _database, control, artifacts = _setup(tmp_path)
    # A non-historical legacy delivery reconciled into a canonical run.
    adapter = ProjectDeliveryControlAdapter(control, artifacts)
    job = _legacy_job("delivery-live", tmp_path)
    adapter.ensure(job, tmp_path, migrated=True)
    error = _raised(lambda: adapter.approve_plan_bound(
        job, tmp_path, plan_hash="3" * 64, idempotency_key="approve-live",
        expected_state_version=None, plan_revision_id=None, scope_revision_id=None,
    ))
    assert error is not None
    assert error.code == ProjectControlErrorCode.INVALID_COMMAND


def test_import_historical_creates_fresh_canonical_run_awaiting_approval(tmp_path):
    database, control, artifacts = _setup(tmp_path)
    _tag_historical(database, "delivery-1")
    service = CanonicalProjectService(control, artifacts)
    job = _legacy_job("delivery-1", tmp_path)

    imported = service.import_historical_record(
        historical_job=job, conversation_id="conversation-h", workspace_id="workspace-h",
        repository_root=str(tmp_path), repository_root_fingerprint="fingerprint-h",
        folder_authority=_folder_authority(),
        idempotency_key="import-historical:delivery-1",
    )
    assert imported.project_run_id != "delivery-1"
    assert imported.lifecycle_state.value == "awaiting_plan_approval"
    assert imported.pending_user_action == "approve_plan"

    # The historical record itself is untouched and remains read-only.
    assert CompatibilityClassificationService(database).is_historical_read_only("delivery-1") is True

    # Re-import is idempotent: the same fresh run, no duplicate.
    again = service.import_historical_record(
        historical_job=job, conversation_id="conversation-h", workspace_id="workspace-h",
        repository_root=str(tmp_path), repository_root_fingerprint="fingerprint-h",
        folder_authority=_folder_authority(),
        idempotency_key="import-historical:delivery-1",
    )
    assert again.project_run_id == imported.project_run_id
