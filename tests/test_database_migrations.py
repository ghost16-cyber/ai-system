from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.database.migrations import (
    LATEST_SCHEMA_VERSION,
    MigrationError,
    MigrationBackupError,
    SCHEMA_MIGRATIONS,
    SchemaMigration,
    SchemaMigrationStep,
    apply_schema_migrations,
    assert_schema_compatible,
    build_schema_migrations,
    current_schema_version,
    preflight_schema_compatibility,
    _sqlite_logical_sha256,
    _PHASE4A_SCHEMA_SQL,
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


def _assert_existing_state_preserved(
    before: dict[str, object], after: dict[str, object]
) -> None:
    for table, original_value in before.items():
        original = dict(original_value)
        current = dict(after[table])
        original_columns = tuple(original["columns"])
        current_columns = tuple(current["columns"])
        assert current_columns[: len(original_columns)] == original_columns
        original_rows = tuple(original["rows"])
        current_rows = tuple(current["rows"])
        assert tuple(
            tuple(row[: len(original_columns)]) for row in current_rows
        ) == original_rows


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
    _assert_existing_state_preserved(
        before,
        _table_snapshot(database, existing_tables),
    )
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


def test_existing_database_is_backed_up_before_stage3h_data_tagging(tmp_path: Path) -> None:
    database = tmp_path / "pre-stage3h.db"
    _build_checkpoint_fixture(database, "stage2c")
    result = apply_schema_migrations(database)
    assert result.backup_manifest_path is not None
    manifest_path = Path(result.backup_manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backup = Path(manifest["backup_file_path"])
    assert backup.is_file()
    assert manifest["manifest_schema_version"] == "astra.migration-backup-manifest.v1"
    assert manifest["source_database_path"] == database.resolve().as_posix()
    assert manifest["source_schema_version"] == 0
    assert manifest["target_schema_version"] == LATEST_SCHEMA_VERSION
    assert manifest["source_sha256"]
    assert manifest["backup_sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert manifest["source_logical_sha256"] == manifest["backup_logical_sha256"]
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_runs'"
        ).fetchone() is not None
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM project_compatibility_state WHERE key = 'compatibility_removal_version'"
        ).fetchone()[0] == "stage3h-v1"


def _build_v9_database(database: Path) -> None:
    result = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:9])
    assert result.current_version == 9


def _single_backup_manifest(database: Path) -> Path:
    manifests = tuple(database.parent.glob(f"{database.name}.migration-*.bak.manifest.json"))
    assert len(manifests) == 1
    return manifests[0]


def _rewrite_manifest(manifest_path: Path, **updates: object) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(updates)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    return manifest


def test_fresh_exact_v9_backup_permits_migration_10_and_records_binding(tmp_path: Path) -> None:
    database = tmp_path / "fresh-v9.db"
    _build_v9_database(database)
    source_bytes = database.read_bytes()
    source_stat = database.stat()

    result = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:10])

    assert result.applied_versions == (10,)
    manifest_path = Path(result.backup_manifest_path or "")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_schema_version"] == 9
    assert manifest["target_schema_version"] == 10
    assert manifest["source_file_size"] == len(source_bytes)
    assert manifest["source_file_mtime_ns"] == source_stat.st_mtime_ns
    assert manifest["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert manifest["migration_operation_id"] in manifest_path.name
    assert current_schema_version(database) == 10
    assert assert_schema_compatible(database, migrations=SCHEMA_MIGRATIONS[:10]) == 10


def test_source_change_after_backup_is_rejected_and_safe_retry_works(tmp_path: Path) -> None:
    database = tmp_path / "changed-after-backup.db"
    _build_v9_database(database)

    def mutate_after_manifest(event: str, _version: int, _step: str | None) -> None:
        if event == "after_backup_manifest":
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE concurrent_source_change (value TEXT)")

    with pytest.raises(MigrationBackupError) as error:
        apply_schema_migrations(
            database, migrations=SCHEMA_MIGRATIONS[:10], boundary=mutate_after_manifest
        )
    assert error.value.code == "migration_backup_source_changed"
    assert current_schema_version(database) == 9
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'concurrent_source_change'"
        ).fetchone() is not None

    assert apply_schema_migrations(
        database, migrations=SCHEMA_MIGRATIONS[:10]
    ).applied_versions == (10,)
    assert assert_schema_compatible(database, migrations=SCHEMA_MIGRATIONS[:10]) == 10


