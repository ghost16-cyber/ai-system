from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from backend.app.database.migrations import (
    LATEST_SCHEMA_VERSION, MigrationError, current_schema_version,
    preflight_schema_compatibility,
)
from backend.app.project_workers.isolation import DockerIsolationBackend, default_isolation_profile


def collect_project_runtime_diagnostics(
    database_path: str | Path,
    *,
    check_docker: bool = True,
) -> dict[str, Any]:
    """Collect bounded read-only operator diagnostics; never repair, pull, or build."""
    path = Path(database_path).expanduser().resolve()
    report: dict[str, Any] = {
        "schema_version": "astra.project-doctor.report.v1",
        "database": {"path": str(path), "exists": path.is_file()},
        "schema": {"current": 0, "latest": LATEST_SCHEMA_VERSION, "pending": LATEST_SCHEMA_VERSION},
        "worker": {"available_instances": 0, "last_heartbeat_at": None},
        "counts": {}, "last_failures": [],
        "docker": {"checked": check_docker, "available": None, "failure_code": None, "detail": None},
        "safe_to_start": False,
    }
    if not path.is_file():
        report["database"]["error"] = "database_not_found"
        return report
    try:
        current = current_schema_version(path)
        preflight_schema_compatibility(path)
        report["schema"] = {
            "current": current, "latest": LATEST_SCHEMA_VERSION,
            "pending": max(0, LATEST_SCHEMA_VERSION - current),
        }
    except MigrationError as exc:
        report["database"]["error"] = "schema_incompatible"
        report["database"]["detail"] = _bounded(str(exc))
        return report
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        report["counts"] = {
            "pending_worker_requests": _count(connection, tables, "project_worker_requests", "status IN ('pending','claimed','running')"),
            "pending_coordinator_intents": _count(connection, tables, "project_coordinator_intents", "status IN ('pending','claimed')"),
            "pending_cancellations": _count(connection, tables, "project_execution_cancellations", "status != 'acknowledged'"),
            "projection_failures": _count(connection, tables, "project_projection_checkpoints", "status = 'failed'"),
            "historical_read_only_records": _count(connection, tables, "project_compatibility_records", "read_only = 1"),
        }
        runtime_columns = _columns(connection, tables, "project_worker_runtime_instances")
        if {"status", "last_heartbeat_at"}.issubset(runtime_columns):
            row = connection.execute(
                "SELECT COUNT(*) AS count, MAX(last_heartbeat_at) AS heartbeat FROM project_worker_runtime_instances WHERE status = 'available'"
            ).fetchone()
            report["worker"] = {"available_instances": int(row["count"]), "last_heartbeat_at": row["heartbeat"]}
        failures: list[dict[str, Any]] = []
        worker_columns = _columns(connection, tables, "project_worker_requests")
        if {
            "worker_request_id", "project_run_id", "failure_classification", "updated_at",
        }.issubset(worker_columns):
            for row in connection.execute(
                "SELECT worker_request_id, project_run_id, failure_classification, updated_at FROM project_worker_requests "
                "WHERE failure_classification IS NOT NULL ORDER BY updated_at DESC LIMIT 5"
            ):
                failures.append({
                    "kind": "worker", "id": str(row["worker_request_id"]),
                    "project_run_id": str(row["project_run_id"]),
                    "classification": _bounded(str(row["failure_classification"]), 120),
                    "updated_at": str(row["updated_at"]),
                })
        coordinator_columns = _columns(connection, tables, "project_coordinator_intents")
        if {
            "coordinator_intent_id", "project_run_id", "last_failure_classification", "updated_at",
        }.issubset(coordinator_columns):
            for row in connection.execute(
                "SELECT coordinator_intent_id, project_run_id, last_failure_classification, updated_at "
                "FROM project_coordinator_intents WHERE last_failure_classification IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 5"
            ):
                failures.append({
                    "kind": "coordinator", "id": str(row["coordinator_intent_id"]),
                    "project_run_id": str(row["project_run_id"]),
                    "classification": _bounded(str(row["last_failure_classification"]), 120),
                    "updated_at": str(row["updated_at"]),
                })
        report["last_failures"] = sorted(failures, key=lambda item: item["updated_at"], reverse=True)[:8]
    finally:
        connection.close()
    if check_docker:
        capability = DockerIsolationBackend(default_isolation_profile()).probe()
        report["docker"] = {
            "checked": True, "available": capability.available,
            "failure_code": capability.failure_code,
            "detail": _bounded(capability.detail or "") or None,
            "image_reference": capability.image_reference,
            "expected_digest": capability.configured_image_digest,
            "observed_digest": capability.observed_image_digest,
        }
    report["safe_to_start"] = (
        report["schema"]["pending"] == 0
        and (not check_docker or report["docker"]["available"] is True)
    )
    return report


def _count(connection: sqlite3.Connection, tables: set[str], table: str, where: str) -> int:
    if table not in tables:
        return 0
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0])
    except sqlite3.OperationalError:
        # Older additive table shapes may not expose the optional status column.
        # The doctor is deliberately read-only, so an unavailable diagnostic is
        # reported as zero instead of attempting an on-read repair or migration.
        return 0


def _columns(connection: sqlite3.Connection, tables: set[str], table: str) -> set[str]:
    if table not in tables:
        return set()
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _bounded(value: str, limit: int = 300) -> str:
    return " ".join(value.split())[:limit]


__all__ = ["collect_project_runtime_diagnostics"]
