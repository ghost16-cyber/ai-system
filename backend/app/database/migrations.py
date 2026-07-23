from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote
from uuid import uuid4

from backend.app.database.canonical_schema import CANONICAL_PROJECT_SCHEMA_V9_SQL


MigrationAction = Callable[[sqlite3.Connection], None]
MigrationBoundary = Callable[[str, int, str | None], None]


class MigrationError(RuntimeError):
    """Raised when the durable schema ledger cannot be trusted or upgraded."""


class MigrationBackupError(MigrationError):
    """Typed fail-closed error for an untrusted pre-migration backup."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


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
    backup_manifest_path: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationBackupManifest:
    manifest_schema_version: str
    migration_operation_id: str
    source_database_path: str
    source_database_identity: dict[str, int | str | None]
    source_schema_version: int
    target_schema_version: int
    source_file_size: int
    source_file_mtime_ns: int | None
    source_file_ctime_ns: int | None
    source_sha256: str
    source_logical_sha256: str
    source_wal: dict[str, int | str | None] | None
    backup_file_path: str
    backup_file_size: int
    backup_sha256: str
    backup_logical_sha256: str
    backup_created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


# Stage 3B or a later migration checkpoint must absorb all compatibility DDL into
# the reviewed registry and remove this switch plus the service-local DDL paths.
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


def _stage3b_canonical_artifact_step() -> SchemaMigrationStep:
    checksum_material = """stage3b-canonical-artifacts:v1
add project_artifacts.revision_number INTEGER
unique project_artifacts(project_run_id, artifact_type, binding_hash, revision_number) where revision_number is not null
add project_delivery_jobs.canonical_generation TEXT NOT NULL DEFAULT 'legacy' when table exists
add project_legacy_reconciliations.canonical_generation TEXT NOT NULL DEFAULT 'legacy' when table exists
"""

    def apply(connection: sqlite3.Connection) -> None:
        artifact_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(project_artifacts)")
        }
        if "revision_number" not in artifact_columns:
            connection.execute(
                "ALTER TABLE project_artifacts ADD COLUMN revision_number INTEGER"
            )
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_project_artifacts_revision
               ON project_artifacts(
                   project_run_id, artifact_type, binding_hash, revision_number
               ) WHERE revision_number IS NOT NULL"""
        )
        for table in ("project_delivery_jobs", "project_legacy_reconciliations"):
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if exists is None:
                continue
            columns = {
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if "canonical_generation" not in columns:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN canonical_generation "
                    "TEXT NOT NULL DEFAULT 'legacy'"
                )

    return SchemaMigrationStep(
        "bind_canonical_artifact_revisions",
        checksum_material,
        apply,
    )


def _stage3d_coordinator_processor_step() -> SchemaMigrationStep:
    checksum_material = """stage3d-coordinator-processor:v1
create project_coordinator_intents when absent
add heartbeat_at TEXT
add attempt_count INTEGER NOT NULL DEFAULT 0
add last_failure_classification TEXT
index coordinator claimed leases
"""

    def apply(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS project_coordinator_intents (
                coordinator_intent_id TEXT PRIMARY KEY,
                project_run_id TEXT NOT NULL,
                intent_type TEXT NOT NULL,
                status TEXT NOT NULL,
                trigger_event_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                plan_revision_id TEXT NOT NULL,
                scope_revision_id TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                expected_project_state_version INTEGER NOT NULL,
                payload_hash TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                intent_json TEXT NOT NULL,
                lease_expires_at TEXT,
                heartbeat_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_failure_classification TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(project_run_id, idempotency_key)
            )"""
        )
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(project_coordinator_intents)"
            )
        }
        additions = {
            "heartbeat_at": "TEXT",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "last_failure_classification": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE project_coordinator_intents ADD COLUMN {name} {definition}"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_coordinator_pending "
            "ON project_coordinator_intents(status, created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_coordinator_project "
            "ON project_coordinator_intents(project_run_id, created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_coordinator_claimed_lease "
            "ON project_coordinator_intents(status, lease_expires_at)"
        )

    return SchemaMigrationStep(
        "bind_coordinator_processor_leases",
        checksum_material,
        apply,
    )


_REPAIR_CYCLE_TABLE_SQL = """CREATE TABLE project_repair_cycles_v2 (
    repair_cycle_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL,
    cycle_number INTEGER NOT NULL CHECK(cycle_number >= 1),
    status TEXT NOT NULL,
    failure_artifact_id TEXT NOT NULL,
    diagnosis_artifact_id TEXT,
    repair_preview_artifact_id TEXT,
    schema_version TEXT NOT NULL,
    cycle_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(project_run_id, cycle_number),
    UNIQUE(project_run_id, failure_artifact_id),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
)"""


def _stage3h_compatibility_retirement_step() -> SchemaMigrationStep:
    checksum_material = """stage3h-compatibility-retirement:v1
create project_compatibility_records read-only classification table
create project_compatibility_state and record removal version 3h
tag existing delivery records from deterministic canonical_generation
fail when a legacy project has an active execution attempt
"""

    def apply(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS project_compatibility_records (
                record_id TEXT PRIMARY KEY,
                project_run_id TEXT,
                source_table TEXT NOT NULL,
                source_id TEXT NOT NULL,
                generation TEXT NOT NULL CHECK(generation IN ('legacy', 'canonical')),
                read_only INTEGER NOT NULL CHECK(read_only IN (0, 1)),
                tagged_at TEXT NOT NULL,
                UNIQUE(source_table, source_id)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS project_compatibility_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )"""
        )
        now = datetime.now(timezone.utc).isoformat()
        delivery_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_delivery_jobs'"
        ).fetchone()
        if delivery_exists is not None:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(project_delivery_jobs)")}
            if {"delivery_job_id", "canonical_generation"}.issubset(columns):
                connection.execute(
                    """INSERT OR IGNORE INTO project_compatibility_records (
                           record_id, project_run_id, source_table, source_id,
                           generation, read_only, tagged_at
                       )
                       SELECT 'compat-delivery-' || delivery_job_id, delivery_job_id,
                              'project_delivery_jobs', delivery_job_id,
                              CASE WHEN canonical_generation = 'canonical' THEN 'canonical' ELSE 'legacy' END,
                              CASE WHEN canonical_generation = 'canonical' THEN 0 ELSE 1 END, ?
                       FROM project_delivery_jobs""",
                    (now,),
                )
        connection.execute(
            "INSERT OR REPLACE INTO project_compatibility_state (key, value, recorded_at) VALUES (?, ?, ?)",
            ("compatibility_removal_version", "stage3h-v1", now),
        )
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if {"project_runs", "project_execution_attempts"}.issubset(tables):
            unsupported = connection.execute(
                """SELECT a.execution_attempt_id
                   FROM project_execution_attempts a
                   JOIN project_runs r ON r.project_run_id = a.project_run_id
                   WHERE a.status IN ('pending', 'active', 'cancelling')
                     AND json_extract(r.run_json, '$.canonical_generation') = 'legacy'
                   LIMIT 1"""
            ).fetchone()
            if unsupported is not None:
                raise MigrationError(
                    "A legacy active project attempt must be reconciled before Stage 3H compatibility retirement."
                )

    return SchemaMigrationStep(
        "tag_historical_records_read_only",
        checksum_material,
        apply,
    )