def test_backup_from_another_database_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    other = tmp_path / "other.db"
    _build_v9_database(database)
    with sqlite3.connect(other) as connection:
        connection.execute("CREATE TABLE unrelated (secretless_value TEXT)")

    def substitute_backup(event: str, _version: int, _step: str | None) -> None:
        if event != "after_backup_manifest":
            return
        manifest_path = _single_backup_manifest(database)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        backup = Path(manifest["backup_file_path"])
        shutil.copyfile(other, backup)
        _rewrite_manifest(
            manifest_path,
            backup_file_size=backup.stat().st_size,
            backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
            backup_logical_sha256=_sqlite_logical_sha256(backup),
        )

    with pytest.raises(MigrationBackupError) as error:
        apply_schema_migrations(database, boundary=substitute_backup)
    assert error.value.code == "migration_backup_source_mismatch"
    assert current_schema_version(database) == 9


@pytest.mark.parametrize("failure", ["missing", "malformed"])
def test_missing_or_malformed_backup_manifest_is_rejected(
    tmp_path: Path, failure: str
) -> None:
    database = tmp_path / f"manifest-{failure}.db"
    _build_v9_database(database)

    def damage_manifest(event: str, _version: int, _step: str | None) -> None:
        if event != "after_backup_manifest":
            return
        manifest_path = _single_backup_manifest(database)
        if failure == "missing":
            manifest_path.unlink()
        else:
            manifest_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(MigrationBackupError) as error:
        apply_schema_migrations(database, boundary=damage_manifest)
    assert error.value.code == (
        "migration_backup_manifest_missing"
        if failure == "missing"
        else "migration_backup_manifest_malformed"
    )
    assert current_schema_version(database) == 9


def test_corrupted_or_missing_backup_is_rejected(tmp_path: Path) -> None:
    for failure in ("corrupt", "missing"):
        database = tmp_path / f"backup-{failure}.db"
        _build_v9_database(database)

        def damage_backup(event: str, _version: int, _step: str | None) -> None:
            if event != "after_backup_manifest":
                return
            manifest = json.loads(
                _single_backup_manifest(database).read_text(encoding="utf-8")
            )
            backup = Path(manifest["backup_file_path"])
            if failure == "corrupt":
                backup.write_bytes(b"not-a-sqlite-database")
            else:
                backup.unlink()

        with pytest.raises(MigrationBackupError) as error:
            apply_schema_migrations(database, boundary=damage_backup)
        assert error.value.code == (
            "migration_backup_hash_mismatch"
            if failure == "corrupt"
            else "migration_backup_missing"
        )
        assert current_schema_version(database) == 9


def test_wrong_backup_target_or_source_version_is_rejected(tmp_path: Path) -> None:
    for field, value, expected_code in (
        ("target_schema_version", 9, "migration_backup_target_mismatch"),
        ("source_schema_version", 8, "migration_backup_source_version_mismatch"),
    ):
        database = tmp_path / f"wrong-{field}.db"
        _build_v9_database(database)

        def alter_binding(event: str, _version: int, _step: str | None) -> None:
            if event == "after_backup_manifest":
                _rewrite_manifest(_single_backup_manifest(database), **{field: value})

        with pytest.raises(MigrationBackupError) as error:
            apply_schema_migrations(database, boundary=alter_binding)
        assert error.value.code == expected_code
        assert current_schema_version(database) == 9


def test_interrupted_backup_creation_blocks_migration_and_retains_identity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "interrupted-backup.db"
    _build_v9_database(database)

    def interrupt(event: str, _version: int, _step: str | None) -> None:
        if event == "after_backup":
            raise RuntimeError("injected backup interruption")

    with pytest.raises(MigrationBackupError) as error:
        apply_schema_migrations(
            database, migrations=SCHEMA_MIGRATIONS[:10], boundary=interrupt
        )
    assert error.value.code == "migration_backup_interrupted"
    assert current_schema_version(database) == 9
    orphaned_backups = tuple(database.parent.glob(f"{database.name}.migration-*.bak"))
    assert len(orphaned_backups) == 1
    assert not Path(f"{orphaned_backups[0]}.manifest.json").exists()

    result = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:10])
    assert result.applied_versions == (10,)
    assert Path(result.backup_manifest_path or "").is_file()
    assert orphaned_backups[0].is_file()


