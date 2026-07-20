from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote


MigrationAction = Callable[[sqlite3.Connection], None]
MigrationBoundary = Callable[[str, int, str | None], None]


class MigrationError(RuntimeError):
    """Raised when the durable schema ledger cannot be trusted or upgraded."""


@dataclass(frozen=True, slots=True)
class SchemaMigrationStep:
    step_id: str
    checksum_material: str
    apply_callback: MigrationAction

    def apply(self, connection: sqlite3.Connection) -> None:
        self.apply_callback(connection)


@dataclass(frozen=True, slots=True)
class SchemaMigration:
    version: int
    name: str
    checksum_material: str
    steps: tuple[SchemaMigrationStep, ...]

    @property
    def checksum(self) -> str:
        """Hash only explicit, immutable, code-review-visible migration material."""

        material = json.dumps(
            {
                "version": self.version,
                "name": self.name,
                "checksum_material": self.checksum_material,
                "steps": [
                    {
                        "step_id": step.step_id,
                        "checksum_material": step.checksum_material,
                    }
                    for step in self.steps
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def apply(
        self,
        connection: sqlite3.Connection,
        *,
        boundary: MigrationBoundary | None = None,
    ) -> None:
        for step in self.steps:
            _notify(boundary, "before_step", self.version, step.step_id)
            step.apply(connection)
            _notify(boundary, "after_step", self.version, step.step_id)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    current_version: int
    latest_version: int
    applied_versions: tuple[int, ...]
    status: str = "current"


# Stage 3B or a later migration checkpoint must absorb all compatibility DDL into
# the reviewed registry and remove this switch plus the service-local DDL paths.
STAGE3A_TEMPORARY_COMPATIBILITY_DDL_ENABLED = True


_ARTIFACT_TABLE_SQL = """CREATE TABLE project_artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    binding_hash TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    artifact_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_run_id, artifact_type, binding_hash, content_hash),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
)"""
_ARTIFACT_PROJECT_INDEX_SQL = """CREATE INDEX idx_project_artifacts_project
    ON project_artifacts(project_run_id, created_at, artifact_id)"""
_ARTIFACT_BINDING_INDEX_SQL = """CREATE INDEX idx_project_artifacts_binding
    ON project_artifacts(project_run_id, artifact_type, binding_hash)"""

_MODEL_INVOCATION_TABLE_SQL = """CREATE TABLE project_model_invocations (
    invocation_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL,
    coordinator_intent_id TEXT,
    purpose TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_profile TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    lease_owner TEXT,
    lease_token_hash TEXT,
    lease_expires_at TEXT,
    schema_version TEXT NOT NULL,
    invocation_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(project_run_id, idempotency_key),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
)"""
_MODEL_PROJECT_INDEX_SQL = """CREATE INDEX idx_project_model_invocations_project
    ON project_model_invocations(project_run_id, created_at, invocation_id)"""
_MODEL_CLAIM_INDEX_SQL = """CREATE INDEX idx_project_model_invocations_claim
    ON project_model_invocations(status, lease_expires_at, created_at)"""
_MODEL_BINDING_INDEX_SQL = """CREATE INDEX idx_project_model_invocations_binding
    ON project_model_invocations(project_run_id, purpose, evidence_hash)"""

_CANCELLATION_TABLE_SQL = """CREATE TABLE project_execution_cancellations (
    cancellation_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL,
    execution_attempt_id TEXT NOT NULL,
    worker_request_id TEXT,
    status TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    cancellation_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    acknowledged_at TEXT,
    UNIQUE(project_run_id, execution_attempt_id),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
    FOREIGN KEY(execution_attempt_id)
        REFERENCES project_execution_attempts(execution_attempt_id)
)"""
_CANCELLATION_STATUS_INDEX_SQL = """CREATE INDEX idx_project_execution_cancellations_status
    ON project_execution_cancellations(status, updated_at)"""
_PROJECTION_TABLE_SQL = """CREATE TABLE project_projection_checkpoints (
    project_run_id TEXT PRIMARY KEY,
    last_event_sequence INTEGER NOT NULL DEFAULT 0 CHECK(last_event_sequence >= 0),
    last_event_id TEXT,
    status TEXT NOT NULL,
    failure_message TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
)"""
_PROJECTION_STATUS_INDEX_SQL = """CREATE INDEX idx_project_projection_checkpoints_status
    ON project_projection_checkpoints(status, updated_at)"""


def _sql_step(step_id: str, sql: str) -> SchemaMigrationStep:
    def execute(connection: sqlite3.Connection) -> None:
        connection.execute(sql)

    return SchemaMigrationStep(step_id, sql, execute)


def _baseline_step() -> SchemaMigrationStep:
    def accept_stage2c_baseline(_connection: sqlite3.Connection) -> None:
        return None

    return SchemaMigrationStep(
        "accept_stage2c_baseline",
        "stage2c-baseline:no-data-rewrite:v1",
        accept_stage2c_baseline,
    )


def build_schema_migrations() -> tuple[SchemaMigration, ...]:
    """Rebuild the registry from explicit immutable identifiers and SQL text."""

    return (
        SchemaMigration(
            1,
            "stage_2c_baseline",
            "astra-schema-migration:stage2c-baseline:v1",
            (_baseline_step(),),
        ),
        SchemaMigration(
            2,
            "immutable_project_artifacts",
            "astra-schema-migration:immutable-project-artifacts:v1",
            (
                _sql_step("create_project_artifacts", _ARTIFACT_TABLE_SQL),
                _sql_step("index_project_artifacts_project", _ARTIFACT_PROJECT_INDEX_SQL),
                _sql_step("index_project_artifacts_binding", _ARTIFACT_BINDING_INDEX_SQL),
            ),
        ),
        SchemaMigration(
            3,
            "durable_model_invocations",
            "astra-schema-migration:durable-model-invocations:v1",
            (
                _sql_step("create_project_model_invocations", _MODEL_INVOCATION_TABLE_SQL),
                _sql_step("index_model_invocations_project", _MODEL_PROJECT_INDEX_SQL),
                _sql_step("index_model_invocations_claim", _MODEL_CLAIM_INDEX_SQL),
                _sql_step("index_model_invocations_binding", _MODEL_BINDING_INDEX_SQL),
            ),
        ),
        SchemaMigration(
            4,
            "cancellations_and_projections",
            "astra-schema-migration:cancellations-and-projections:v1",
            (
                _sql_step("create_execution_cancellations", _CANCELLATION_TABLE_SQL),
                _sql_step("index_execution_cancellations_status", _CANCELLATION_STATUS_INDEX_SQL),
                _sql_step("create_projection_checkpoints", _PROJECTION_TABLE_SQL),
                _sql_step("index_projection_checkpoints_status", _PROJECTION_STATUS_INDEX_SQL),
            ),
        ),
    )


SCHEMA_MIGRATIONS = build_schema_migrations()
LATEST_SCHEMA_VERSION = SCHEMA_MIGRATIONS[-1].version


def _notify(
    boundary: MigrationBoundary | None,
    event: str,
    version: int,
    step_id: str | None,
) -> None:
    if boundary is not None:
        boundary(event, version, step_id)


def _registry(migrations: Iterable[SchemaMigration]) -> tuple[SchemaMigration, ...]:
    ordered = tuple(migrations)
    versions = tuple(item.version for item in ordered)
    if not ordered or versions != tuple(range(1, len(ordered) + 1)):
        raise MigrationError(
            "Schema migrations must be ordered, contiguous, and start at version 1."
        )
    for migration in ordered:
        if not migration.name.strip() or not migration.checksum_material.strip():
            raise MigrationError(
                "Every schema migration requires a stable name and checksum material."
            )
        step_ids = tuple(step.step_id for step in migration.steps)
        if not step_ids or len(set(step_ids)) != len(step_ids):
            raise MigrationError(
                f"Schema migration {migration.version} requires unique explicit steps."
            )
        if any(
            not step_id.strip() or not step.checksum_material.strip()
            for step_id, step in zip(step_ids, migration.steps, strict=True)
        ):
            raise MigrationError(
                f"Schema migration {migration.version} has incomplete step material."
            )
    return ordered


def _connect(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(database_path), timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _connect_readonly(database_path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(database_path.resolve().as_posix(), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _create_ledger(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY CHECK(version >= 1),
            name TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )""")


def _validate_applied(
    rows: Iterable[sqlite3.Row], registry: tuple[SchemaMigration, ...]
) -> int:
    applied = tuple(rows)
    known = {item.version: item for item in registry}
    versions = tuple(int(row["version"]) for row in applied)
    if versions and versions != tuple(range(1, versions[-1] + 1)):
        raise MigrationError("The schema migration ledger contains a version gap.")
    for row in applied:
        version = int(row["version"])
        migration = known.get(version)
        if migration is None:
            raise MigrationError(
                f"Database schema version {version} is newer than this runtime supports."
            )
        if row["name"] != migration.name or row["checksum"] != migration.checksum:
            raise MigrationError(
                f"Schema migration {version} checksum does not match the runtime."
            )
    return versions[-1] if versions else 0


def preflight_schema_compatibility(
    database_path: str | Path,
    *,
    migrations: Iterable[SchemaMigration] | None = None,
) -> int:
    """Inspect an existing ledger read-only before any compatibility schema write."""

    registry = _registry(SCHEMA_MIGRATIONS if migrations is None else migrations)
    path = Path(database_path)
    if not path.exists():
        return 0
    try:
        with _connect_readonly(path) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            if exists is None:
                return 0
            rows = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            return _validate_applied(rows, registry)
    except MigrationError:
        raise
    except sqlite3.DatabaseError as exc:
        raise MigrationError(f"Schema compatibility inspection failed: {exc}") from exc


def apply_schema_migrations(
    database_path: str | Path,
    *,
    migrations: Iterable[SchemaMigration] | None = None,
    boundary: MigrationBoundary | None = None,
) -> MigrationResult:
    """Apply every missing migration atomically under a SQLite write lock."""

    registry = _registry(SCHEMA_MIGRATIONS if migrations is None else migrations)
    path = Path(database_path)
    preflight_schema_compatibility(path, migrations=registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _create_ledger(connection)
        rows = connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        current = _validate_applied(rows, registry)
        applied: list[int] = []
        for migration in registry[current:]:
            _notify(boundary, "before_migration", migration.version, None)
            migration.apply(connection, boundary=boundary)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            _notify(boundary, "after_migration", migration.version, None)
            applied.append(migration.version)
        connection.commit()
        return MigrationResult(
            registry[-1].version, registry[-1].version, tuple(applied)
        )
    except Exception as exc:
        connection.rollback()
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(f"Schema migration failed: {exc}") from exc
    finally:
        connection.close()


def initialize_stage3a_schema(database_path: str | Path) -> bool:
    """Validate/apply central migrations before temporary service-local DDL."""

    preflight_schema_compatibility(database_path)
    apply_schema_migrations(database_path)
    return STAGE3A_TEMPORARY_COMPATIBILITY_DDL_ENABLED


def current_schema_version(database_path: str | Path) -> int:
    path = Path(database_path)
    if not path.exists():
        return 0
    try:
        with _connect_readonly(path) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            if exists is None:
                return 0
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            return int(row["version"])
    except sqlite3.DatabaseError as exc:
        raise MigrationError(f"Schema version inspection failed: {exc}") from exc


def assert_schema_compatible(
    database_path: str | Path,
    *,
    migrations: Iterable[SchemaMigration] | None = None,
) -> int:
    """Validate the full ledger without mutating it and return its current version."""

    registry = _registry(SCHEMA_MIGRATIONS if migrations is None else migrations)
    path = Path(database_path)
    if not path.exists():
        raise MigrationError("The database has not been initialized.")
    current = preflight_schema_compatibility(path, migrations=registry)
    if current == 0:
        raise MigrationError("The schema migration ledger is missing.")
    if current != registry[-1].version:
        raise MigrationError(
            f"Database schema version {current} is not current ({registry[-1].version})."
        )
    return current
