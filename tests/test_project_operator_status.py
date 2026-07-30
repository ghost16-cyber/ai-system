from __future__ import annotations

import hashlib
import sqlite3

from backend.app.operations import collect_project_runtime_diagnostics
from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control import ProjectControlPlane


def test_project_doctor_is_bounded_read_only_and_reports_schema(tmp_path) -> None:
    database = tmp_path / "astra.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    report = collect_project_runtime_diagnostics(database, check_docker=False)
    after = hashlib.sha256(database.read_bytes()).hexdigest()
    assert report["schema_version"] == "astra.project-doctor.report.v1"
    assert report["schema"]["pending"] == 0
    assert report["docker"]["checked"] is False
    assert report["safe_to_start"] is True
    assert before == after
    assert len(report["last_failures"]) <= 8


def test_project_doctor_missing_database_fails_closed(tmp_path) -> None:
    report = collect_project_runtime_diagnostics(tmp_path / "missing.db", check_docker=False)
    assert report["safe_to_start"] is False
    assert report["database"]["error"] == "database_not_found"


def test_project_doctor_tolerates_optional_columns_missing_from_historical_table_shape(
    tmp_path,
) -> None:
    database = tmp_path / "astra.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "ALTER TABLE project_coordinator_intents "
            "DROP COLUMN last_failure_classification"
        )
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    report = collect_project_runtime_diagnostics(database, check_docker=False)

    assert report["schema"]["pending"] == 0
    assert report["last_failures"] == []
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
