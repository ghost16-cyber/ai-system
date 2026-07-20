from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.database.migrations import (
    LATEST_SCHEMA_VERSION,
    MigrationError,
    SCHEMA_MIGRATIONS,
    SchemaMigration,
    SchemaMigrationStep,
    apply_schema_migrations,
    assert_schema_compatible,
    build_schema_migrations,
    current_schema_version,
)
from backend.app.database.repository import AnalysisRepository
from backend.app.project_control import (
    ProjectCommand,
    ProjectCommandType,
    ProjectControlPlane,
)
from backend.app.project_coordinator import ProjectCoordinatorService
from backend.app.project_workers import ProjectWorkerQueue
from backend.app.project_workers.mutations import FileMutationEngine


EXPECTED_STAGE3A_TABLES = {
    "schema_migrations",
    "project_artifacts",
    "project_model_invocations",
    "project_execution_cancellations",
    "project_projection_checkpoints",
}

_CHECKPOINT_ORDER = ("stage0", "stage1", "stage2a", "stage2b", "stage2c")
_CHECKPOINT_DDL: dict[str, tuple[str, ...]] = {
    "stage0": (
        """CREATE TABLE chat_conversations (
            conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE chat_requests (
            request_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            status TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            request_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE chat_runs (
            run_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            request_id TEXT, status TEXT NOT NULL, response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
    ),
    "stage1": (
        """CREATE TABLE project_runs (
            project_run_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL, repository_root_fingerprint TEXT NOT NULL,
            lifecycle_status TEXT NOT NULL, state_version INTEGER NOT NULL,
            schema_version TEXT NOT NULL, run_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE project_scope_revisions (
            scope_revision_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL, content_hash TEXT NOT NULL,
            schema_version TEXT NOT NULL, revision_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE project_plan_revisions_v3 (
            plan_revision_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
            scope_revision_id TEXT NOT NULL, revision_number INTEGER NOT NULL,
            content_hash TEXT NOT NULL, required_manifest_hash TEXT NOT NULL,
            schema_version TEXT NOT NULL, revision_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE project_approval_grants (
            approval_grant_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
            approval_type TEXT NOT NULL, plan_revision_id TEXT NOT NULL,
            scope_revision_id TEXT NOT NULL, manifest_hash TEXT NOT NULL,
            authority_hash TEXT NOT NULL, schema_version TEXT NOT NULL,
            grant_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE project_events (
            event_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
            request_id TEXT NOT NULL, previous_state_version INTEGER NOT NULL,
            resulting_state_version INTEGER NOT NULL, schema_version TEXT NOT NULL,
            event_json TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
    ),
    "stage2a": (
        """CREATE TABLE project_execution_attempts (
            execution_attempt_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
            attempt_type TEXT NOT NULL, status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, plan_revision_id TEXT NOT NULL,
            scope_revision_id TEXT NOT NULL, attempt_number INTEGER NOT NULL,
            schema_version TEXT NOT NULL, attempt_json TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT
        )""",
        """CREATE TABLE project_execution_dispatches (
            execution_dispatch_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
            execution_attempt_id TEXT NOT NULL, attempt_type TEXT NOT NULL,
            status TEXT NOT NULL, expected_project_state_version INTEGER NOT NULL,
            priority INTEGER NOT NULL, enqueue_idempotency_key TEXT NOT NULL,
            available_at TEXT NOT NULL, schema_version TEXT NOT NULL,
            dispatch_json TEXT NOT NULL, worker_request_id TEXT,
            created_at TEXT NOT NULL, dispatched_at TEXT, cancelled_at TEXT,
            failure_classification TEXT
        )""",
    ),
    "stage2b": (
        """CREATE TABLE project_worker_requests (
            worker_request_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
            execution_attempt_id TEXT NOT NULL, attempt_type TEXT NOT NULL,
            status TEXT NOT NULL, priority INTEGER NOT NULL,
            available_at TEXT NOT NULL, delivery_count INTEGER NOT NULL,
            max_deliveries INTEGER NOT NULL, request_hash TEXT NOT NULL,
            enqueue_idempotency_key TEXT NOT NULL, schema_version TEXT NOT NULL,
            request_json TEXT NOT NULL, result_json TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE project_worker_runtime_instances (
            worker_id TEXT PRIMARY KEY, execution_backend TEXT NOT NULL,
            status TEXT NOT NULL, schema_version TEXT NOT NULL,
            instance_json TEXT NOT NULL, started_at TEXT NOT NULL,
            last_heartbeat_at TEXT NOT NULL
        )""",
    ),
    "stage2c": (
        """CREATE TABLE project_file_mutation_specs (
            file_mutation_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
            execution_attempt_id TEXT NOT NULL, mutation_kind TEXT NOT NULL,
            authority_id TEXT NOT NULL, spec_hash TEXT NOT NULL,
            schema_version TEXT NOT NULL, spec_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE project_file_mutation_journals (
            file_mutation_id TEXT PRIMARY KEY, status TEXT NOT NULL,
            applied_operations INTEGER NOT NULL, journal_json TEXT NOT NULL,
            result_json TEXT, failure_classification TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE project_file_mutation_snapshots (
            file_mutation_id TEXT NOT NULL, operation_index INTEGER NOT NULL,
            relative_path TEXT NOT NULL, existed_before INTEGER NOT NULL,
            preimage_sha256 TEXT, snapshot_path TEXT, original_mode INTEGER,
            staged_path TEXT, PRIMARY KEY(file_mutation_id, operation_index)
        )""",
        """CREATE TABLE project_coordinator_intents (
            coordinator_intent_id TEXT PRIMARY KEY, project_run_id TEXT NOT NULL,
            intent_type TEXT NOT NULL, status TEXT NOT NULL,
            trigger_event_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
            plan_revision_id TEXT NOT NULL, scope_revision_id TEXT NOT NULL,
            manifest_hash TEXT NOT NULL, expected_project_state_version INTEGER NOT NULL,
            payload_hash TEXT NOT NULL, schema_version TEXT NOT NULL,
            intent_json TEXT NOT NULL, lease_expires_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
        )""",
    ),
}


def _checkpoint_tables(checkpoint: str) -> tuple[str, ...]:
    index = _CHECKPOINT_ORDER.index(checkpoint)
    tables: list[str] = []
    for stage in _CHECKPOINT_ORDER[: index + 1]:
        for statement in _CHECKPOINT_DDL[stage]:
            tables.append(statement.split("CREATE TABLE ", 1)[1].split(" ", 1)[0])
    return tuple(tables)


def _build_checkpoint_fixture(database: Path, checkpoint: str) -> tuple[str, ...]:
    tables = _checkpoint_tables(checkpoint)
    with sqlite3.connect(database) as connection:
        for stage in _CHECKPOINT_ORDER[: _CHECKPOINT_ORDER.index(checkpoint) + 1]:
            for statement in _CHECKPOINT_DDL[stage]:
                connection.execute(statement)
        connection.execute(
            "INSERT INTO chat_conversations VALUES (?, ?, ?, ?)",
            ("conversation-1", "Existing conversation", "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO chat_requests VALUES (?, ?, ?, ?, ?, ?)",
            ("request-1", "conversation-1", "completed", "request-key", "{}", "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO chat_runs VALUES (?, ?, ?, ?, ?, ?)",
            ("run-1", "conversation-1", "request-1", "completed", "{}", "2026-01-01"),
        )
        if checkpoint != "stage0":
            connection.execute(
                "INSERT INTO project_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "project-1", "conversation-1", "workspace-1", "root-fingerprint",
                    "ready_for_work", 3, "astra.project-control.project-run.v1",
                    '{"project_run_id":"project-1","lifecycle_status":"ready_for_work"}',
                    "2026-01-01", "2026-01-01",
                ),
            )
            connection.execute(
                "INSERT INTO project_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "event-1", "project-1", 1, "initialize_project", "request-1",
                    0, 1, "astra.project-control.event.v1", "{}", "2026-01-01",
                ),
            )
        if checkpoint in {"stage2b", "stage2c"}:
            connection.execute(
                "INSERT INTO project_worker_runtime_instances VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "worker-1", "docker", "stopped",
                    "astra.project-workers.runtime-instance.v1", "{}",
                    "2026-01-01", "2026-01-01",
                ),
            )
        if checkpoint == "stage2c":
            connection.execute(
                "INSERT INTO project_coordinator_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "intent-1", "project-1", "prepare_work_unit", "completed", "event-1",
                    "intent-key", "plan-1", "scope-1", "a" * 64, 3, "b" * 64,
                    "astra.project-coordinator.intent.v1", "{}", None,
                    "2026-01-01", "2026-01-01", "2026-01-01",
                ),
            )
    return tables


def _table_snapshot(database: Path, tables: tuple[str, ...]) -> dict[str, object]:
    with sqlite3.connect(database) as connection:
        return {
            table: {
                "columns": tuple(
                    (row[1], row[2], row[3], row[5])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                ),
                "rows": tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid")),
            }
            for table in tables
        }


@pytest.mark.parametrize("checkpoint", _CHECKPOINT_ORDER)
def test_representative_checkpoint_upgrades_without_rewriting_existing_state(
    tmp_path: Path, checkpoint: str
) -> None:
    database = tmp_path / f"{checkpoint}.db"
    existing_tables = _build_checkpoint_fixture(database, checkpoint)
    before = _table_snapshot(database, existing_tables)

    result = apply_schema_migrations(database)

    assert result.applied_versions == tuple(range(1, LATEST_SCHEMA_VERSION + 1))
    assert current_schema_version(database) == LATEST_SCHEMA_VERSION
    assert assert_schema_compatible(database) == LATEST_SCHEMA_VERSION
    assert _table_snapshot(database, existing_tables) == before
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert EXPECTED_STAGE3A_TABLES <= tables


def test_rerun_is_idempotent_and_concurrent_initializers_serialize(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent.db"
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda _: apply_schema_migrations(database), range(4))
        )

    assert sum(bool(result.applied_versions) for result in results) == 1
    schema_before = _sqlite_schema_snapshot(database)
    ledger_before = _migration_ledger(database)
    assert apply_schema_migrations(database).applied_versions == ()
    assert _sqlite_schema_snapshot(database) == schema_before
    assert _migration_ledger(database) == ledger_before
    assert len(ledger_before) == LATEST_SCHEMA_VERSION


_MIGRATION_BOUNDARIES = tuple(
    boundary
    for migration in SCHEMA_MIGRATIONS
    for boundary in (
        ("before_migration", migration.version, None),
        *(
            item
            for step in migration.steps
            for item in (
                ("before_step", migration.version, step.step_id),
                ("after_step", migration.version, step.step_id),
            )
        ),
        ("after_migration", migration.version, None),
    )
)


@pytest.mark.parametrize(
    "failure_boundary",
    _MIGRATION_BOUNDARIES,
    ids=lambda value: "-".join(str(item or "migration") for item in value),
)
def test_every_migration_and_step_boundary_is_restart_safe(
    tmp_path: Path, failure_boundary: tuple[str, int, str | None]
) -> None:
    database = tmp_path / (
        f"interrupt-{failure_boundary[0]}-{failure_boundary[1]}-"
        f"{failure_boundary[2] or 'migration'}.db"
    )

    def interrupt(event: str, version: int, step_id: str | None) -> None:
        if (event, version, step_id) == failure_boundary:
            raise RuntimeError("injected migration interruption")

    with pytest.raises(MigrationError, match="injected migration interruption"):
        apply_schema_migrations(database, boundary=interrupt)
    assert current_schema_version(database) == 0

    result = apply_schema_migrations(database)
    assert result.current_version == LATEST_SCHEMA_VERSION
    schema_after_recovery = _sqlite_schema_snapshot(database)
    ledger_after_recovery = _migration_ledger(database)
    assert apply_schema_migrations(database).applied_versions == ()
    assert _sqlite_schema_snapshot(database) == schema_after_recovery
    assert _migration_ledger(database) == ledger_after_recovery
    assert len(ledger_after_recovery) == LATEST_SCHEMA_VERSION


def test_checksum_is_stable_across_registry_and_callback_reconstruction() -> None:
    rebuilt = build_schema_migrations()
    assert [migration.checksum for migration in rebuilt] == [
        migration.checksum for migration in SCHEMA_MIGRATIONS
    ]

    original = SCHEMA_MIGRATIONS[1]
    source_independent_steps = tuple(
        SchemaMigrationStep(
            step.step_id,
            step.checksum_material,
            lambda _connection: None,
        )
        for step in original.steps
    )
    source_independent = SchemaMigration(
        original.version,
        original.name,
        original.checksum_material,
        source_independent_steps,
    )
    assert source_independent.checksum == original.checksum


def test_modified_explicit_checksum_material_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "modified-material.db"
    apply_schema_migrations(database)
    modified = list(build_schema_migrations())
    modified[1] = replace(
        modified[1],
        checksum_material=modified[1].checksum_material + ":changed",
    )

    with pytest.raises(MigrationError, match="checksum"):
        apply_schema_migrations(database, migrations=modified)


@pytest.mark.parametrize("invalid_kind", ["checksum", "newer"])
@pytest.mark.parametrize(
    "initializer_name", ["repository", "control", "queue", "mutation", "coordinator"]
)
def test_invalid_ledger_is_rejected_before_any_compatibility_ddl_or_file_change(
    tmp_path: Path, initializer_name: str, invalid_kind: str
) -> None:
    database = tmp_path / f"{initializer_name}-{invalid_kind}.db"
    apply_schema_migrations(database)
    with sqlite3.connect(database) as connection:
        if invalid_kind == "checksum":
            connection.execute(
                "UPDATE schema_migrations SET checksum = ? WHERE version = 2",
                ("0" * 64,),
            )
        else:
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                (LATEST_SCHEMA_VERSION + 1, "future", "f" * 64, "2026-01-01"),
            )
    schema_before = _sqlite_schema_snapshot(database)
    bytes_before = database.read_bytes()
    stat_before = database.stat()
    journal_root = tmp_path / "mutation-journal"

    initializer = _initializer(initializer_name, database, journal_root)
    expected = "checksum" if invalid_kind == "checksum" else "newer"
    with pytest.raises(MigrationError, match=expected):
        initializer()

    assert _sqlite_schema_snapshot(database) == schema_before
    assert database.read_bytes() == bytes_before
    assert database.stat().st_size == stat_before.st_size
    assert database.stat().st_mtime_ns == stat_before.st_mtime_ns
    assert not journal_root.exists()


def test_assert_compatible_requires_initialized_current_ledger(tmp_path: Path) -> None:
    with pytest.raises(MigrationError, match="not been initialized"):
        assert_schema_compatible(tmp_path / "missing.db")


def test_stage2c_project_records_remain_readable_without_emitting_work(
    tmp_path: Path,
) -> None:
    database = tmp_path / "stage2c-project.db"
    control = ProjectControlPlane(database)
    control.initialize()
    control.execute(
        ProjectCommand(
            command_type=ProjectCommandType.INITIALIZE_PROJECT,
            project_run_id="existing-project",
            conversation_id="existing-conversation",
            workspace_id="existing-workspace",
            repository_root="canonical-root",
            repository_root_fingerprint="root-fingerprint",
            actor_id="local-user",
            expected_state_version=0,
            idempotency_key="initialize-existing",
        )
    )
    events_before = control.list_events("existing-project")
    with sqlite3.connect(database) as connection:
        for table in (
            "project_projection_checkpoints",
            "project_execution_cancellations",
            "project_model_invocations",
            "project_artifacts",
            "schema_migrations",
        ):
            connection.execute(f"DROP TABLE {table}")

    apply_schema_migrations(database)
    restarted = ProjectControlPlane(database)
    restarted.initialize()

    assert restarted.get_project("existing-project").conversation_id == "existing-conversation"
    assert restarted.list_events("existing-project") == events_before
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_model_invocations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM project_artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM project_execution_cancellations").fetchone()[0] == 0
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_worker_requests'"
        ).fetchone() is None


def _initializer(
    name: str, database: Path, journal_root: Path
):
    if name == "repository":
        return AnalysisRepository(database).initialize
    if name == "control":
        return ProjectControlPlane(database).initialize
    if name == "queue":
        return ProjectWorkerQueue(database).initialize
    if name == "mutation":
        return FileMutationEngine(database, journal_root).initialize
    if name == "coordinator":
        return ProjectCoordinatorService(database, object()).initialize
    raise AssertionError(f"unsupported initializer: {name}")


def _sqlite_schema_snapshot(database: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(database) as connection:
        return tuple(
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
            )
        )


def _migration_ledger(database: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(database) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if exists is None:
            return ()
        return tuple(
            connection.execute(
                "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version"
            )
        )
