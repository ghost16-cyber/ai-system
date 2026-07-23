from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app.database.migrations import (
    LATEST_SCHEMA_VERSION,
    MigrationError,
    SCHEMA_MIGRATIONS,
    apply_schema_migrations,
    assert_schema_compatible,
    build_schema_migrations,
)


def test_latest_schema_version_is_17(tmp_path: Path) -> None:
    assert LATEST_SCHEMA_VERSION == 17
    assert SCHEMA_MIGRATIONS[16].version == 17
    assert SCHEMA_MIGRATIONS[16].name == "phase8_runtime_orchestration"


def test_migrations_1_through_16_checksums_are_unchanged_by_migration_17() -> None:
    """Regression guard for the migration-checksum incident earlier in this
    project: migration 17 must be purely additive and must never alter the
    checksum of any already-applied migration."""
    rebuilt = build_schema_migrations()
    for version in range(1, 17):
        original = SCHEMA_MIGRATIONS[version - 1]
        again = rebuilt[version - 1]
        assert original.version == version
        assert again.checksum == original.checksum


def test_migration_17_applies_on_fresh_database_and_creates_runtime_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "fresh.db"
    result = apply_schema_migrations(database)
    assert result.applied_versions == tuple(range(1, 18))
    assert result.current_version == 17
    assert assert_schema_compatible(database) == 17

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    for table in (
        "runtime_background_jobs",
        "runtime_state_events",
        "runtime_recovery_events",
        "runtime_cache_statistics",
        "runtime_indexing_history",
        "runtime_telemetry_snapshots",
    ):
        assert table in tables


def test_migration_17_upgrades_an_existing_16_database_without_losing_data(
    tmp_path: Path,
) -> None:
    database = tmp_path / "upgrade.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:16])
    assert assert_schema_compatible(database, migrations=SCHEMA_MIGRATIONS[:16]) == 16

    result = apply_schema_migrations(database)
    assert result.applied_versions == (17,)
    assert assert_schema_compatible(database) == 17


def test_append_only_runtime_tables_reject_update_and_delete(tmp_path: Path) -> None:
    database = tmp_path / "immutable.db"
    apply_schema_migrations(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO runtime_state_events "
            "(event_id, from_state, to_state, transition_trigger, reason, event_json, created_at) "
            "VALUES ('event-1', 'stopped', 'initializing', 'startup', NULL, '{}', '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE runtime_state_events SET reason = 'x' WHERE event_id = 'event-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM runtime_state_events WHERE event_id = 'event-1'")


def test_runtime_background_jobs_table_is_mutable_for_queue_transitions(
    tmp_path: Path,
) -> None:
    """Unlike the event/history/telemetry tables, the job queue is a live
    queue (mirrors local_ai_scheduler_jobs) and must support status/lease
    UPDATEs."""
    database = tmp_path / "jobs.db"
    apply_schema_migrations(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO runtime_background_jobs "
            "(job_id, idempotency_key, request_hash, job_type, target_id, status, priority, job_json, created_at, updated_at) "
            "VALUES ('job-1', 'idem-1', 'hash-1', 'corpus_reindex', 'project-1', 'queued', 100, '{}', "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
        )
        connection.commit()
        connection.execute(
            "UPDATE runtime_background_jobs SET status = 'claimed' WHERE job_id = 'job-1'"
        )
        connection.commit()
        status = connection.execute(
            "SELECT status FROM runtime_background_jobs WHERE job_id = 'job-1'"
        ).fetchone()[0]
    assert status == "claimed"


def test_schema_migration_registry_still_contiguous_after_migration_17() -> None:
    versions = tuple(migration.version for migration in SCHEMA_MIGRATIONS)
    assert versions == tuple(range(1, 18))
    assert len(set(versions)) == len(versions)