def test_migration_11_backfills_canonical_replay_and_retires_old_authority(
    tmp_path: Path,
) -> None:
    database = tmp_path / "phase4a-replay-upgrade.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:10])
    # Current Phase 4A service reads the new invalidation projection while the
    # fixture deliberately remains on the old v10 replay schema. Installing
    # only this empty table lets us create a representative legacy replay row;
    # Migration 11 still owns/backfills/validates the actual upgrade.
    with sqlite3.connect(database) as connection:
        for statement in _PHASE4A_SCHEMA_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)
    control = ProjectControlPlane(database)
    initialize = ProjectCommand(
        command_type=ProjectCommandType.INITIALIZE_PROJECT,
        project_run_id="upgrade-project",
        conversation_id="upgrade-conversation",
        workspace_id="upgrade-workspace",
        repository_root="canonical-root",
        repository_root_fingerprint="upgrade-root",
        actor_id="local-user",
        expected_state_version=0,
        idempotency_key="upgrade-initialize",
    )
    original = control.execute(initialize)
    with sqlite3.connect(database) as connection:
        replay = connection.execute(
            "SELECT action_type, request_fingerprint, replay_json, created_at "
            "FROM project_action_replays WHERE project_run_id = ? AND idempotency_key = ?",
            (initialize.project_run_id, initialize.idempotency_key),
        ).fetchone()
        result_json = json.dumps(
            json.loads(replay[2])["result"], sort_keys=True, separators=(",", ":")
        )
        connection.execute(
            "INSERT INTO project_idempotency "
            "(project_run_id, idempotency_key, command_type, request_hash, result_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                initialize.project_run_id, initialize.idempotency_key,
                replay[0], replay[1], result_json, replay[3],
            ),
        )
        connection.execute(
            "DELETE FROM project_action_replays WHERE project_run_id = ? AND idempotency_key = ?",
            (initialize.project_run_id, initialize.idempotency_key),
        )

    result = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:11])
    assert result.applied_versions == (11,)
    with sqlite3.connect(database) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "project_idempotency" not in tables
        assert "project_idempotency_legacy" in tables
        assert connection.execute(
            "SELECT COUNT(*) FROM project_action_replays WHERE project_run_id = ? "
            "AND idempotency_key = ?",
            (initialize.project_run_id, initialize.idempotency_key),
        ).fetchone()[0] == 1

    restarted = ProjectControlPlane(database)
    restarted.initialize()
    replayed = restarted.replay_completed(initialize)
    assert replayed is not None and replayed.replayed is True
    assert replayed.model_dump(exclude={"replayed"}) == original.model_dump(exclude={"replayed"})


def test_migration_11_rejects_conflicting_replay_authorities(tmp_path: Path) -> None:
    database = tmp_path / "phase4a-conflicting-replay.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:10])
    with sqlite3.connect(database) as connection:
        for statement in _PHASE4A_SCHEMA_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)
    control = ProjectControlPlane(database)
    initialize = ProjectCommand(
        command_type=ProjectCommandType.INITIALIZE_PROJECT,
        project_run_id="conflict-project",
        conversation_id="conflict-conversation",
        workspace_id="conflict-workspace",
        repository_root="canonical-root",
        repository_root_fingerprint="conflict-root",
        actor_id="local-user",
        expected_state_version=0,
        idempotency_key="conflict-initialize",
    )
    control.execute(initialize)
    with sqlite3.connect(database) as connection:
        replay = connection.execute(
            "SELECT action_type, replay_json, created_at FROM project_action_replays "
            "WHERE project_run_id = ? AND idempotency_key = ?",
            (initialize.project_run_id, initialize.idempotency_key),
        ).fetchone()
        connection.execute(
            "INSERT INTO project_idempotency "
            "(project_run_id, idempotency_key, command_type, request_hash, result_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                initialize.project_run_id, initialize.idempotency_key, replay[0],
                "0" * 64,
                json.dumps(json.loads(replay[1])["result"]), replay[2],
            ),
        )

    with pytest.raises(MigrationError, match="conflicts"):
        apply_schema_migrations(database)
    assert current_schema_version(database) == 10


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
            "chat_runtime_links",
            "runtime_telemetry_snapshots",
            "runtime_indexing_history",
            "runtime_cache_statistics",
            "runtime_recovery_events",
            "runtime_state_events",
            "runtime_background_jobs",
            "rag_invalidations",
            "rag_retrieval_replays",
            "rag_retrieval_evidence",
            "rag_retrieval_artifacts",
            "rag_retrieval_candidates",
            "rag_retrieval_requests",
            "rag_embeddings",
            "rag_chunks",
            "rag_sources",
            "rag_corpus_generations",
            "project_synthesis_proposal_events",
            "project_synthesis_proposals",
            "project_repair_cycles_v2",
            "project_projection_checkpoints",
            "project_execution_cancellations",
            "project_model_invocations",
            "project_artifacts",
            "local_ai_generation_invocations",
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
            "SELECT COUNT(*) FROM project_worker_requests"
        ).fetchone()[0] == 0


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