def _canonical_schema_ownership_step() -> SchemaMigrationStep:
    checksum_material = "canonical-schema-ownership:v1\n" + CANONICAL_PROJECT_SCHEMA_V9_SQL

    def apply(connection: sqlite3.Connection) -> None:
        # Do not use executescript here: sqlite3 commits before executescript,
        # which would violate the migration registry's atomic crash boundary.
        # These reviewed statements contain no triggers or embedded semicolons.
        coordinator_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'project_coordinator_intents'"
        ).fetchone()
        if coordinator_exists is not None:
            coordinator_columns = {
                str(row[1]) for row in connection.execute(
                    "PRAGMA table_info(project_coordinator_intents)"
                )
            }
            for name, definition in {
                "lease_expires_at": "TEXT",
                "heartbeat_at": "TEXT",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "last_failure_classification": "TEXT",
                "completed_at": "TEXT",
            }.items():
                if name not in coordinator_columns:
                    connection.execute(
                        f"ALTER TABLE project_coordinator_intents ADD COLUMN {name} {definition}"
                    )
        dispatch_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(project_execution_dispatches)")
        }
        if dispatch_columns and "failure_classification" not in dispatch_columns:
            connection.execute(
                "ALTER TABLE project_execution_dispatches "
                "ADD COLUMN failure_classification TEXT"
            )
        worker_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'project_worker_requests'"
        ).fetchone()
        if worker_exists is not None:
            worker_columns = {
                str(row[1]) for row in connection.execute(
                    "PRAGMA table_info(project_worker_requests)"
                )
            }
            for name, definition in {
                "lease_owner": "TEXT",
                "lease_token_hash": "TEXT",
                "lease_expires_at": "TEXT",
                "heartbeat_at": "TEXT",
                "cancellation_requested_at": "TEXT",
                "failure_classification": "TEXT",
                "finished_at": "TEXT",
                "canonical_reconciled_at": "TEXT",
            }.items():
                if name not in worker_columns:
                    connection.execute(
                        f"ALTER TABLE project_worker_requests ADD COLUMN {name} {definition}"
                    )
        for statement in CANONICAL_PROJECT_SCHEMA_V9_SQL.split(";"):
            if statement.strip():
                connection.execute(statement)

    return SchemaMigrationStep(
        "own_all_canonical_project_tables",
        checksum_material,
        apply,
    )


_STAGE7A_SCHEMA_SQL = """
CREATE TABLE project_action_replays (
    project_run_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    action_type TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    terminal_status TEXT NOT NULL CHECK(terminal_status IN ('completed', 'failed')),
    state_version_before INTEGER NOT NULL,
    state_version_after INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    result_schema_version TEXT NOT NULL,
    replay_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_run_id, idempotency_key),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);
CREATE INDEX idx_project_action_replays_event ON project_action_replays(event_id);
CREATE TABLE project_manual_evidence (
    evidence_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL,
    criterion_id TEXT NOT NULL,
    execution_attempt_id TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('submitted', 'passed', 'failed', 'stale', 'invalidated')),
    idempotency_key TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_run_id, idempotency_key),
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);
CREATE INDEX idx_project_manual_evidence_criterion ON project_manual_evidence(project_run_id, criterion_id, created_at);
CREATE TABLE local_ai_capability_snapshots (
    snapshot_id TEXT PRIMARY KEY, report_hash TEXT NOT NULL, report_json TEXT NOT NULL, generated_at TEXT NOT NULL
);
CREATE INDEX idx_local_ai_capability_time ON local_ai_capability_snapshots(generated_at);
CREATE TABLE local_ai_providers (
    provider_id TEXT PRIMARY KEY, config_version INTEGER NOT NULL CHECK(config_version >= 1), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), profile_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE local_ai_models (
    model_profile_id TEXT PRIMARY KEY, config_version INTEGER NOT NULL CHECK(config_version >= 1), provider_id TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), profile_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES local_ai_providers(provider_id)
);
CREATE INDEX idx_local_ai_models_provider ON local_ai_models(provider_id, enabled);
CREATE TABLE local_ai_role_mappings (
    role_id TEXT PRIMARY KEY, model_profile_id TEXT NOT NULL, config_version INTEGER NOT NULL CHECK(config_version >= 1), mapping_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    FOREIGN KEY(model_profile_id) REFERENCES local_ai_models(model_profile_id)
);
CREATE TABLE local_ai_scheduler_jobs (
    job_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER NOT NULL, job_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX idx_local_ai_scheduler_queue ON local_ai_scheduler_jobs(status, priority, created_at);
CREATE TABLE local_ai_execution_provenance (
    execution_id TEXT PRIMARY KEY, project_run_id TEXT, provider_id TEXT NOT NULL, model_profile_id TEXT NOT NULL, status TEXT NOT NULL, provenance_json TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT,
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
);
CREATE INDEX idx_local_ai_provenance_project ON local_ai_execution_provenance(project_run_id, created_at);
CREATE TABLE local_ai_config_idempotency (
    idempotency_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE local_ai_audit_events (
    event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, aggregate_id TEXT NOT NULL, event_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX idx_local_ai_audit_aggregate ON local_ai_audit_events(aggregate_id, created_at);
CREATE TABLE local_ai_rag_source_manifests (
    source_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled = 0), manifest_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE local_ai_evaluation_definitions (
    evaluation_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled = 0), definition_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE local_ai_training_definitions (
    training_definition_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled = 0), definition_json TEXT NOT NULL, created_at TEXT NOT NULL
)
"""


def _stage7a_schema_step() -> SchemaMigrationStep:
    def apply(connection: sqlite3.Connection) -> None:
        for statement in _STAGE7A_SCHEMA_SQL.split(";"):
            if statement.strip():
                adoptable = statement.replace(
                    "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1
                ).replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
                connection.execute(adoptable)

    return SchemaMigrationStep(
        "create_replay_manual_evidence_and_local_ai_tables",
        "stage7a-schema:v1\n" + _STAGE7A_SCHEMA_SQL,
        apply,
    )


_PHASE4A_SCHEMA_SQL = """
CREATE TABLE project_manual_evidence_invalidations (
    invalidation_id TEXT PRIMARY KEY,
    project_run_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL UNIQUE,
    criterion_id TEXT NOT NULL,
    criterion_hash TEXT NOT NULL,
    cause TEXT NOT NULL,
    superseding_plan_revision_id TEXT,
    superseding_scope_revision_id TEXT,
    superseding_manifest_hash TEXT,
    superseding_execution_attempt_id TEXT,
    superseding_artifact_id TEXT,
    invalidation_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
    FOREIGN KEY(evidence_id) REFERENCES project_manual_evidence(evidence_id)
);
CREATE INDEX idx_project_manual_evidence_invalidations_project
    ON project_manual_evidence_invalidations(project_run_id, criterion_id, created_at)
"""


