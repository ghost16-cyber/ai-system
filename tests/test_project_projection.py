from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from backend.app.database.repository import AnalysisRepository
from backend.app.project_control import ProjectCommandType, ProjectLifecycle
from backend.app.project_projection import ProjectProjectionService
from tests.test_project_worker_execution import _project_command, _runtime


def _compatibility_job(request) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "delivery_job_id": request.project_run_id,
        "action_run_id": "run-projection",
        "conversation_id": request.conversation_id,
        "folder_access_id": request.workspace_id,
        "root_fingerprint": request.repository_root_fingerprint,
        "canonical_generation": "canonical",
        "state_version": 1,
        "status": "verification_running",
        "original_user_request": "Project a canonical command.",
        "specification": {
            "normalized_objective": "Project a canonical command.",
            "acceptance_criteria": [],
        },
        "plan": {"work_units": []},
        "patch_references": [],
        "created_at": now,
        "updated_at": now,
    }


def test_projection_rebuilds_ordered_events_and_idle_rebuild_does_not_write(tmp_path) -> None:
    _root, _script, control, _queue, _service, request, _executor = _runtime(
        tmp_path, "print('projection')\n"
    )
    repository = AnalysisRepository(control.database_path)
    repository.initialize()
    repository.store_project_delivery_job(_compatibility_job(request))
    projector = ProjectProjectionService(control.database_path, control)

    projected = projector.rebuild_project(request.project_run_id)
    assert len(projected) == len(control.list_events(request.project_run_id))
    job = repository.get_project_delivery_job(request.project_run_id)
    assert job["project_control"]["project_run_id"] == request.project_run_id
    with sqlite3.connect(control.database_path) as connection:
        before = connection.execute(
            "SELECT last_event_sequence, last_event_id, status, failure_message, updated_at "
            "FROM project_projection_checkpoints WHERE project_run_id = ?",
            (request.project_run_id,),
        ).fetchone()
        before_job = connection.execute(
            "SELECT job_json, updated_at FROM project_delivery_jobs WHERE delivery_job_id = ?",
            (request.project_run_id,),
        ).fetchone()

    assert projector.rebuild_project(request.project_run_id) == ()
    with sqlite3.connect(control.database_path) as connection:
        after = connection.execute(
            "SELECT last_event_sequence, last_event_id, status, failure_message, updated_at "
            "FROM project_projection_checkpoints WHERE project_run_id = ?",
            (request.project_run_id,),
        ).fetchone()
        after_job = connection.execute(
            "SELECT job_json, updated_at FROM project_delivery_jobs WHERE delivery_job_id = ?",
            (request.project_run_id,),
        ).fetchone()
    assert after == before
    assert after_job == before_job


def test_projection_failure_pauses_without_changing_canonical_state_and_recovers(
    tmp_path, monkeypatch
) -> None:
    _root, _script, control, _queue, _service, request, _executor = _runtime(
        tmp_path, "print('projection-recovery')\n"
    )
    repository = AnalysisRepository(control.database_path)
    repository.initialize()
    repository.store_project_delivery_job(_compatibility_job(request))
    projector = ProjectProjectionService(control.database_path, control)
    projector.rebuild_project(request.project_run_id)
    run = control.get_project(request.project_run_id)
    control.execute(_project_command(
        ProjectCommandType.MARK_BLOCKED,
        run,
        "block-for-projection",
        payload={"reason": "canonical reason"},
    ))
    canonical = control.get_project(request.project_run_id)
    original = projector._project_job
    monkeypatch.setattr(
        projector,
        "_project_job",
        lambda _job: (_ for _ in ()).throw(ValueError("projection unavailable")),
    )

    with pytest.raises(ValueError, match="projection unavailable"):
        projector.rebuild_project(request.project_run_id)
    assert control.get_project(request.project_run_id) == canonical
    assert canonical.lifecycle_status == ProjectLifecycle.BLOCKED
    read = control.get_read_model(request.project_run_id)
    assert read.projection_status == "paused"
    assert read.projection_lag == 1

    monkeypatch.setattr(projector, "_project_job", original)
    assert len(projector.rebuild_project(request.project_run_id)) == 1
    recovered = control.get_read_model(request.project_run_id)
    assert recovered.projection_status == "current"
    assert recovered.projection_lag == 0