def _fully_initialized(tmp_path):
    database = tmp_path / "shape.db"
    ProjectControlPlane(database).initialize()
    return database


def test_shape_validation_fails_closed_on_missing_migration_table(tmp_path: Path) -> None:
    database = _fully_initialized(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE project_coordinator_intents")
        connection.commit()
    with pytest.raises(MigrationError) as error:
        assert_schema_compatible(database)
    assert "project_coordinator_intents" in str(error.value)


def test_dropped_required_column_fails_closed_instead_of_silent_repair(tmp_path: Path) -> None:
    database = _fully_initialized(tmp_path)
    # Simulate schema drift: an existing schema-8 table missing a required column.
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("ALTER TABLE project_execution_dispatches RENAME TO _drift_dispatches")
        connection.execute(
            "CREATE TABLE project_execution_dispatches ("
            "execution_dispatch_id TEXT PRIMARY KEY, project_run_id TEXT)"
        )
        connection.commit()
    with pytest.raises(MigrationError) as error:
        assert_schema_compatible(database)
    assert "failure_classification" in str(error.value)
    # Re-running the service initializer must not silently repair the column.
    with pytest.raises(MigrationError):
        ProjectControlPlane(database).initialize()


def test_valid_current_schema_passes_shape_validation(tmp_path: Path) -> None:
    database = _fully_initialized(tmp_path)
    assert assert_schema_compatible(database) == LATEST_SCHEMA_VERSION


# --- Regression coverage for a migration-12 checksum-integrity incident -----
#
# Migration 12 ("production_safe_local_generation_gateway") was applied to a
# live local database with 4 steps (table, 2 indexes, terminal-immutability
# trigger). Before that work was committed, a 5th step (a delete-protection
# trigger) was folded into migration 12's definition instead of becoming its
# own migration -- changing migration 12's checksum without bumping its
# version, so every subsequent startup failed closed with "Schema migration
# 12 checksum does not match the runtime." The fix restores migration 12 to
# its original 4-step definition and moves the delete-protection trigger into
# additive migration 15. These tests pin that exact incident so it cannot
# regress silently.

_ORIGINAL_MIGRATION_12_CHECKSUM = (
    "273ad99e2c85f6f12cd6c0e23cae1bf4ef4643f9b59de048a9648b969b1b667c"
)


def test_original_migration_12_ledger_starts_successfully(tmp_path: Path) -> None:
    """Category 1: a database whose ledger records the true original
    migration-12 checksum (captured from the incident database before this
    fix) starts successfully and upgrades cleanly to the latest version."""
    database = tmp_path / "original-migration-12.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:12])
    assert SCHEMA_MIGRATIONS[11].checksum == _ORIGINAL_MIGRATION_12_CHECKSUM

    assert preflight_schema_compatibility(database) == 12
    result = apply_schema_migrations(database)
    assert result.current_version == LATEST_SCHEMA_VERSION
    assert assert_schema_compatible(database) == LATEST_SCHEMA_VERSION


def test_runtime_mutation_of_migration_12_fails_closed(tmp_path: Path) -> None:
    """Category 3: editing migration 12's declared checksum material at
    runtime (the exact defect class that caused the incident) is detected and
    rejected, rather than silently accepted or silently re-applied."""
    database = tmp_path / "mutated-migration-12.db"
    apply_schema_migrations(database)
    mutated = list(build_schema_migrations())
    mutated[11] = replace(
        mutated[11],
        checksum_material=mutated[11].checksum_material + ":mutated",
    )

    with pytest.raises(MigrationError, match="checksum"):
        preflight_schema_compatibility(database, migrations=mutated)
    with pytest.raises(MigrationError, match="checksum"):
        apply_schema_migrations(database, migrations=mutated)


def test_incorrect_migration_12_checksum_fails_closed(tmp_path: Path) -> None:
    """Category 4: a ledger row for version 12 whose recorded checksum does
    not match any valid runtime definition fails closed, reproducing the
    exact reported error."""
    database = tmp_path / "corrupted-migration-12.db"
    apply_schema_migrations(database)
    schema_before = _sqlite_schema_snapshot(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE version = 12",
            ("f" * 64,),
        )

    with pytest.raises(MigrationError, match="migration 12 checksum"):
        preflight_schema_compatibility(database)
    with pytest.raises(MigrationError, match="migration 12 checksum"):
        assert_schema_compatible(database)
    # Fail-closed: no repair, no rewrite, no silent recovery attempt.
    with sqlite3.connect(database) as connection:
        recorded = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version = 12"
        ).fetchone()[0]
    assert recorded == "f" * 64
    assert _sqlite_schema_snapshot(database) == schema_before