def _phase4a_manual_invalidation_and_replay_step() -> SchemaMigrationStep:
    checksum_material = (
        "phase4a-manual-invalidation-and-replay:v1\n"
        + _PHASE4A_SCHEMA_SQL
        + "\nbackfill-project-action-replays-and-retire-project-idempotency:v1"
    )

    def apply(connection: sqlite3.Connection) -> None:
        for statement in _PHASE4A_SCHEMA_SQL.split(";"):
            if statement.strip():
                adoptable = statement.replace(
                    "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1
                ).replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)
                connection.execute(adoptable)
        legacy_invalidations = connection.execute(
            "SELECT evidence_id, project_run_id, criterion_id, evidence_hash, "
            "evidence_json, updated_at FROM project_manual_evidence "
            "WHERE status IN ('stale', 'invalidated') ORDER BY evidence_id"
        ).fetchall()
        for row in legacy_invalidations:
            try:
                evidence = json.loads(str(row["evidence_json"]))
                if not isinstance(evidence, dict):
                    raise TypeError("manual evidence is not an object")
                criterion_hash = str(evidence["criterion_hash"])
                cause = str(
                    evidence.get("invalidation_reason")
                    or "legacy_manual_evidence_invalidation"
                )
                created_at = str(evidence.get("invalidated_at") or row["updated_at"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise MigrationError(
                    "Legacy invalidated manual evidence cannot be migrated immutably."
                ) from exc
            identity = json.dumps(
                {
                    "project_run_id": row["project_run_id"],
                    "evidence_id": row["evidence_id"],
                    "criterion_id": row["criterion_id"],
                    "criterion_hash": criterion_hash,
                    "cause": cause,
                    "legacy_backfill": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            invalidation_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            invalidation = {
                "schema_version": "astra.project-control.manual-evidence-invalidation.v1",
                "invalidation_id": invalidation_id,
                "project_run_id": str(row["project_run_id"]),
                "evidence_id": str(row["evidence_id"]),
                "evidence_hash": str(row["evidence_hash"]),
                "criterion_id": str(row["criterion_id"]),
                "criterion_hash": criterion_hash,
                "cause": cause,
                "superseding_plan_revision_id": None,
                "superseding_scope_revision_id": None,
                "superseding_manifest_hash": None,
                "superseding_execution_attempt_id": None,
                "superseding_artifact_id": None,
                "created_at": created_at,
            }
            connection.execute(
                "INSERT INTO project_manual_evidence_invalidations "
                "(invalidation_id, project_run_id, evidence_id, criterion_id, "
                "criterion_hash, cause, invalidation_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invalidation_id, row["project_run_id"], row["evidence_id"],
                    row["criterion_id"], criterion_hash, cause,
                    json.dumps(invalidation, sort_keys=True, separators=(",", ":")),
                    created_at,
                ),
            )
        rows = connection.execute(
            "SELECT i.project_run_id, i.idempotency_key, i.command_type, "
            "i.request_hash, i.result_json, i.created_at, "
            "r.project_run_id AS replay_project_run_id, "
            "r.action_type AS replay_action_type, "
            "r.request_fingerprint AS replay_request_fingerprint, "
            "r.event_id AS replay_event_id, r.result_schema_version, r.replay_json "
            "FROM project_idempotency i "
            "LEFT JOIN project_action_replays r "
            "ON r.project_run_id = i.project_run_id "
            "AND r.idempotency_key = i.idempotency_key "
            "ORDER BY i.project_run_id, i.idempotency_key"
        ).fetchall()
        for row in rows:
            try:
                result = json.loads(str(row["result_json"]))
                if not isinstance(result, dict):
                    raise TypeError("transition result is not an object")
                if result.get("schema_version") != "astra.project-control.transition-result.v1":
                    raise ValueError("unsupported transition result schema")
                event_id = str(result["event_id"])
                before = int(result["previous_state_version"])
                after = int(result["state_version"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise MigrationError(
                    "A legacy idempotency result cannot be migrated to the canonical replay ledger."
                ) from exc
            event = connection.execute(
                "SELECT event_type, request_id, previous_state_version, "
                "resulting_state_version FROM project_events "
                "WHERE event_id = ? AND project_run_id = ?",
                (event_id, row["project_run_id"]),
            ).fetchone()
            if (
                event is None
                or str(event["event_type"]) != str(row["command_type"])
                or str(event["request_id"]) != str(row["idempotency_key"])
                or int(event["previous_state_version"]) != before
                or int(event["resulting_state_version"]) != after
            ):
                raise MigrationError(
                    "A legacy idempotency result has no matching canonical project event."
                )
            if row["replay_project_run_id"] is not None:
                try:
                    existing_replay = json.loads(str(row["replay_json"]))
                    replay_result = existing_replay["result"]
                except (KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise MigrationError(
                        "An existing canonical action replay is malformed."
                    ) from exc
                if (
                    str(row["replay_action_type"]) != str(row["command_type"])
                    or str(row["replay_request_fingerprint"]) != str(row["request_hash"])
                    or str(row["replay_event_id"]) != event_id
                    or str(row["result_schema_version"]) != str(result["schema_version"])
                    or json.dumps(replay_result, sort_keys=True, separators=(",", ":"))
                    != json.dumps(result, sort_keys=True, separators=(",", ":"))
                ):
                    raise MigrationError(
                        "The legacy idempotency result conflicts with the canonical replay ledger."
                    )
                continue
            replay_payload = {
                "request_fingerprint": str(row["request_hash"]),
                "result": result,
                "legacy_idempotency_backfill": True,
            }
            connection.execute(
                "INSERT INTO project_action_replays "
                "(project_run_id, idempotency_key, action_type, request_fingerprint, "
                "terminal_status, state_version_before, state_version_after, event_id, "
                "result_schema_version, replay_json, created_at) "
                "VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)",
                (
                    row["project_run_id"], row["idempotency_key"], row["command_type"],
                    row["request_hash"], before, after, event_id,
                    result["schema_version"],
                    json.dumps(replay_payload, sort_keys=True, separators=(",", ":")),
                    row["created_at"],
                ),
            )
        legacy_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'project_idempotency_legacy'"
        ).fetchone()
        if legacy_exists is None:
            connection.execute(
                "ALTER TABLE project_idempotency RENAME TO project_idempotency_legacy"
            )
        else:
            connection.execute(
                "INSERT OR IGNORE INTO project_idempotency_legacy "
                "(project_run_id, idempotency_key, command_type, request_hash, "
                "result_json, created_at) "
                "SELECT project_run_id, idempotency_key, command_type, request_hash, "
                "result_json, created_at FROM project_idempotency"
            )
            connection.execute("DROP TABLE project_idempotency")

    return SchemaMigrationStep(
        "manual_evidence_invalidations_and_canonical_action_replay",
        checksum_material,
        apply,
    )


_PHASE5A_GENERATION_TABLE_SQL = """
CREATE TABLE local_ai_generation_invocations (
    generation_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_fingerprint TEXT NOT NULL,
    purpose TEXT NOT NULL,
    provider_identity TEXT NOT NULL,
    endpoint_identity TEXT NOT NULL,
    exact_model_tag TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    expected_schema_identity TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('started', 'completed', 'failed', 'timed_out', 'cancelled')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
    response_hash TEXT,
    failure_classification TEXT,
    result_json TEXT,
    diagnostic_json TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""
_PHASE5A_GENERATION_REQUEST_INDEX_SQL = """
CREATE INDEX idx_local_ai_generation_request
    ON local_ai_generation_invocations(request_id, started_at)
"""
_PHASE5A_GENERATION_STATUS_INDEX_SQL = """
CREATE INDEX idx_local_ai_generation_status
    ON local_ai_generation_invocations(status, started_at)
"""
_PHASE5A_GENERATION_IMMUTABILITY_TRIGGER_SQL = """
CREATE TRIGGER local_ai_generation_terminal_immutable
BEFORE UPDATE ON local_ai_generation_invocations
WHEN OLD.status != 'started'
  OR NEW.status NOT IN ('completed', 'failed', 'timed_out', 'cancelled')
  OR NEW.generation_id IS NOT OLD.generation_id
  OR NEW.request_id IS NOT OLD.request_id
  OR NEW.idempotency_key IS NOT OLD.idempotency_key
  OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
  OR NEW.purpose IS NOT OLD.purpose
  OR NEW.provider_identity IS NOT OLD.provider_identity
  OR NEW.endpoint_identity IS NOT OLD.endpoint_identity
  OR NEW.exact_model_tag IS NOT OLD.exact_model_tag
  OR NEW.input_hash IS NOT OLD.input_hash
  OR NEW.context_hash IS NOT OLD.context_hash
  OR NEW.expected_schema_identity IS NOT OLD.expected_schema_identity
  OR NEW.started_at IS NOT OLD.started_at
  OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'local_ai_generation_record_immutable');
END
"""
def _phase5a_generation_gateway_steps() -> tuple[SchemaMigrationStep, ...]:
    return (
        _sql_step(
            "create_local_ai_generation_invocations",
            _PHASE5A_GENERATION_TABLE_SQL,
        ),
        _sql_step(
            "index_local_ai_generation_request",
            _PHASE5A_GENERATION_REQUEST_INDEX_SQL,
        ),
        _sql_step(
            "index_local_ai_generation_status",
            _PHASE5A_GENERATION_STATUS_INDEX_SQL,
        ),
        _sql_step(
            "protect_local_ai_generation_terminal_records",
            _PHASE5A_GENERATION_IMMUTABILITY_TRIGGER_SQL,
        ),
    )


_LOCAL_AI_GENERATION_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER local_ai_generation_records_no_delete
BEFORE DELETE ON local_ai_generation_invocations
BEGIN
    SELECT RAISE(ABORT, 'local_ai_generation_record_immutable');
END
"""


def _local_ai_generation_delete_protection_steps() -> tuple[SchemaMigrationStep, ...]:
    return (
        _sql_step(
            "protect_local_ai_generation_records_from_deletion",
            _LOCAL_AI_GENERATION_NO_DELETE_TRIGGER_SQL,
        ),
    )


_PHASE5B_PROPOSAL_TABLE_SQL = """
CREATE TABLE project_synthesis_proposals (
    proposal_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    proposal_type TEXT NOT NULL CHECK(proposal_type IN ('clarification', 'implementation_plan', 'patch', 'command', 'diagnosis')),
    project_run_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    generation_request_id TEXT NOT NULL,
    generation_request_fingerprint TEXT NOT NULL,
    proposal_fingerprint TEXT NOT NULL UNIQUE,
    evidence_envelope_id TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    repository_manifest_identity TEXT NOT NULL,
    scope_revision_id TEXT NOT NULL,
    plan_revision_id TEXT,
    semantic_validation_status TEXT NOT NULL CHECK(semantic_validation_status IN ('accepted', 'rejected')),
    initial_lifecycle_state TEXT NOT NULL CHECK(initial_lifecycle_state IN ('accepted', 'rejected')),
    proposal_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
)
"""
_PHASE5B_PROPOSAL_PROJECT_INDEX_SQL = """
CREATE INDEX idx_project_synthesis_proposals_project
ON project_synthesis_proposals(project_run_id, created_at)
"""
_PHASE5B_PROPOSAL_GENERATION_INDEX_SQL = """
CREATE INDEX idx_project_synthesis_proposals_generation
ON project_synthesis_proposals(generation_id, generation_request_id)
"""
_PHASE5B_PROPOSAL_EVENT_TABLE_SQL = """
CREATE TABLE project_synthesis_proposal_events (
    event_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 1),
    lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN ('generated', 'rejected', 'accepted', 'previewed', 'stale', 'superseded', 'invalidated')),
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(proposal_id, sequence),
    FOREIGN KEY(proposal_id) REFERENCES project_synthesis_proposals(proposal_id)
)
"""
_PHASE5B_PROPOSAL_EVENT_INDEX_SQL = """
CREATE INDEX idx_project_synthesis_proposal_events_state
ON project_synthesis_proposal_events(proposal_id, lifecycle_state, sequence)
"""
_PHASE5B_PROPOSAL_NO_UPDATE_SQL = """
CREATE TRIGGER project_synthesis_proposals_no_update
BEFORE UPDATE ON project_synthesis_proposals
BEGIN
    SELECT RAISE(ABORT, 'project_synthesis_proposal_immutable');
END
"""
_PHASE5B_PROPOSAL_NO_DELETE_SQL = """
CREATE TRIGGER project_synthesis_proposals_no_delete
BEFORE DELETE ON project_synthesis_proposals
BEGIN
    SELECT RAISE(ABORT, 'project_synthesis_proposal_immutable');
END
"""
_PHASE5B_PROPOSAL_EVENT_NO_UPDATE_SQL = """
CREATE TRIGGER project_synthesis_proposal_events_no_update
BEFORE UPDATE ON project_synthesis_proposal_events
BEGIN
    SELECT RAISE(ABORT, 'project_synthesis_proposal_event_immutable');
END
"""
_PHASE5B_PROPOSAL_EVENT_NO_DELETE_SQL = """
CREATE TRIGGER project_synthesis_proposal_events_no_delete
BEFORE DELETE ON project_synthesis_proposal_events
BEGIN
    SELECT RAISE(ABORT, 'project_synthesis_proposal_event_immutable');
END
"""


def _phase5b_canonical_synthesis_steps() -> tuple[SchemaMigrationStep, ...]:
    return (
        _sql_step("create_project_synthesis_proposals", _PHASE5B_PROPOSAL_TABLE_SQL),
        _sql_step("index_project_synthesis_proposals_project", _PHASE5B_PROPOSAL_PROJECT_INDEX_SQL),
        _sql_step("index_project_synthesis_proposals_generation", _PHASE5B_PROPOSAL_GENERATION_INDEX_SQL),
        _sql_step("create_project_synthesis_proposal_events", _PHASE5B_PROPOSAL_EVENT_TABLE_SQL),
        _sql_step("index_project_synthesis_proposal_events_state", _PHASE5B_PROPOSAL_EVENT_INDEX_SQL),
        _sql_step("protect_project_synthesis_proposals_from_update", _PHASE5B_PROPOSAL_NO_UPDATE_SQL),
        _sql_step("protect_project_synthesis_proposals_from_delete", _PHASE5B_PROPOSAL_NO_DELETE_SQL),
        _sql_step("protect_project_synthesis_proposal_events_from_update", _PHASE5B_PROPOSAL_EVENT_NO_UPDATE_SQL),
        _sql_step("protect_project_synthesis_proposal_events_from_delete", _PHASE5B_PROPOSAL_EVENT_NO_DELETE_SQL),
    )


_PHASE5B_RESPONSE_SCHEMA_HASH_COLUMN_SQL = """
ALTER TABLE local_ai_generation_invocations
ADD COLUMN response_schema_hash TEXT
"""
_PHASE5B_DROP_GENERATION_IMMUTABILITY_TRIGGER_SQL = """
DROP TRIGGER local_ai_generation_terminal_immutable
"""
_PHASE5B_SCHEMA_BOUND_GENERATION_IMMUTABILITY_TRIGGER_SQL = """
CREATE TRIGGER local_ai_generation_terminal_immutable
BEFORE UPDATE ON local_ai_generation_invocations
WHEN OLD.status != 'started'
  OR NEW.status NOT IN ('completed', 'failed', 'timed_out', 'cancelled')
  OR NEW.generation_id IS NOT OLD.generation_id
  OR NEW.request_id IS NOT OLD.request_id
  OR NEW.idempotency_key IS NOT OLD.idempotency_key
  OR NEW.request_fingerprint IS NOT OLD.request_fingerprint
  OR NEW.purpose IS NOT OLD.purpose
  OR NEW.provider_identity IS NOT OLD.provider_identity
  OR NEW.endpoint_identity IS NOT OLD.endpoint_identity
  OR NEW.exact_model_tag IS NOT OLD.exact_model_tag
  OR NEW.input_hash IS NOT OLD.input_hash
  OR NEW.context_hash IS NOT OLD.context_hash
  OR NEW.expected_schema_identity IS NOT OLD.expected_schema_identity
  OR NEW.response_schema_hash IS NOT OLD.response_schema_hash
  OR NEW.started_at IS NOT OLD.started_at
  OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'local_ai_generation_record_immutable');
END
"""


def _phase5b_ollama_json_schema_steps() -> tuple[SchemaMigrationStep, ...]:
    return (
        _sql_step(
            "add_local_generation_response_schema_hash",
            _PHASE5B_RESPONSE_SCHEMA_HASH_COLUMN_SQL,
        ),
        _sql_step(
            "drop_pre_schema_generation_immutability_trigger",
            _PHASE5B_DROP_GENERATION_IMMUTABILITY_TRIGGER_SQL,
        ),
        _sql_step(
            "protect_schema_bound_local_generation_records",
            _PHASE5B_SCHEMA_BOUND_GENERATION_IMMUTABILITY_TRIGGER_SQL,
        ),
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
        SchemaMigration(
            5,
            "canonical_projects_and_artifact_revisions",
            "astra-schema-migration:canonical-projects-and-artifact-revisions:v1",
            (_stage3b_canonical_artifact_step(),),
        ),
        SchemaMigration(
            6,
            "coordinator_processor_leases",
            "astra-schema-migration:coordinator-processor-leases:v1",
            (_stage3d_coordinator_processor_step(),),
        ),
        SchemaMigration(
            7,
            "canonical_one_repair_cycles",
            "astra-schema-migration:canonical-one-repair-cycles:v1",
            (
                _sql_step("create_project_repair_cycles_v2", _REPAIR_CYCLE_TABLE_SQL),
                _sql_step(
                    "index_project_repair_cycles_v2_status",
                    "CREATE INDEX idx_project_repair_cycles_v2_status "
                    "ON project_repair_cycles_v2(status, updated_at)",
                ),
            ),
        ),
        SchemaMigration(
            8,
            "compatibility_retirement",
            "astra-schema-migration:compatibility-retirement:v1",
            (_stage3h_compatibility_retirement_step(),),
        ),
        SchemaMigration(
            9,
            "canonical_schema_ownership",
            "astra-schema-migration:canonical-schema-ownership:v1",
            (_canonical_schema_ownership_step(),),
        ),
        SchemaMigration(
            10,
            "exact_replay_manual_evidence_and_local_ai",
            "astra-schema-migration:exact-replay-manual-evidence-local-ai:v1",
            (_stage7a_schema_step(),),
        ),
        SchemaMigration(
            11,
            "manual_evidence_invalidation_and_replay_authority",
            "astra-schema-migration:manual-evidence-invalidation-replay-authority:v1",
            (_phase4a_manual_invalidation_and_replay_step(),),
        ),
        SchemaMigration(
            12,
            "production_safe_local_generation_gateway",
            "astra-schema-migration:production-safe-local-generation-gateway:v1",
            _phase5a_generation_gateway_steps(),
        ),
        SchemaMigration(
            13,
            "canonical_model_synthesis_integration",
            "astra-schema-migration:canonical-model-synthesis-integration:v1",
            _phase5b_canonical_synthesis_steps(),
        ),
        SchemaMigration(
            14,
            "ollama_json_schema_constrained_generation",
            "astra-schema-migration:ollama-json-schema-constrained-generation:v1",
            _phase5b_ollama_json_schema_steps(),
        ),
        SchemaMigration(
            15,
            "local_ai_generation_delete_protection",
            "astra-schema-migration:local-ai-generation-delete-protection:v1",
            _local_ai_generation_delete_protection_steps(),
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
    preflight_version = preflight_schema_compatibility(path, migrations=registry)
    target_version = registry[-1].version
    backup_manifest: MigrationBackupManifest | None = None
    if (
        path.is_file()
        and path.stat().st_size > 0
        and preflight_version < target_version
    ):
        backup_manifest = _prepare_migration_backup(
            path,
            source_version=preflight_version,
            target_version=target_version,
            registry=registry,
            boundary=boundary,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        ledger_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        rows = (
            connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            if ledger_exists is not None
            else ()
        )
        current = _validate_applied(rows, registry)
        if backup_manifest is not None:
            _notify(boundary, "before_backup_revalidation", target_version, None)
            _validate_migration_backup(
                path,
                backup_manifest,
                expected_source_version=current,
                expected_target_version=target_version,
                registry=registry,
            )
            _notify(boundary, "after_backup_revalidation", target_version, None)
        _create_ledger(connection)
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
            target_version,
            target_version,
            tuple(applied),
            backup_manifest_path=(
                _manifest_path_for_backup(Path(backup_manifest.backup_file_path)).as_posix()
                if backup_manifest is not None
                else None
            ),
        )
    except Exception as exc:
        connection.rollback()
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(f"Schema migration failed: {exc}") from exc
    finally:
        connection.close()


_BACKUP_MANIFEST_SCHEMA_VERSION = "astra.migration-backup-manifest.v1"
_HASH_CHUNK_BYTES = 1024 * 1024


def _notify_backup_boundary(
    boundary: MigrationBoundary | None, event: str, target_version: int
) -> None:
    try:
        _notify(boundary, event, target_version, None)
    except Exception as exc:
        raise MigrationBackupError(
            "migration_backup_interrupted",
            "Migration backup preparation was interrupted before validation completed.",
        ) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise MigrationBackupError(
            "migration_backup_unreadable",
            "A required migration file could not be read.",
        ) from exc
    return digest.hexdigest()


def _sqlite_logical_sha256(path: Path) -> str:
    """Hash the logical SQLite contents to bind a normalized backup to its source."""
    digest = hashlib.sha256()
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_readonly(path)
        for statement in connection.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
    except (sqlite3.DatabaseError, UnicodeError) as exc:
        raise MigrationBackupError(
            "migration_backup_logical_hash_failed",
            "The database contents could not be fingerprinted safely.",
        ) from exc
    finally:
        if connection is not None:
            connection.close()
    return digest.hexdigest()


def _file_snapshot(path: Path) -> dict[str, int | str | None]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise MigrationBackupError(
            "migration_source_missing",
            "The migration source database is missing or inaccessible.",
        ) from exc
    return {
        "size": stat.st_size,
        "mtime_ns": getattr(stat, "st_mtime_ns", None),
        "ctime_ns": getattr(stat, "st_ctime_ns", None),
        "device": getattr(stat, "st_dev", None),
        "inode": getattr(stat, "st_ino", None),
        "sha256": _sha256_file(path),
    }


def _wal_snapshot(path: Path) -> dict[str, int | str | None] | None:
    wal_path = path.with_name(f"{path.name}-wal")
    if not wal_path.exists():
        return None
    snapshot = _file_snapshot(wal_path)
    return {
        "size": snapshot["size"],
        "mtime_ns": snapshot["mtime_ns"],
        "sha256": snapshot["sha256"],
    }


def _checkpoint_database(path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(path)
        connection.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
    except sqlite3.DatabaseError as exc:
        raise MigrationBackupError(
            "migration_backup_checkpoint_failed",
            "The source database could not be checkpointed before backup.",
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _manifest_path_for_backup(backup_path: Path) -> Path:
    return backup_path.with_name(f"{backup_path.name}.manifest.json")


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_manifest(path: Path, manifest: MigrationBackupManifest) -> None:
    temporary = path.with_name(f".{path.name}.{manifest.migration_operation_id}.tmp")
    payload = json.dumps(
        manifest.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise MigrationBackupError(
            "migration_backup_manifest_write_failed",
            "The migration backup manifest could not be persisted atomically.",
        ) from exc


def _load_backup_manifest(path: Path) -> MigrationBackupManifest:
    if not path.is_file():
        raise MigrationBackupError(
            "migration_backup_manifest_missing",
            "The migration backup manifest is missing.",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("manifest root must be an object")
        expected_fields = set(MigrationBackupManifest.__dataclass_fields__)
        if set(raw) != expected_fields:
            raise ValueError("manifest fields do not match the versioned contract")
        manifest = MigrationBackupManifest(**raw)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise MigrationBackupError(
            "migration_backup_manifest_malformed",
            "The migration backup manifest is malformed.",
        ) from exc
    if manifest.manifest_schema_version != _BACKUP_MANIFEST_SCHEMA_VERSION:
        raise MigrationBackupError(
            "migration_backup_manifest_version_unsupported",
            "The migration backup manifest version is unsupported.",
        )
    string_fields = (
        manifest.migration_operation_id,
        manifest.source_database_path,
        manifest.source_sha256,
        manifest.source_logical_sha256,
        manifest.backup_file_path,
        manifest.backup_sha256,
        manifest.backup_logical_sha256,
        manifest.backup_created_at,
    )
    if (
        any(not isinstance(value, str) or not value for value in string_fields)
        or not isinstance(manifest.source_database_identity, dict)
        or not isinstance(manifest.source_schema_version, int)
        or not isinstance(manifest.target_schema_version, int)
        or not isinstance(manifest.source_file_size, int)
        or not isinstance(manifest.backup_file_size, int)
        or manifest.source_file_size < 1
        or manifest.backup_file_size < 1
    ):
        raise MigrationBackupError(
            "migration_backup_manifest_malformed",
            "The migration backup manifest contains invalid field types or values.",
        )
    return manifest


def _validate_backup_database(
    backup_path: Path,
    *,
    expected_source_version: int,
    registry: tuple[SchemaMigration, ...],
) -> None:
    if not backup_path.is_file():
        raise MigrationBackupError(
            "migration_backup_missing", "The required migration backup is missing."
        )
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_readonly(backup_path)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            raise MigrationBackupError(
                "migration_backup_integrity_failed",
                "The migration backup failed SQLite integrity validation.",
            )
        connection.close()
        connection = None
        backup_version = preflight_schema_compatibility(backup_path, migrations=registry)
        if backup_version != expected_source_version:
            raise MigrationBackupError(
                "migration_backup_schema_mismatch",
                "The migration backup schema version does not match the source snapshot.",
            )
        validate_schema_shape(backup_path, backup_version)
    except MigrationBackupError:
        raise
    except (MigrationError, sqlite3.DatabaseError) as exc:
        raise MigrationBackupError(
            "migration_backup_validation_failed",
            "The migration backup could not be opened or schema-validated.",
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def _prepare_migration_backup(
    path: Path,
    *,
    source_version: int,
    target_version: int,
    registry: tuple[SchemaMigration, ...],
    boundary: MigrationBoundary | None,
) -> MigrationBackupManifest:
    operation_id = uuid4().hex
    resolved_source = path.resolve()
    backup_path = path.with_name(
        f"{path.name}.migration-v{source_version}-to-v{target_version}.{operation_id}.bak"
    ).resolve()
    temporary_backup = backup_path.with_name(f".{backup_path.name}.tmp")
    manifest_path = _manifest_path_for_backup(backup_path)

    _notify_backup_boundary(boundary, "before_backup", target_version)
    _checkpoint_database(path)
    source_snapshot = _file_snapshot(path)
    source_logical_sha256 = _sqlite_logical_sha256(path)
    source_wal = _wal_snapshot(path)
    source = destination = None
    try:
        source = _connect_readonly(path)
        destination = sqlite3.connect(temporary_backup, timeout=30)
        source.backup(destination)
        destination.commit()
        destination.close()
        destination = None
        source.close()
        source = None
        with temporary_backup.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary_backup, backup_path)
        _fsync_directory(path.parent)
    except Exception as exc:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        temporary_backup.unlink(missing_ok=True)
        if isinstance(exc, MigrationBackupError):
            raise
        raise MigrationBackupError(
            "migration_backup_creation_failed",
            "The SQLite-consistent migration backup could not be created.",
        ) from exc

    _notify_backup_boundary(boundary, "after_backup", target_version)
    backup_snapshot = _file_snapshot(backup_path)
    backup_logical_sha256 = _sqlite_logical_sha256(backup_path)
    if backup_logical_sha256 != source_logical_sha256:
        raise MigrationBackupError(
            "migration_backup_content_mismatch",
            "The SQLite backup does not represent the source database snapshot.",
        )
    manifest = MigrationBackupManifest(
        manifest_schema_version=_BACKUP_MANIFEST_SCHEMA_VERSION,
        migration_operation_id=operation_id,
        source_database_path=resolved_source.as_posix(),
        source_database_identity={
            "canonical_path": resolved_source.as_posix(),
            "device": source_snapshot["device"],
            "inode": source_snapshot["inode"],
        },
        source_schema_version=source_version,
        target_schema_version=target_version,
        source_file_size=int(source_snapshot["size"]),
        source_file_mtime_ns=source_snapshot["mtime_ns"],
        source_file_ctime_ns=source_snapshot["ctime_ns"],
        source_sha256=str(source_snapshot["sha256"]),
        source_logical_sha256=source_logical_sha256,
        source_wal=source_wal,
        backup_file_path=backup_path.as_posix(),
        backup_file_size=int(backup_snapshot["size"]),
        backup_sha256=str(backup_snapshot["sha256"]),
        backup_logical_sha256=backup_logical_sha256,
        backup_created_at=datetime.now(timezone.utc).isoformat(),
    )
    _validate_backup_database(
        backup_path, expected_source_version=source_version, registry=registry
    )
    _atomic_write_manifest(manifest_path, manifest)
    _notify_backup_boundary(boundary, "after_backup_manifest", target_version)
    return manifest


def _validate_migration_backup(
    source_path: Path,
    prepared_manifest: MigrationBackupManifest,
    *,
    expected_source_version: int,
    expected_target_version: int,
    registry: tuple[SchemaMigration, ...],
) -> MigrationBackupManifest:
    manifest_path = _manifest_path_for_backup(Path(prepared_manifest.backup_file_path))
    manifest = _load_backup_manifest(manifest_path)
    resolved_source = source_path.resolve().as_posix()
    if (
        manifest.source_database_path != resolved_source
        or manifest.source_database_identity.get("canonical_path") != resolved_source
    ):
        raise MigrationBackupError(
            "migration_backup_source_mismatch",
            "The migration backup belongs to another source database.",
        )
    if manifest.source_schema_version != expected_source_version:
        raise MigrationBackupError(
            "migration_backup_source_version_mismatch",
            "The migration backup source schema version is stale.",
        )
    if manifest.target_schema_version != expected_target_version:
        raise MigrationBackupError(
            "migration_backup_target_mismatch",
            "The migration backup targets a different schema version.",
        )
    backup_path = Path(manifest.backup_file_path)
    if backup_path.resolve().parent != source_path.resolve().parent:
        raise MigrationBackupError(
            "migration_backup_path_mismatch",
            "The migration backup is not adjacent to its source database.",
        )
    if not backup_path.is_file():
        raise MigrationBackupError(
            "migration_backup_missing", "The required migration backup is missing."
        )
    backup_snapshot = _file_snapshot(backup_path)
    if (
        backup_snapshot["sha256"] != manifest.backup_sha256
        or backup_snapshot["size"] != manifest.backup_file_size
    ):
        raise MigrationBackupError(
            "migration_backup_hash_mismatch",
            "The migration backup bytes do not match the manifest.",
        )
    if _sqlite_logical_sha256(backup_path) != manifest.backup_logical_sha256:
        raise MigrationBackupError(
            "migration_backup_hash_mismatch",
            "The migration backup contents do not match the manifest.",
        )
    if manifest.backup_logical_sha256 != manifest.source_logical_sha256:
        raise MigrationBackupError(
            "migration_backup_source_mismatch",
            "The migration backup belongs to another source database snapshot.",
        )
    _validate_backup_database(
        backup_path, expected_source_version=expected_source_version, registry=registry
    )
    source_snapshot = _file_snapshot(source_path)
    source_identity = manifest.source_database_identity
    if (
        source_snapshot["sha256"] != manifest.source_sha256
        or source_snapshot["size"] != manifest.source_file_size
        or source_snapshot["device"] != source_identity.get("device")
        or source_snapshot["inode"] != source_identity.get("inode")
        or _wal_snapshot(source_path) != manifest.source_wal
        or _sqlite_logical_sha256(source_path) != manifest.source_logical_sha256
    ):
        raise MigrationBackupError(
            "migration_backup_source_changed",
            "The source database changed after backup capture.",
        )
    return manifest


def initialize_stage3a_schema(database_path: str | Path) -> bool:
    """Compatibility name for the migration-only database bootstrap.

    The boolean return is retained for callers being migrated away from the old
    switch, but is always false: application tables are owned by migrations.
    """

    preflight_schema_compatibility(database_path)
    apply_schema_migrations(database_path)
    return False


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


# Declarative required shape for the canonical project schema at the current
# ledger version. Read-only shape validation fails closed when a table, column,
# or critical index is missing or altered, instead of letting a service-local
# `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE` silently repair drift without a
# new ledger entry.
# Each table declares `present`: migration-owned tables must exist at the current
# ledger version; service-owned tables (created by a service initializer that may
# run before or after this assert) are validated only when they already exist, so
# a dropped column on an existing table still fails closed without imposing a
# cross-module initialization order.
REQUIRED_SCHEMA_SHAPE: dict[int, dict[str, dict[str, object]]] = {
    8: {
        # Migration-owned tables (always present after the ledger is applied).
        "project_artifacts": {
            "present": True,
            "columns": ("artifact_id", "project_run_id", "content_hash", "binding_hash", "revision_number"),
            "indexes": ("idx_project_artifacts_revision",),
        },
        "project_coordinator_intents": {
            "present": True,
            "columns": ("coordinator_intent_id", "project_run_id", "status", "intent_json"),
        },
        "project_model_invocations": {
            "present": True,
            "columns": ("invocation_id", "project_run_id", "idempotency_key", "status"),
        },
        "project_compatibility_records": {
            "present": True,
            "columns": ("record_id", "source_id", "generation", "read_only"),
        },
        # Service-owned tables (validated only when already created).
        "project_runs": {
            "present": False,
            "columns": ("project_run_id", "lifecycle_status", "state_version", "run_json"),
        },
        "project_events": {
            "present": False,
            "columns": ("event_id", "project_run_id", "event_json"),
        },
        "project_approval_grants": {
            "present": False,
            "columns": ("approval_grant_id", "project_run_id", "approval_type", "grant_json"),
        },
        "project_execution_dispatches": {
            # `failure_classification` is the column the audit flagged as being
            # silently re-added by service-local repair.
            "present": False,
            "columns": ("execution_dispatch_id", "project_run_id", "failure_classification"),
        },
        "project_idempotency": {
            "present": False,
            "columns": ("project_run_id", "idempotency_key", "request_hash", "result_json"),
        },
    },
    9: {
        "project_runs": {
            "present": True,
            "columns": ("project_run_id", "conversation_id", "workspace_id", "lifecycle_status", "state_version", "run_json"),
            "indexes": ("idx_project_runs_conversation", "idx_project_runs_status", "idx_project_runs_workspace"),
        },
        "project_scope_revisions": {
            "present": True,
            "columns": ("scope_revision_id", "project_run_id", "revision_number", "content_hash", "revision_json"),
            "indexes": ("idx_project_scope_project",),
        },
        "project_plan_revisions_v3": {
            "present": True,
            "columns": ("plan_revision_id", "project_run_id", "scope_revision_id", "required_manifest_hash", "revision_json"),
            "indexes": ("idx_project_plan_project", "idx_project_plan_scope"),
        },
        "project_approval_grants": {
            "present": True,
            "columns": ("approval_grant_id", "project_run_id", "approval_type", "authority_hash", "grant_json"),
            "indexes": ("idx_project_approval_binding",),
        },
        "project_execution_attempts": {
            "present": True,
            "columns": ("execution_attempt_id", "project_run_id", "attempt_type", "status", "attempt_json"),
            "indexes": ("idx_project_attempt_status",),
        },
        "project_execution_dispatches": {
            "present": True,
            "columns": ("execution_dispatch_id", "project_run_id", "execution_attempt_id", "failure_classification", "dispatch_json"),
            "indexes": ("idx_project_dispatch_pending", "idx_project_dispatch_project"),
        },
        "project_events": {
            "present": True,
            "columns": ("event_id", "project_run_id", "sequence", "request_id", "event_json"),
            "indexes": ("idx_project_events_project", "idx_project_events_request"),
        },
        "project_idempotency": {
            "present": True,
            "columns": ("project_run_id", "idempotency_key", "request_hash", "result_json"),
            "indexes": ("idx_project_idempotency_request",),
        },
        "project_artifacts": {
            "present": True,
            "columns": ("artifact_id", "project_run_id", "content_hash", "binding_hash", "revision_number"),
            "indexes": ("idx_project_artifacts_project", "idx_project_artifacts_binding", "idx_project_artifacts_revision"),
        },
        "project_model_invocations": {
            "present": True,
            "columns": ("invocation_id", "project_run_id", "coordinator_intent_id", "idempotency_key", "status", "invocation_json"),
            "indexes": ("idx_project_model_invocations_project", "idx_project_model_invocations_claim", "idx_project_model_invocations_binding"),
        },
        "project_coordinator_intents": {
            "present": True,
            "columns": ("coordinator_intent_id", "project_run_id", "status", "heartbeat_at", "attempt_count", "intent_json"),
            "indexes": ("idx_coordinator_pending", "idx_coordinator_project", "idx_coordinator_claimed_lease"),
        },
        "project_worker_requests": {
            "present": True,
            "columns": ("worker_request_id", "project_run_id", "execution_attempt_id", "status", "request_json", "canonical_reconciled_at"),
            "indexes": ("idx_project_worker_claim", "idx_project_worker_project", "idx_project_worker_lease"),
        },
        "project_worker_events": {
            "present": True,
            "columns": ("event_id", "worker_request_id", "project_run_id", "sequence", "event_json"),
            "indexes": ("idx_project_worker_events_request",),
        },
        "project_file_mutation_specs": {
            "present": True,
            "columns": ("file_mutation_id", "project_run_id", "execution_attempt_id", "spec_hash", "spec_json"),
            "indexes": ("idx_file_mutation_project",),
        },
        "project_file_mutation_journals": {
            "present": True,
            "columns": ("file_mutation_id", "status", "journal_json", "failure_classification"),
            "indexes": ("idx_file_mutation_journal_status",),
        },
        "project_file_mutation_snapshots": {
            "present": True,
            "columns": ("file_mutation_id", "operation_index", "relative_path", "existed_before"),
        },
        "project_execution_cancellations": {
            "present": True,
            "columns": ("cancellation_id", "project_run_id", "execution_attempt_id", "status", "cancellation_json"),
            "indexes": ("idx_project_execution_cancellations_status",),
        },
        "project_projection_checkpoints": {
            "present": True,
            "columns": ("project_run_id", "last_event_sequence", "status", "updated_at"),
            "indexes": ("idx_project_projection_checkpoints_status",),
        },
        "project_repair_cycles_v2": {
            "present": True,
            "columns": ("repair_cycle_id", "project_run_id", "cycle_number", "status", "cycle_json"),
            "indexes": ("idx_project_repair_cycles_v2_status",),
        },
        "project_compatibility_records": {
            "present": True,
            "columns": ("record_id", "source_table", "source_id", "generation", "read_only"),
            "sql_contains": ("CHECK(generation IN ('legacy', 'canonical'))", "CHECK(read_only IN (0, 1))"),
        },
    },
}

REQUIRED_SCHEMA_SHAPE[10] = {
    **REQUIRED_SCHEMA_SHAPE[9],
    "project_action_replays": {
        "present": True,
        "columns": ("project_run_id", "idempotency_key", "request_fingerprint", "terminal_status", "replay_json"),
        "indexes": ("idx_project_action_replays_event",),
    },
    "project_manual_evidence": {
        "present": True,
        "columns": ("evidence_id", "project_run_id", "criterion_id", "execution_attempt_id", "status", "evidence_json"),
        "indexes": ("idx_project_manual_evidence_criterion",),
    },
    "local_ai_capability_snapshots": {"present": True, "columns": ("snapshot_id", "report_hash", "report_json"), "indexes": ("idx_local_ai_capability_time",)},
    "local_ai_providers": {"present": True, "columns": ("provider_id", "config_version", "enabled", "profile_json")},
    "local_ai_models": {"present": True, "columns": ("model_profile_id", "provider_id", "enabled", "profile_json"), "indexes": ("idx_local_ai_models_provider",)},
    "local_ai_role_mappings": {"present": True, "columns": ("role_id", "model_profile_id", "config_version", "mapping_json")},
    "local_ai_scheduler_jobs": {"present": True, "columns": ("job_id", "idempotency_key", "request_hash", "status", "job_json"), "indexes": ("idx_local_ai_scheduler_queue",)},
    "local_ai_execution_provenance": {"present": True, "columns": ("execution_id", "project_run_id", "provider_id", "model_profile_id", "provenance_json"), "indexes": ("idx_local_ai_provenance_project",)},
    "local_ai_config_idempotency": {"present": True, "columns": ("idempotency_key", "request_hash", "result_json")},
    "local_ai_audit_events": {"present": True, "columns": ("event_id", "event_type", "aggregate_id", "event_json"), "indexes": ("idx_local_ai_audit_aggregate",)},
    "local_ai_rag_source_manifests": {"present": True, "columns": ("source_id", "enabled", "manifest_json"), "sql_contains": ("CHECK(enabled = 0)",)},
    "local_ai_evaluation_definitions": {"present": True, "columns": ("evaluation_id", "enabled", "definition_json"), "sql_contains": ("CHECK(enabled = 0)",)},
    "local_ai_training_definitions": {"present": True, "columns": ("training_definition_id", "enabled", "definition_json"), "sql_contains": ("CHECK(enabled = 0)",)},
}

REQUIRED_SCHEMA_SHAPE[11] = {
    **{
        table: requirements
        for table, requirements in REQUIRED_SCHEMA_SHAPE[10].items()
        if table != "project_idempotency"
    },
    "project_idempotency_legacy": {
        "present": True,
        "columns": (
            "project_run_id", "idempotency_key", "command_type", "request_hash",
            "result_json", "created_at",
        ),
        "indexes": ("idx_project_idempotency_request",),
    },
    "project_manual_evidence_invalidations": {
        "present": True,
        "columns": (
            "invalidation_id", "project_run_id", "evidence_id", "criterion_id",
            "criterion_hash", "cause", "superseding_plan_revision_id",
            "superseding_scope_revision_id", "superseding_manifest_hash",
            "superseding_execution_attempt_id", "superseding_artifact_id",
            "invalidation_json", "created_at",
        ),
        "indexes": ("idx_project_manual_evidence_invalidations_project",),
        "foreign_keys": (
            ("project_run_id", "project_runs", "project_run_id"),
            ("evidence_id", "project_manual_evidence", "evidence_id"),
        ),
    },
}

REQUIRED_SCHEMA_SHAPE[12] = {
    **REQUIRED_SCHEMA_SHAPE[11],
    "local_ai_generation_invocations": {
        "present": True,
        "columns": (
            "generation_id", "request_id", "idempotency_key",
            "request_fingerprint", "purpose", "provider_identity",
            "endpoint_identity", "exact_model_tag", "input_hash",
            "context_hash", "expected_schema_identity", "status",
            "started_at", "completed_at", "duration_ms", "response_hash",
            "failure_classification", "result_json", "diagnostic_json",
            "created_at",
        ),
        "indexes": (
            "idx_local_ai_generation_request",
            "idx_local_ai_generation_status",
        ),
        "sql_contains": (
            "idempotency_key TEXT NOT NULL UNIQUE",
            "CHECK(status IN ('started', 'completed', 'failed', 'timed_out', 'cancelled'))",
        ),
    },
}

REQUIRED_SCHEMA_SHAPE[13] = {
    **REQUIRED_SCHEMA_SHAPE[12],
    "project_synthesis_proposals": {
        "present": True,
        "columns": (
            "proposal_id", "idempotency_key", "proposal_type", "project_run_id",
            "generation_id", "generation_request_id",
            "generation_request_fingerprint", "proposal_fingerprint",
            "evidence_envelope_id", "evidence_hash",
            "repository_manifest_identity", "scope_revision_id", "plan_revision_id",
            "semantic_validation_status", "initial_lifecycle_state",
            "proposal_json", "created_at",
        ),
        "indexes": (
            "idx_project_synthesis_proposals_project",
            "idx_project_synthesis_proposals_generation",
        ),
        "foreign_keys": (("project_run_id", "project_runs", "project_run_id"),),
        "sql_contains": (
            "idempotency_key TEXT NOT NULL UNIQUE",
            "proposal_fingerprint TEXT NOT NULL UNIQUE",
        ),
    },
    "project_synthesis_proposal_events": {
        "present": True,
        "columns": (
            "event_id", "proposal_id", "sequence", "lifecycle_state",
            "metadata_json", "created_at",
        ),
        "indexes": ("idx_project_synthesis_proposal_events_state",),
        "foreign_keys": (("proposal_id", "project_synthesis_proposals", "proposal_id"),),
        "sql_contains": ("UNIQUE(proposal_id, sequence)",),
    },
}

REQUIRED_SCHEMA_SHAPE[14] = {
    **REQUIRED_SCHEMA_SHAPE[13],
    "local_ai_generation_invocations": {
        **REQUIRED_SCHEMA_SHAPE[13]["local_ai_generation_invocations"],
        "columns": (
            *REQUIRED_SCHEMA_SHAPE[13]["local_ai_generation_invocations"]["columns"],
            "response_schema_hash",
        ),
    },
}

REQUIRED_SCHEMA_SHAPE[15] = {
    **REQUIRED_SCHEMA_SHAPE[14],
    "local_ai_generation_invocations": {
        **REQUIRED_SCHEMA_SHAPE[14]["local_ai_generation_invocations"],
        "triggers": (
            *REQUIRED_SCHEMA_SHAPE[14]["local_ai_generation_invocations"].get("triggers", ()),
            "local_ai_generation_records_no_delete",
        ),
    },
}


def validate_schema_shape(database_path: str | Path, current_version: int) -> None:
    """Fail closed when the live schema drifts from the required shape.

    Runs read-only after ledger validation. A required migration-owned table that
    is missing, or any required table that exists but is missing a required column
    or critical index, is rejected rather than silently repaired.
    """
    shape = REQUIRED_SCHEMA_SHAPE.get(current_version)
    if not shape:
        return
    path = Path(database_path)
    with _connect_readonly(path) as connection:
        for table, requirements in shape.items():
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if exists is None:
                if requirements.get("present"):
                    raise MigrationError(
                        f"Required table '{table}' is missing at schema version {current_version}."
                    )
                continue
            columns = {
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing_columns = tuple(
                column for column in requirements.get("columns", ()) if column not in columns
            )
            if missing_columns:
                raise MigrationError(
                    f"Table '{table}' is missing required column(s) "
                    f"{', '.join(missing_columns)} at schema version {current_version}."
                )
            index_names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?",
                    (table,),
                )
            }
            missing_indexes = tuple(
                index for index in requirements.get("indexes", ()) if index not in index_names
            )
            if missing_indexes:
                raise MigrationError(
                    f"Table '{table}' is missing required index(es) "
                    f"{', '.join(missing_indexes)} at schema version {current_version}."
                )
            foreign_keys = {
                (str(row[3]), str(row[2]), str(row[4]))
                for row in connection.execute(f"PRAGMA foreign_key_list({table})")
            }
            missing_foreign_keys = tuple(
                item for item in requirements.get("foreign_keys", ())
                if tuple(item) not in foreign_keys
            )
            if missing_foreign_keys:
                raise MigrationError(
                    f"Table '{table}' is missing required foreign key(s) at schema "
                    f"version {current_version}."
                )
            trigger_names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
                    (table,),
                )
            }
            missing_triggers = tuple(
                trigger for trigger in requirements.get("triggers", ())
                if trigger not in trigger_names
            )
            if missing_triggers:
                raise MigrationError(
                    f"Table '{table}' is missing required trigger(s) "
                    f"{', '.join(missing_triggers)} at schema version {current_version}."
                )
            table_sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            normalized_sql = " ".join(str(table_sql_row[0] or "").split())
            missing_sql = tuple(
                fragment for fragment in requirements.get("sql_contains", ())
                if " ".join(str(fragment).split()) not in normalized_sql
            )
            if missing_sql:
                raise MigrationError(
                    f"Table '{table}' is missing a required constraint at schema "
                    f"version {current_version}."
                )


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
    validate_schema_shape(path, current)
    return current