def test_existing_local_ai_generation_data_survives_the_12_to_16_upgrade(
    tmp_path: Path,
) -> None:
    """Category 8: a row written under migration 12 (before the delete-
    protection trigger existed) survives untouched through migrations 13-16,
    and the new trigger becomes active without disturbing prior rows."""
    database = tmp_path / "generation-data-survives.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:12])
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO local_ai_generation_invocations ("
            "generation_id, request_id, idempotency_key, request_fingerprint, "
            "purpose, provider_identity, endpoint_identity, exact_model_tag, "
            "input_hash, context_hash, expected_schema_identity, status, "
            "started_at, diagnostic_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, '{}', ?)",
            (
                "gen-1", "req-1", "idem-1", "fp-1", "purpose", "provider",
                "endpoint", "model", "input-hash", "context-hash", "schema-id",
                "2026-07-22T00:00:00+00:00", "2026-07-22T00:00:00+00:00",
            ),
        )
        connection.commit()

    result = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:16])
    assert result.applied_versions == (13, 14, 15, 16)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT generation_id, request_id, status FROM "
            "local_ai_generation_invocations WHERE generation_id = 'gen-1'"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM local_ai_generation_invocations WHERE generation_id = 'gen-1'"
            )
    assert row == ("gen-1", "req-1", "completed")


def test_schema_migration_registry_has_unique_contiguous_versions(tmp_path: Path) -> None:
    """Category 9: the live registry's versions are exactly 1..N, unique and
    contiguous, and the shared constructor rejects a registry that is not."""
    versions = tuple(migration.version for migration in SCHEMA_MIGRATIONS)
    assert versions == tuple(range(1, len(SCHEMA_MIGRATIONS) + 1))
    assert len(set(versions)) == len(versions)
    assert LATEST_SCHEMA_VERSION == 18
    assert SCHEMA_MIGRATIONS[11].version == 12
    assert SCHEMA_MIGRATIONS[11].name == "production_safe_local_generation_gateway"


def test_migration_16_adds_canonical_rag_schema_and_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "rag-upgrade.db"
    apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:15])

    result = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:16])
    replay = apply_schema_migrations(database, migrations=SCHEMA_MIGRATIONS[:16])

    assert result.applied_versions == (16,)
    assert replay.applied_versions == ()
    assert assert_schema_compatible(database, migrations=SCHEMA_MIGRATIONS[:16]) == 16
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'rag_%'"
            )
        }
    assert {
        "rag_sources",
        "rag_chunks",
        "rag_embeddings",
        "rag_corpus_generations",
        "rag_retrieval_requests",
        "rag_retrieval_candidates",
        "rag_retrieval_artifacts",
        "rag_retrieval_evidence",
        "rag_retrieval_replays",
        "rag_invalidations",
    } <= tables

    gapped = SCHEMA_MIGRATIONS[:11] + SCHEMA_MIGRATIONS[12:]
    with pytest.raises(MigrationError, match="ordered, contiguous"):
        preflight_schema_compatibility(tmp_path / "never-created.db", migrations=gapped)

    duplicated = SCHEMA_MIGRATIONS[:12] + (SCHEMA_MIGRATIONS[11],) + SCHEMA_MIGRATIONS[12:]
    with pytest.raises(MigrationError, match="ordered, contiguous"):
        preflight_schema_compatibility(tmp_path / "never-created.db", migrations=duplicated)


def test_checksum_generation_is_deterministic_for_migration_12() -> None:
    """Category 10: recomputing migration 12's checksum from a freshly
    rebuilt registry, or from an independently constructed migration with the
    same declared material, always yields the same value."""
    first = build_schema_migrations()[11].checksum
    second = build_schema_migrations()[11].checksum
    assert first == second == SCHEMA_MIGRATIONS[11].checksum

    original = SCHEMA_MIGRATIONS[11]
    source_independent_steps = tuple(
        SchemaMigrationStep(step.step_id, step.checksum_material, lambda _connection: None)
        for step in original.steps
    )
    source_independent = SchemaMigration(
        original.version, original.name, original.checksum_material, source_independent_steps,
    )
    assert source_independent.checksum == original.checksum
