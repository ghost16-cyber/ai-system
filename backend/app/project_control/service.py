from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import ValidationError

from backend.app.database.migrations import (
    assert_schema_compatible,
    initialize_stage3a_schema,
)
from backend.app.project_control.contracts import (
    EXECUTION_DISPATCH_VERSION,
    APPROVAL_GRANT_VERSION,
    PLAN_REVISION_VERSION,
    PROJECT_RUN_VERSION,
    SCOPE_REVISION_VERSION,
    ApprovalGrant,
    ExecutionDispatch,
    ExecutionDispatchStatus,
    ApprovalType,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionAttemptType,
    PlanRevision,
    ProjectCommand,
    ProjectCommandType,
    ProjectEvent,
    ProjectLifecycle,
    ProjectReadModel,
    ProjectRun,
    ScopeRevision,
    TransitionResult,
    canonical_json,
    content_hash,
)
from backend.app.project_control.errors import ProjectControlError, ProjectControlErrorCode
from backend.app.project_control.transitions import validate_command_source, validate_transition

if TYPE_CHECKING:
    from backend.app.project_artifacts.contracts import ProjectArtifact
    from backend.app.project_artifacts.store import ProjectArtifactStore


class ProjectControlPlane:
    """The sole durable lifecycle writer for project delivery operations."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        artifact_store: ProjectArtifactStore | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.artifact_store = artifact_store

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        compatibility_ddl_enabled = initialize_stage3a_schema(self.database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if not compatibility_ddl_enabled:
            assert_schema_compatible(self.database_path)
            return
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_runs (
                    project_run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    repository_root_fingerprint TEXT NOT NULL,
                    lifecycle_status TEXT NOT NULL,
                    state_version INTEGER NOT NULL CHECK(state_version >= 1),
                    schema_version TEXT NOT NULL,
                    run_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_runs_conversation
                    ON project_runs(conversation_id, created_at, project_run_id);
                CREATE INDEX IF NOT EXISTS idx_project_runs_status
                    ON project_runs(lifecycle_status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_project_runs_workspace
                    ON project_runs(workspace_id, updated_at);

                CREATE TABLE IF NOT EXISTS project_scope_revisions (
                    scope_revision_id TEXT PRIMARY KEY,
                    project_run_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL CHECK(revision_number >= 1),
                    content_hash TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    revision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_run_id, revision_number),
                    UNIQUE(project_run_id, content_hash),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_scope_project
                    ON project_scope_revisions(project_run_id, revision_number);

                CREATE TABLE IF NOT EXISTS project_plan_revisions_v3 (
                    plan_revision_id TEXT PRIMARY KEY,
                    project_run_id TEXT NOT NULL,
                    scope_revision_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL CHECK(revision_number >= 1),
                    content_hash TEXT NOT NULL,
                    required_manifest_hash TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    revision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_run_id, revision_number),
                    UNIQUE(project_run_id, content_hash),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
                    FOREIGN KEY(scope_revision_id) REFERENCES project_scope_revisions(scope_revision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_plan_project
                    ON project_plan_revisions_v3(project_run_id, revision_number);
                CREATE INDEX IF NOT EXISTS idx_project_plan_scope
                    ON project_plan_revisions_v3(scope_revision_id);

                CREATE TABLE IF NOT EXISTS project_approval_grants (
                    approval_grant_id TEXT PRIMARY KEY,
                    project_run_id TEXT NOT NULL,
                    approval_type TEXT NOT NULL,
                    plan_revision_id TEXT NOT NULL,
                    scope_revision_id TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    authority_hash TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    grant_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_run_id, approval_type, authority_hash),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
                    FOREIGN KEY(plan_revision_id) REFERENCES project_plan_revisions_v3(plan_revision_id),
                    FOREIGN KEY(scope_revision_id) REFERENCES project_scope_revisions(scope_revision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_approval_binding
                    ON project_approval_grants(project_run_id, plan_revision_id, scope_revision_id, manifest_hash);

                CREATE TABLE IF NOT EXISTS project_approval_invalidations (
                    invalidation_id TEXT PRIMARY KEY,
                    approval_grant_id TEXT NOT NULL UNIQUE,
                    project_run_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    superseded_by_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(approval_grant_id) REFERENCES project_approval_grants(approval_grant_id),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
                );

                CREATE TABLE IF NOT EXISTS project_execution_attempts (
                    execution_attempt_id TEXT PRIMARY KEY,
                    project_run_id TEXT NOT NULL,
                    attempt_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    plan_revision_id TEXT NOT NULL,
                    scope_revision_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 1),
                    schema_version TEXT NOT NULL,
                    attempt_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE(project_run_id, attempt_type, idempotency_key),
                    UNIQUE(project_run_id, attempt_type, attempt_number),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
                    FOREIGN KEY(plan_revision_id) REFERENCES project_plan_revisions_v3(plan_revision_id),
                    FOREIGN KEY(scope_revision_id) REFERENCES project_scope_revisions(scope_revision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_attempt_status
                    ON project_execution_attempts(project_run_id, status, started_at);
                CREATE TABLE IF NOT EXISTS project_execution_dispatches (
                    execution_dispatch_id TEXT PRIMARY KEY,
                    project_run_id TEXT NOT NULL,
                    execution_attempt_id TEXT NOT NULL UNIQUE,
                    attempt_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expected_project_state_version INTEGER NOT NULL CHECK(expected_project_state_version >= 1),
                    priority INTEGER NOT NULL,
                    enqueue_idempotency_key TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    dispatch_json TEXT NOT NULL,
                    worker_request_id TEXT,
                    created_at TEXT NOT NULL,
                    dispatched_at TEXT,
                    cancelled_at TEXT,
                    failure_classification TEXT,
                    UNIQUE(project_run_id, enqueue_idempotency_key),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
                    FOREIGN KEY(execution_attempt_id) REFERENCES project_execution_attempts(execution_attempt_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_dispatch_pending
                    ON project_execution_dispatches(status, available_at, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_project_dispatch_project
                    ON project_execution_dispatches(project_run_id, created_at);


                CREATE TABLE IF NOT EXISTS project_events (
                    event_id TEXT PRIMARY KEY,
                    project_run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence >= 1),
                    event_type TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    previous_state_version INTEGER NOT NULL,
                    resulting_state_version INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_run_id, sequence),
                    UNIQUE(project_run_id, request_id),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_events_project
                    ON project_events(project_run_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_project_events_request
                    ON project_events(request_id);

                CREATE TABLE IF NOT EXISTS project_idempotency (
                    project_run_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    command_type TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_run_id, idempotency_key),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_idempotency_request
                    ON project_idempotency(idempotency_key, command_type);

                CREATE TABLE IF NOT EXISTS project_legacy_reconciliations (
                    legacy_type TEXT NOT NULL,
                    legacy_id TEXT NOT NULL,
                    project_run_id TEXT NOT NULL UNIQUE,
                    legacy_hash TEXT NOT NULL,
                    canonical_generation TEXT NOT NULL DEFAULT 'legacy',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(legacy_type, legacy_id),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
                );
                """
            )
            dispatch_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(project_execution_dispatches)")
            }
            if "failure_classification" not in dispatch_columns:
                connection.execute(
                    "ALTER TABLE project_execution_dispatches ADD COLUMN failure_classification TEXT"
                )
        assert_schema_compatible(self.database_path)

    def execute(self, command: ProjectCommand | dict[str, Any]) -> TransitionResult:
        try:
            parsed = command if isinstance(command, ProjectCommand) else ProjectCommand.model_validate(command)
        except ValidationError as error:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "The project command did not match the supported command schema.",
            ) from error
        request_hash = content_hash(parsed.model_dump(mode="json"))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if parsed.command_type == ProjectCommandType.INITIALIZE_PROJECT:
                result = self._initialize_project(connection, parsed, request_hash)
            else:
                result = self._execute_existing(connection, parsed, request_hash)
            connection.execute("COMMIT")
            return result
        except ProjectControlError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except (sqlite3.DatabaseError, sqlite3.IntegrityError) as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ProjectControlError(
                ProjectControlErrorCode.PERSISTENCE_CONFLICT,
                "The project mutation could not be persisted atomically.",
            ) from error
        finally:
            connection.close()

    def get_project(self, project_run_id: str) -> ProjectRun:
        with self._connect() as connection:
            return self._load_project(connection, project_run_id)

    def get_read_model(self, project_run_id: str) -> ProjectReadModel:
        with self._connect() as connection:
            run = self._load_project(connection, project_run_id)
            return self._read_model(connection, run)

    def get_plan_revision(self, plan_revision_id: str) -> PlanRevision:
        with self._connect() as connection:
            return self._load_plan(connection, plan_revision_id)

    def has_idempotency_key(self, project_run_id: str, idempotency_key: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM project_idempotency WHERE project_run_id = ? AND idempotency_key = ?",
                (project_run_id, idempotency_key),
            ).fetchone() is not None

    def list_projects_for_conversation(self, conversation_id: str) -> list[ProjectReadModel]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT project_run_id FROM project_runs WHERE conversation_id = ? ORDER BY created_at, project_run_id",
                (conversation_id,),
            ).fetchall()
            return [self._read_model(connection, self._load_project(connection, row["project_run_id"])) for row in rows]

    def list_events(self, project_run_id: str) -> list[ProjectEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM project_events WHERE project_run_id = ? ORDER BY sequence",
                (project_run_id,),
            ).fetchall()
            return [self._stored_model(ProjectEvent, row["event_json"], "event") for row in rows]

    def list_approvals(self, project_run_id: str, *, active_only: bool = False) -> list[ApprovalGrant]:
        with self._connect() as connection:
            sql = "SELECT g.grant_json FROM project_approval_grants g"
            params: tuple[Any, ...]
            if active_only:
                sql += " LEFT JOIN project_approval_invalidations i ON i.approval_grant_id = g.approval_grant_id WHERE g.project_run_id = ? AND i.approval_grant_id IS NULL"
            else:
                sql += " WHERE g.project_run_id = ?"
            sql += " ORDER BY g.created_at, g.approval_grant_id"
            params = (project_run_id,)
            return [self._stored_model(ApprovalGrant, row["grant_json"], "approval") for row in connection.execute(sql, params).fetchall()]

    def list_attempts(self, project_run_id: str) -> list[ExecutionAttempt]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT attempt_json FROM project_execution_attempts WHERE project_run_id = ? ORDER BY started_at, execution_attempt_id",
                (project_run_id,),
            ).fetchall()
            return [self._stored_model(ExecutionAttempt, row["attempt_json"], "attempt") for row in rows]
    def get_execution_dispatch(self, execution_dispatch_id: str) -> ExecutionDispatch:
        with self._connect() as connection:
            return self._load_execution_dispatch(connection, execution_dispatch_id)

    def list_execution_dispatches(self, project_run_id: str) -> list[ExecutionDispatch]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT dispatch_json FROM project_execution_dispatches WHERE project_run_id = ? ORDER BY created_at, execution_dispatch_id",
                (project_run_id,),
            ).fetchall()
            return [
                self._stored_model(ExecutionDispatch, row["dispatch_json"], "execution dispatch")
                for row in rows
            ]

    def list_pending_execution_dispatches(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[ExecutionDispatch]:
        bounded_limit = max(1, min(int(limit), 500))
        current = (now or self._now()).astimezone(timezone.utc)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT dispatch_json FROM project_execution_dispatches WHERE status = 'pending' AND available_at <= ? ORDER BY priority DESC, created_at, execution_dispatch_id LIMIT ?",
                (current.isoformat(), bounded_limit),
            ).fetchall()
            return [
                self._stored_model(ExecutionDispatch, row["dispatch_json"], "execution dispatch")
                for row in rows
            ]

    def mark_execution_dispatch_dispatched(
        self,
        execution_dispatch_id: str,
        worker_request_id: str,
    ) -> ExecutionDispatch:
        worker_id = _text(worker_request_id)[:160]
        if not worker_id:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "worker_request_id is required.",
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            dispatch = self._load_execution_dispatch(connection, execution_dispatch_id)
            if dispatch.status == ExecutionDispatchStatus.DISPATCHED:
                if dispatch.worker_request_id != worker_id:
                    raise ProjectControlError(
                        ProjectControlErrorCode.PERSISTENCE_CONFLICT,
                        "The execution dispatch is already bound to another worker request.",
                    )
                connection.execute("COMMIT")
                return dispatch
            if dispatch.status != ExecutionDispatchStatus.PENDING:
                raise ProjectControlError(
                    ProjectControlErrorCode.ILLEGAL_TRANSITION,
                    "Only a pending execution dispatch can be delivered.",
                )
            now = self._now()
            updated = dispatch.model_copy(update={
                "status": ExecutionDispatchStatus.DISPATCHED,
                "worker_request_id": worker_id,
                "dispatched_at": now,
            })
            cursor = connection.execute(
                "UPDATE project_execution_dispatches SET status = ?, worker_request_id = ?, dispatch_json = ?, dispatched_at = ? WHERE execution_dispatch_id = ? AND status = 'pending'",
                (
                    updated.status.value,
                    worker_id,
                    updated.model_dump_json(),
                    now.isoformat(),
                    execution_dispatch_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ProjectControlError(
                    ProjectControlErrorCode.PERSISTENCE_CONFLICT,
                    "Another dispatcher changed the execution dispatch.",
                )
            connection.execute("COMMIT")
            return updated
        except ProjectControlError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def mark_execution_dispatch_cancelled(
        self,
        execution_dispatch_id: str,
        *,
        failure_classification: str,
    ) -> ExecutionDispatch:
        classification = _text(failure_classification)[:160] or "dispatch_cancelled"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            dispatch = self._load_execution_dispatch(connection, execution_dispatch_id)
            if dispatch.status == ExecutionDispatchStatus.CANCELLED:
                connection.execute("COMMIT")
                return dispatch
            if dispatch.status != ExecutionDispatchStatus.PENDING:
                raise ProjectControlError(
                    ProjectControlErrorCode.ILLEGAL_TRANSITION,
                    "Only a pending execution dispatch can be cancelled.",
                )
            now = self._now()
            updated = dispatch.model_copy(update={
                "status": ExecutionDispatchStatus.CANCELLED,
                "cancelled_at": now,
                "failure_classification": classification,
            })
            cursor = connection.execute(
                "UPDATE project_execution_dispatches SET status = ?, dispatch_json = ?, cancelled_at = ?, failure_classification = ? WHERE execution_dispatch_id = ? AND status = 'pending'",
                (
                    updated.status.value,
                    updated.model_dump_json(),
                    now.isoformat(),
                    classification,
                    execution_dispatch_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ProjectControlError(
                    ProjectControlErrorCode.PERSISTENCE_CONFLICT,
                    "Another dispatcher changed the execution dispatch.",
                )
            connection.execute("COMMIT")
            return updated
        except ProjectControlError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


    def reconcile_legacy_delivery(
        self,
        legacy: dict[str, Any],
        *,
        actor_id: str = "local-user",
        migrated: bool = True,
        repository_root: str | None = None,
    ) -> ProjectReadModel:
        legacy_id = str(legacy.get("delivery_job_id") or "")
        if not legacy_id:
            raise ProjectControlError(ProjectControlErrorCode.CORRUPTED_STORED_STATE, "The legacy delivery has no durable identity.")
        project_run_id = legacy_id
        try:
            return self.get_read_model(project_run_id)
        except ProjectControlError as error:
            if error.code != ProjectControlErrorCode.PROJECT_NOT_FOUND:
                raise
        with self._connect() as connection:
            row = connection.execute(
                "SELECT project_run_id, legacy_hash FROM project_legacy_reconciliations WHERE legacy_type = 'project_delivery' AND legacy_id = ?",
                (legacy_id,),
            ).fetchone()
        if row:
            return self.get_read_model(str(row["project_run_id"]))
        specification = dict(legacy.get("specification") or {})
        manifest = dict(legacy.get("project_state_manifest") or {})
        plan = dict(legacy.get("plan_revision") or legacy.get("plan") or {})
        spec_hash = str(specification.get("specification_hash") or content_hash({"legacy_id": legacy_id, "request": legacy.get("original_user_request")}))
        workspace = str(legacy.get("folder_access_id") or "legacy-unknown")
        root_fingerprint = str(legacy.get("root_fingerprint") or "legacy-unknown")
        root = str(repository_root or legacy.get("canonical_repository_root") or root_fingerprint)
        base = {
            "project_run_id": project_run_id,
            "conversation_id": str(legacy.get("conversation_id") or "legacy-unknown"),
            "workspace_id": workspace,
            "repository_root": root,
            "repository_root_fingerprint": root_fingerprint,
            "actor_id": actor_id,
        }
        self.execute(ProjectCommand(
            command_type=ProjectCommandType.INITIALIZE_PROJECT, expected_state_version=0,
            idempotency_key=f"legacy-init:{legacy_id}",
            payload={"migrated_from": legacy_id} if migrated else {}, **base,
        ))
        current = self.get_project(project_run_id)
        scope_payload = {
            "specification_hash": spec_hash,
            "task_specification_id": str(specification.get("specification_id") or f"legacy-spec-{legacy_id}"),
            "included_paths": sorted({str(path) for unit in plan.get("work_units", []) for path in unit.get("expected_files", [])}),
            "excluded_paths": list(specification.get("explicit_exclusions") or []),
            "allowed_operations": ["read", "patch_preview", "approved_patch", "approved_command", "verification"],
            "reason": (
                "Fail-closed migration of legacy project-delivery evidence."
                if migrated else "Initial scope imported from the Stage 0 delivery evidence adapter."
            ),
        }
        self.execute(self._followup(ProjectCommandType.ATTACH_SPECIFICATION, current, base, f"legacy-spec:{legacy_id}", scope_payload))
        current = self.get_project(project_run_id)
        manifest_hash = str(manifest.get("manifest_hash") or legacy.get("project_state_hash") or content_hash({"legacy_manifest": legacy_id}))
        self.execute(self._followup(ProjectCommandType.REGISTER_MANIFEST, current, base, f"legacy-manifest:{legacy_id}", {
            "manifest_hash": manifest_hash, "complete": bool(manifest.get("complete")),
            "incomplete_reasons": list(manifest.get("incomplete_reasons") or []),
        }))
        current = self.get_project(project_run_id)
        if current.manifest_complete and plan:
            self.execute(self._followup(ProjectCommandType.PROPOSE_PLAN_REVISION, current, base, f"legacy-plan:{legacy_id}", {
                "acceptance_criteria": list(specification.get("acceptance_criteria") or []),
                "work_units": list(plan.get("work_units") or []),
                "configured_limits": dict(legacy.get("limits") or {}),
                "reason": "Legacy plan imported as immutable evidence; approval intentionally discarded.",
            }))
        if not migrated:
            return self.get_read_model(project_run_id)
        current = self.get_project(project_run_id)
        self.execute(self._followup(
            ProjectCommandType.RECONCILE_LEGACY,
            current,
            base,
            f"legacy-reconcile:{legacy_id}",
            {"legacy_id": legacy_id, "legacy_hash": content_hash(legacy)},
        ))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO project_legacy_reconciliations (legacy_type, legacy_id, project_run_id, legacy_hash, created_at) VALUES ('project_delivery', ?, ?, ?, ?)",
                    (legacy_id, project_run_id, content_hash(legacy), self._now().isoformat()),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get_read_model(project_run_id)

    def _initialize_project(self, connection: sqlite3.Connection, command: ProjectCommand, request_hash: str) -> TransitionResult:
        existing = connection.execute(
            "SELECT run_json FROM project_runs WHERE project_run_id = ?", (command.project_run_id,)
        ).fetchone()
        if existing:
            replay = self._idempotent_result(connection, command, request_hash)
            if replay is not None:
                return replay
            raise ProjectControlError(ProjectControlErrorCode.PERSISTENCE_CONFLICT, "A project with this identity already exists.")
        if command.expected_state_version != 0:
            raise ProjectControlError(ProjectControlErrorCode.STALE_STATE_VERSION, "Project initialization requires state version zero.")
        now = self._now()
        canonical_generation = str(
            command.payload.get("canonical_generation") or "legacy"
        )
        if canonical_generation not in {"legacy", "canonical"}:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "Project generation must be either legacy or canonical.",
            )
        run = ProjectRun(
            project_run_id=command.project_run_id,
            conversation_id=command.conversation_id,
            workspace_id=command.workspace_id,
            repository_root=command.repository_root,
            repository_root_fingerprint=command.repository_root_fingerprint,
            actor_id=command.actor_id,
            lifecycle_status=ProjectLifecycle.SPECIFICATION_PENDING,
            state_version=1,
            pending_user_action="attach_specification",
            created_at=now,
            updated_at=now,
            migrated_from=_text(command.payload.get("migrated_from")),
            requires_reapproval=bool(command.payload.get("migrated_from")),
            canonical_generation=canonical_generation,
        )
        connection.execute(
            "INSERT INTO project_runs (project_run_id, conversation_id, workspace_id, repository_root_fingerprint, lifecycle_status, state_version, schema_version, run_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run.project_run_id, run.conversation_id, run.workspace_id, run.repository_root_fingerprint,
             run.lifecycle_status.value, run.state_version, run.schema_version, run.model_dump_json(), now.isoformat(), now.isoformat()),
        )
        return self._finish(connection, command, request_hash, None, run, ())

    def _execute_existing(self, connection: sqlite3.Connection, command: ProjectCommand, request_hash: str) -> TransitionResult:
        run = self._load_project(connection, command.project_run_id)
        self._validate_identity(run, command)
        replay = self._idempotent_result(connection, command, request_hash)
        if replay is not None:
            return replay
        if command.expected_state_version != run.state_version:
            raise ProjectControlError(
                ProjectControlErrorCode.STALE_STATE_VERSION,
                "The project changed after this action was displayed; reload the current card.",
                metadata={"expected": command.expected_state_version, "current": run.state_version},
            )
        validate_command_source(command.command_type, run.lifecycle_status)
        self._validate_revision_bindings(run, command)
        updated, created = self._apply(connection, run, command)
        validate_transition(run.lifecycle_status, updated.lifecycle_status)
        updated = updated.model_copy(update={"state_version": run.state_version + 1, "updated_at": self._now()})
        if updated.lifecycle_status in {ProjectLifecycle.CANCELLED, ProjectLifecycle.COMPLETED}:
            updated = updated.model_copy(update={
                "terminal_at": updated.terminal_at or self._now(),
                "terminal_reason": updated.terminal_reason or updated.lifecycle_status.value,
            })
        cursor = connection.execute(
            "UPDATE project_runs SET lifecycle_status = ?, state_version = ?, schema_version = ?, run_json = ?, updated_at = ? WHERE project_run_id = ? AND state_version = ?",
            (updated.lifecycle_status.value, updated.state_version, updated.schema_version, updated.model_dump_json(),
             updated.updated_at.isoformat(), updated.project_run_id, run.state_version),
        )
        if cursor.rowcount != 1:
            raise ProjectControlError(ProjectControlErrorCode.PERSISTENCE_CONFLICT, "Another project mutation won the concurrency race.")
        return self._finish(connection, command, request_hash, run, updated, created)

    def _apply(self, connection: sqlite3.Connection, run: ProjectRun, command: ProjectCommand) -> tuple[ProjectRun, tuple[str, ...]]:
        kind = command.command_type
        payload = command.payload
        created: list[str] = []
        artifact = self._verified_transition_artifact(run, command)
        if kind == ProjectCommandType.ATTACH_SPECIFICATION:
            specification_hash = self._hash(payload, "specification_hash")
            scope = self._create_scope(connection, run, command, specification_hash)
            created.append(scope.scope_revision_id)
            return self._with_artifact(run.model_copy(update={
                "task_specification_id": _required(payload, "task_specification_id"),
                "specification_hash": specification_hash,
                "current_scope_revision_id": scope.scope_revision_id,
                "lifecycle_status": ProjectLifecycle.MANIFEST_REQUIRED,
                "pending_user_action": "register_manifest",
                "blocked_reason": None,
            }), artifact), tuple(created)
        if kind == ProjectCommandType.REGISTER_MANIFEST:
            manifest_hash = self._hash(payload, "manifest_hash")
            complete = bool(payload.get("complete"))
            status = ProjectLifecycle.PLANNING if complete else ProjectLifecycle.BLOCKED
            return self._with_artifact(run.model_copy(update={
                "current_manifest_hash": manifest_hash,
                "manifest_complete": complete,
                "lifecycle_status": status,
                "pending_user_action": "propose_plan_revision" if complete else "rescan_manifest",
                "blocked_reason": None if complete else "Project manifest is incomplete.",
                "handoff_eligible": False,
            }), artifact), ()
        if kind == ProjectCommandType.PROPOSE_PLAN_REVISION:
            self._require_manifest(run)
            plan = self._create_plan(connection, run, command)
            created.append(plan.plan_revision_id)
            self._invalidate_approvals(connection, run.project_run_id, "plan_revision_superseded")
            work_state = {
                str(unit.get("work_unit_id") or unit.get("id")): {"status": "pending", "attempts": 0}
                for unit in plan.work_units if str(unit.get("work_unit_id") or unit.get("id"))
            }
            return self._with_artifact(run.model_copy(update={
                "current_plan_revision_id": plan.plan_revision_id,
                "active_approval_grant_ids": (),
                "work_unit_state": work_state,
                "verification_state": {},
                "handoff_eligible": False,
                "lifecycle_status": ProjectLifecycle.AWAITING_PLAN_APPROVAL,
                "pending_user_action": "approve_plan",
                "requires_reapproval": True,
            }), artifact), tuple(created)
        if kind == ProjectCommandType.APPROVE_PLAN:
            self._require_manifest(run)
            grant = self._create_approval(connection, run, command, ApprovalType.PLAN)
            created.append(grant.approval_grant_id)
            return self._with_grant(run, grant).model_copy(update={
                "lifecycle_status": ProjectLifecycle.READY_FOR_WORK,
                "pending_user_action": "begin_work_unit",
                "requires_reapproval": False,
            }), tuple(created)
        if kind == ProjectCommandType.BEGIN_WORK_UNIT:
            self._require_approval(connection, run, ApprovalType.PLAN)
            work_unit_id = _required(payload, "work_unit_id")
            if work_unit_id not in run.work_unit_state:
                raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "The requested work unit is not in the current plan.")
            state = _copy(run.work_unit_state)
            state[work_unit_id] = {**state[work_unit_id], "status": "in_progress", "attempts": int(state[work_unit_id].get("attempts", 0)) + 1}
            attempt = self._create_attempt(connection, run, command, ExecutionAttemptType.WORK_UNIT)
            created.append(attempt.execution_attempt_id)
            return self._with_attempt(run, attempt).model_copy(update={
                "work_unit_state": state, "lifecycle_status": ProjectLifecycle.WORK_IN_PROGRESS,
                "pending_user_action": "record_patch_preview",
            }), tuple(created)
        if kind == ProjectCommandType.RECORD_PATCH_PREVIEW:
            patch_id = _required(payload, "patch_id")
            return self._with_artifact(run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.AWAITING_PATCH_APPROVAL,
                "pending_user_action": f"approve_patch:{patch_id}",
            }), artifact), ()
        if kind == ProjectCommandType.APPROVE_PATCH:
            grant = self._create_approval(connection, run, command, ApprovalType.PATCH)
            created.append(grant.approval_grant_id)
            return self._with_grant(run, grant).model_copy(update={
                "lifecycle_status": ProjectLifecycle.WORK_IN_PROGRESS,
                "pending_user_action": "record_patch_result",
            }), tuple(created)
        if kind == ProjectCommandType.BEGIN_PATCH_APPLICATION:
            patch_id = _required(payload, "patch_id")
            self._require_authority_approval(connection, run, ApprovalType.PATCH, "patch_id", patch_id)
            attempt = self._create_attempt(connection, run, command, ExecutionAttemptType.PATCH)
            created.append(attempt.execution_attempt_id)
            return self._with_attempt(run, attempt).model_copy(update={"pending_user_action": "record_patch_result"}), tuple(created)
        if kind == ProjectCommandType.RECORD_PATCH_RESULT:
            self._require_authority_approval(connection, run, ApprovalType.PATCH, "patch_id", _required(payload, "patch_id"))
            succeeded = bool(payload.get("succeeded"))
            attempt = self._finish_or_create_attempt(connection, run, command, ExecutionAttemptType.PATCH, succeeded=succeeded)
            created.append(attempt.execution_attempt_id)
            manifest_hash = _text(payload.get("resulting_manifest_hash")) or run.current_manifest_hash
            status = ProjectLifecycle.WORK_IN_PROGRESS if succeeded else ProjectLifecycle.REPAIR_REQUIRED
            return self._with_attempt(run, attempt).model_copy(update={
                "current_manifest_hash": manifest_hash,
                "lifecycle_status": status,
                "pending_user_action": "request_verification" if succeeded else "initiate_repair",
                "verification_state": {} if manifest_hash != run.current_manifest_hash else run.verification_state,
                "handoff_eligible": False,
            }), tuple(created)
        if kind == ProjectCommandType.RECORD_COMMAND_PREVIEW:
            _required(payload, "command_id")
            return self._with_artifact(run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.AWAITING_COMMAND_APPROVAL,
                "pending_user_action": f"approve_command:{payload['command_id']}",
            }), artifact), ()
        if kind == ProjectCommandType.APPROVE_COMMAND:
            approval_type = ApprovalType(str(payload.get("approval_type") or ApprovalType.COMMAND.value))
            grant = self._create_approval(connection, run, command, approval_type)
            created.append(grant.approval_grant_id)
            if approval_type == ApprovalType.MANUAL_VERIFICATION:
                return self._with_grant(run, grant).model_copy(update={
                    "pending_user_action": "complete_work_unit",
                }), tuple(created)
            return self._with_grant(run, grant).model_copy(update={
                "lifecycle_status": ProjectLifecycle.WORK_IN_PROGRESS,
                "pending_user_action": "record_command_result" if approval_type == ApprovalType.COMMAND else "request_verification",
            }), tuple(created)
        if kind == ProjectCommandType.BEGIN_COMMAND_EXECUTION:
            command_id = _required(payload, "command_id")
            grant = self._require_authority_approval(
                connection, run, ApprovalType.COMMAND, "command_id", command_id
            )
            if payload.get("worker_dispatch") is not None:
                execution_hash = self._hash(payload, "execution_hash")
                if (
                    str(grant.authority.get("execution_hash") or "") != execution_hash
                    or str(command.authority_scope.get("execution_hash") or "") != execution_hash
                ):
                    raise ProjectControlError(
                        ProjectControlErrorCode.MISSING_APPROVAL,
                        "The command approval does not authorize this exact execution specification.",
                    )
            attempt = self._create_attempt(connection, run, command, ExecutionAttemptType.COMMAND)
            created.append(attempt.execution_attempt_id)
            return self._with_attempt(run, attempt).model_copy(update={"pending_user_action": "record_command_result"}), tuple(created)
        if kind == ProjectCommandType.RECORD_COMMAND_RESULT:
            command_id = _required(payload, "command_id")
            self._require_authority_approval(connection, run, ApprovalType.COMMAND, "command_id", command_id)
            succeeded = bool(payload.get("succeeded"))
            attempt = self._finish_or_create_attempt(connection, run, command, ExecutionAttemptType.COMMAND, succeeded=succeeded)
            created.append(attempt.execution_attempt_id)
            return self._with_attempt(run, attempt).model_copy(update={
                "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING if succeeded else ProjectLifecycle.REPAIR_REQUIRED,
                "pending_user_action": "record_verifier_result" if succeeded else "initiate_repair",
            }), tuple(created)
        if kind == ProjectCommandType.REQUEST_VERIFICATION:
            attempt = self._create_attempt(connection, run, command, ExecutionAttemptType.VERIFICATION)
            created.append(attempt.execution_attempt_id)
            return self._with_attempt(run, attempt).model_copy(update={
                "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING,
                "pending_user_action": "record_verifier_result",
            }), tuple(created)
        if kind == ProjectCommandType.RECORD_VERIFIER_RESULT:
            criterion_id = _required(payload, "criterion_id")
            self._validate_verifier_result(connection, run, payload, criterion_id)
            outcome = _required(payload, "outcome")
            verification = _copy(run.verification_state)
            verification[criterion_id] = {
                "outcome": outcome,
                "result_hash": self._hash(payload, "result_hash"),
                "plan_revision_id": run.current_plan_revision_id,
                "scope_revision_id": run.current_scope_revision_id,
                "manifest_hash": run.current_manifest_hash,
                "criterion_hash": self._hash(payload, "criterion_hash"),
            }
            succeeded = outcome == "passed"
            attempt = self._finish_or_create_attempt(connection, run, command, ExecutionAttemptType.VERIFICATION, succeeded=succeeded)
            created.append(attempt.execution_attempt_id)
            return self._with_artifact(self._with_attempt(run, attempt).model_copy(update={
                "verification_state": verification,
                "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING if succeeded or outcome == "manual_required" else ProjectLifecycle.REPAIR_REQUIRED,
                "pending_user_action": "complete_work_unit" if succeeded else ("approve_manual_verification" if outcome == "manual_required" else "initiate_repair"),
                "handoff_eligible": False,
            }), artifact), tuple(created)
        if kind == ProjectCommandType.REQUEST_CLARIFICATION:
            return run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.CLARIFICATION_REQUIRED,
                "pending_user_action": "answer_clarification",
                "blocked_reason": _text(payload.get("reason")) or "Clarification is required.",
            }), ()
        if kind == ProjectCommandType.MARK_BLOCKED:
            return run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.BLOCKED,
                "pending_user_action": _text(payload.get("pending_user_action")) or "review_block",
                "blocked_reason": _text(payload.get("reason")) or "Project work is blocked.",
                "handoff_eligible": False,
            }), ()
        if kind == ProjectCommandType.REVISE_SCOPE:
            specification_hash = _text(payload.get("specification_hash")) or run.specification_hash
            if not specification_hash:
                raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "A scope revision requires a specification binding.")
            scope = self._create_scope(connection, run, command, specification_hash)
            created.append(scope.scope_revision_id)
            self._invalidate_approvals(connection, run.project_run_id, "scope_revision_superseded")
            return self._with_artifact(run.model_copy(update={
                "specification_hash": specification_hash,
                "current_scope_revision_id": scope.scope_revision_id,
                "current_plan_revision_id": None,
                "active_approval_grant_ids": (),
                "work_unit_state": {}, "verification_state": {}, "handoff_eligible": False,
                "lifecycle_status": ProjectLifecycle.PLANNING,
                "pending_user_action": "propose_plan_revision",
                "requires_reapproval": True,
            }), artifact), tuple(created)
        if kind == ProjectCommandType.INITIATE_REPAIR:
            attempt = self._create_attempt(connection, run, command, ExecutionAttemptType.REPAIR)
            created.append(attempt.execution_attempt_id)
            return self._with_attempt(run, attempt).model_copy(update={
                "lifecycle_status": ProjectLifecycle.WORK_IN_PROGRESS,
                "pending_user_action": "record_patch_preview",
            }), tuple(created)
        if kind == ProjectCommandType.RECORD_ROLLBACK_PREVIEW:
            rollback_id = _required(payload, "rollback_id")
            return run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.ROLLBACK_PENDING,
                "pending_user_action": f"approve_rollback:{rollback_id}",
            }), ()
        if kind == ProjectCommandType.APPROVE_ROLLBACK:
            grant = self._create_approval(connection, run, command, ApprovalType.ROLLBACK)
            created.append(grant.approval_grant_id)
            return self._with_grant(run, grant).model_copy(update={
                "lifecycle_status": ProjectLifecycle.ROLLBACK_PENDING,
                "pending_user_action": "begin_rollback",
            }), tuple(created)
        if kind == ProjectCommandType.BEGIN_ROLLBACK:
            rollback_id = _required(payload, "rollback_id")
            grant = self._require_authority_approval(
                connection,
                run,
                ApprovalType.ROLLBACK,
                "rollback_id",
                rollback_id,
            )
            if payload.get("worker_dispatch") is not None:
                mutation_spec_hash = self._hash(payload, "mutation_spec_hash")
                if (
                    str(grant.authority.get("mutation_spec_hash") or "")
                    != mutation_spec_hash
                    or str(command.authority_scope.get("mutation_spec_hash") or "")
                    != mutation_spec_hash
                ):
                    raise ProjectControlError(
                        ProjectControlErrorCode.MISSING_APPROVAL,
                        "The rollback approval does not authorize this exact mutation specification.",
                    )
            attempt = self._create_attempt(
                connection,
                run,
                command,
                ExecutionAttemptType.ROLLBACK,
            )
            created.append(attempt.execution_attempt_id)
            return self._with_attempt(run, attempt).model_copy(update={
                "lifecycle_status": ProjectLifecycle.ROLLBACK_PENDING,
                "pending_user_action": "record_rollback",
            }), tuple(created)
        if kind == ProjectCommandType.RECORD_ROLLBACK:
            rollback_id = _required(payload, "rollback_id")
            self._require_authority_approval(
                connection,
                run,
                ApprovalType.ROLLBACK,
                "rollback_id",
                rollback_id,
            )
            attempt = self._finish_or_create_attempt(
                connection,
                run,
                command,
                ExecutionAttemptType.ROLLBACK,
                succeeded=bool(payload.get("succeeded")),
            )
            created.append(attempt.execution_attempt_id)
            succeeded = bool(payload.get("succeeded"))
            return self._with_attempt(run, attempt).model_copy(update={
                "current_manifest_hash": _text(payload.get("resulting_manifest_hash")) or run.current_manifest_hash,
                "verification_state": {}, "handoff_eligible": False,
                "lifecycle_status": ProjectLifecycle.READY_FOR_WORK if succeeded else ProjectLifecycle.REPAIR_REQUIRED,
                "pending_user_action": "begin_work_unit" if succeeded else "initiate_repair",
            }), tuple(created)
        if kind == ProjectCommandType.COMPLETE_WORK_UNIT:
            work_unit_id = _required(payload, "work_unit_id")
            if work_unit_id not in run.work_unit_state:
                raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "The requested work unit is not in the current plan.")
            state = _copy(run.work_unit_state)
            state[work_unit_id] = {**state[work_unit_id], "status": "completed"}
            self._finish_active_attempt(connection, run, ExecutionAttemptType.WORK_UNIT, succeeded=True)
            all_complete = bool(state) and all(item.get("status") == "completed" for item in state.values())
            return run.model_copy(update={
                "work_unit_state": state,
                "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING if all_complete else ProjectLifecycle.READY_FOR_WORK,
                "pending_user_action": "request_handoff" if all_complete else "begin_work_unit",
            }), ()
        if kind == ProjectCommandType.REQUEST_HANDOFF:
            self._validate_handoff(connection, run, payload)
            attempt = self._create_attempt(connection, run, command, ExecutionAttemptType.HANDOFF, terminal=True, succeeded=True)
            created.append(attempt.execution_attempt_id)
            return self._with_artifact(self._with_attempt(run, attempt).model_copy(update={
                "handoff_eligible": True,
                "lifecycle_status": ProjectLifecycle.HANDOFF_READY,
                "pending_user_action": "finalize_project",
            }), artifact), tuple(created)
        if kind == ProjectCommandType.FINALIZE_PROJECT:
            if run.lifecycle_status == ProjectLifecycle.HANDOFF_READY:
                return run.model_copy(update={
                    "lifecycle_status": ProjectLifecycle.HANDED_OFF,
                    "pending_user_action": "finalize_project",
                }), ()
            return run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.COMPLETED,
                "pending_user_action": None,
                "terminal_reason": "Project handoff finalized.",
            }), ()
        if kind == ProjectCommandType.CANCEL_PROJECT:
            self._cancel_active_attempts(connection, run.project_run_id)
            self._cancel_pending_dispatches(connection, run.project_run_id)
            return run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.CANCELLED,
                "pending_user_action": None,
                "handoff_eligible": False,
                "terminal_reason": _text(payload.get("reason")) or "Cancelled by the authorized actor.",
            }), ()
        if kind == ProjectCommandType.RECOVER_ATTEMPT:
            attempt_id = _required(payload, "execution_attempt_id")
            self._interrupt_attempt(connection, run, attempt_id)
            return run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.BLOCKED,
                "blocked_reason": "An active execution attempt was interrupted and requires review.",
                "pending_user_action": "review_interrupted_attempt",
            }), ()
        if kind == ProjectCommandType.RECONCILE_LEGACY:
            return run.model_copy(update={
                "requires_reapproval": True,
                "handoff_eligible": False,
            }), ()
        raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "The command has no supported control-plane handler.")

    def _finish(self, connection: sqlite3.Connection, command: ProjectCommand, request_hash: str,
                previous: ProjectRun | None, resulting: ProjectRun, created: tuple[str, ...]) -> TransitionResult:
        sequence = int(connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS value FROM project_events WHERE project_run_id = ?",
            (resulting.project_run_id,),
        ).fetchone()["value"])
        now = self._now()
        event = ProjectEvent(
            event_id=uuid4().hex, sequence=sequence, project_run_id=resulting.project_run_id,
            event_type=command.command_type.value, actor_id=command.actor_id,
            conversation_id=command.conversation_id, workspace_id=command.workspace_id,
            previous_state_version=previous.state_version if previous else 0,
            resulting_state_version=resulting.state_version,
            plan_revision_id=resulting.current_plan_revision_id,
            scope_revision_id=resulting.current_scope_revision_id,
            request_id=command.idempotency_key,
            metadata=self._event_metadata(command, created), created_at=now,
        )
        connection.execute(
            "INSERT INTO project_events (event_id, project_run_id, sequence, event_type, request_id, previous_state_version, resulting_state_version, schema_version, event_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event.event_id, event.project_run_id, event.sequence, event.event_type, event.request_id,
             event.previous_state_version, event.resulting_state_version, event.schema_version,
             event.model_dump_json(), now.isoformat()),
        )
        read_model = self._read_model(connection, resulting)
        result = TransitionResult(
            project_run_id=resulting.project_run_id, command_type=command.command_type,
            idempotency_key=command.idempotency_key,
            previous_state_version=event.previous_state_version,
            state_version=resulting.state_version, lifecycle_status=resulting.lifecycle_status,
            event_id=event.event_id, created_record_ids=created,
            read_model=read_model.model_dump(mode="json"),
        )
        connection.execute(
            "INSERT INTO project_idempotency (project_run_id, idempotency_key, command_type, request_hash, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (resulting.project_run_id, command.idempotency_key, command.command_type.value,
             request_hash, result.model_dump_json(), now.isoformat()),
        )
        return result

    def _idempotent_result(self, connection: sqlite3.Connection, command: ProjectCommand, request_hash: str) -> TransitionResult | None:
        row = connection.execute(
            "SELECT request_hash, result_json FROM project_idempotency WHERE project_run_id = ? AND idempotency_key = ?",
            (command.project_run_id, command.idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ProjectControlError(
                ProjectControlErrorCode.IDEMPOTENCY_CONFLICT,
                "This idempotency key was already used for a different project command.",
            )
        return self._stored_model(TransitionResult, row["result_json"], "transition result")

    def _load_execution_dispatch(
        self,
        connection: sqlite3.Connection,
        execution_dispatch_id: str,
    ) -> ExecutionDispatch:
        row = connection.execute(
            "SELECT schema_version, dispatch_json FROM project_execution_dispatches WHERE execution_dispatch_id = ?",
            (execution_dispatch_id,),
        ).fetchone()
        if row is None:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "Execution dispatch not found.",
            )
        if row["schema_version"] != EXECUTION_DISPATCH_VERSION:
            raise ProjectControlError(
                ProjectControlErrorCode.UNSUPPORTED_STORED_STATE,
                "The stored execution dispatch schema is unsupported.",
            )
        return self._stored_model(
            ExecutionDispatch,
            row["dispatch_json"],
            "execution dispatch",
        )

    def _load_project(self, connection: sqlite3.Connection, project_run_id: str) -> ProjectRun:
        row = connection.execute(
            "SELECT schema_version, run_json FROM project_runs WHERE project_run_id = ?", (project_run_id,)
        ).fetchone()
        if row is None:
            raise ProjectControlError(ProjectControlErrorCode.PROJECT_NOT_FOUND, "Project run not found.")
        if row["schema_version"] != PROJECT_RUN_VERSION:
            raise ProjectControlError(ProjectControlErrorCode.UNSUPPORTED_STORED_STATE, "The stored project schema is not supported.")
        return self._stored_model(ProjectRun, row["run_json"], "project run")

    def _stored_model(self, model: Any, raw: str, label: str) -> Any:
        try:
            value = json.loads(raw)
            expected_schema = model.model_fields["schema_version"].default
            if value.get("schema_version") != expected_schema:
                raise ProjectControlError(
                    ProjectControlErrorCode.UNSUPPORTED_STORED_STATE,
                    f"The stored {label} schema is not supported.",
                )
            return model.model_validate(value)
        except ProjectControlError:
            raise
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
            raise ProjectControlError(
                ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                f"The stored {label} failed integrity validation.",
            ) from error

    def _validate_identity(self, run: ProjectRun, command: ProjectCommand) -> None:
        checks = (
            (run.conversation_id, command.conversation_id, ProjectControlErrorCode.CONVERSATION_MISMATCH, "conversation"),
            (run.workspace_id, command.workspace_id, ProjectControlErrorCode.WORKSPACE_MISMATCH, "workspace"),
            (run.repository_root, command.repository_root, ProjectControlErrorCode.REPOSITORY_ROOT_MISMATCH, "repository root"),
            (run.repository_root_fingerprint, command.repository_root_fingerprint, ProjectControlErrorCode.REPOSITORY_ROOT_MISMATCH, "repository identity"),
            (run.actor_id, command.actor_id, ProjectControlErrorCode.ACTOR_MISMATCH, "actor"),
        )
        for expected, actual, code, label in checks:
            if expected != actual:
                raise ProjectControlError(code, f"The command {label} does not match the project binding.")

    def _validate_revision_bindings(self, run: ProjectRun, command: ProjectCommand) -> None:
        before_plan = {ProjectCommandType.ATTACH_SPECIFICATION, ProjectCommandType.REGISTER_MANIFEST,
                       ProjectCommandType.PROPOSE_PLAN_REVISION, ProjectCommandType.REQUEST_CLARIFICATION,
                       ProjectCommandType.MARK_BLOCKED, ProjectCommandType.REVISE_SCOPE, ProjectCommandType.CANCEL_PROJECT}
        if command.command_type not in before_plan and run.current_plan_revision_id:
            if command.plan_revision_id != run.current_plan_revision_id:
                raise ProjectControlError(ProjectControlErrorCode.PLAN_REVISION_MISMATCH, "The command targets a stale plan revision.")
        if run.current_scope_revision_id and command.command_type not in {ProjectCommandType.ATTACH_SPECIFICATION, ProjectCommandType.REVISE_SCOPE}:
            if command.scope_revision_id != run.current_scope_revision_id:
                raise ProjectControlError(ProjectControlErrorCode.SCOPE_REVISION_MISMATCH, "The command targets a stale scope revision.")
        manifest_bound = command.command_type not in {ProjectCommandType.ATTACH_SPECIFICATION, ProjectCommandType.REGISTER_MANIFEST,
                                                       ProjectCommandType.REQUEST_CLARIFICATION, ProjectCommandType.MARK_BLOCKED,
                                                       ProjectCommandType.CANCEL_PROJECT, ProjectCommandType.REVISE_SCOPE}
        if manifest_bound and run.current_manifest_hash and command.manifest_hash != run.current_manifest_hash:
            raise ProjectControlError(ProjectControlErrorCode.MANIFEST_MISMATCH, "The command targets a stale repository manifest.")

    def _create_scope(self, connection: sqlite3.Connection, run: ProjectRun, command: ProjectCommand, specification_hash: str) -> ScopeRevision:
        revision_number = int(connection.execute(
            "SELECT COALESCE(MAX(revision_number), 0) + 1 AS value FROM project_scope_revisions WHERE project_run_id = ?",
            (run.project_run_id,),
        ).fetchone()["value"])
        data = {
            "project_run_id": run.project_run_id, "specification_hash": specification_hash,
            "revision_number": revision_number,
            "included_paths": tuple(sorted(set(_strings(command.payload.get("included_paths"))))),
            "excluded_paths": tuple(sorted(set(_strings(command.payload.get("excluded_paths"))))),
            "allowed_operations": tuple(sorted(set(_strings(command.payload.get("allowed_operations"))))),
            "parent_revision_id": run.current_scope_revision_id,
            "reason": _text(command.payload.get("reason")) or "Scope attached to the task specification.",
        }
        digest = content_hash(data)
        scope = ScopeRevision(scope_revision_id=f"scope-{digest[:24]}", content_hash=digest, created_at=self._now(), **data)
        connection.execute(
            "INSERT INTO project_scope_revisions (scope_revision_id, project_run_id, revision_number, content_hash, schema_version, revision_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (scope.scope_revision_id, scope.project_run_id, scope.revision_number, scope.content_hash,
             SCOPE_REVISION_VERSION, scope.model_dump_json(), scope.created_at.isoformat()),
        )
        return scope

    def _create_plan(self, connection: sqlite3.Connection, run: ProjectRun, command: ProjectCommand) -> PlanRevision:
        if not run.specification_hash or not run.current_scope_revision_id or not run.current_manifest_hash:
            raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "A complete specification, scope and manifest are required before planning.")
        revision_number = int(connection.execute(
            "SELECT COALESCE(MAX(revision_number), 0) + 1 AS value FROM project_plan_revisions_v3 WHERE project_run_id = ?",
            (run.project_run_id,),
        ).fetchone()["value"])
        data = {
            "project_run_id": run.project_run_id, "specification_hash": run.specification_hash,
            "scope_revision_id": run.current_scope_revision_id, "workspace_id": run.workspace_id,
            "repository_root": run.repository_root, "repository_root_fingerprint": run.repository_root_fingerprint,
            "required_manifest_hash": run.current_manifest_hash,
            "acceptance_criteria": tuple(_dicts(command.payload.get("acceptance_criteria"))),
            "work_units": tuple(_dicts(command.payload.get("work_units"))),
            "configured_limits": {str(k): int(v) for k, v in dict(command.payload.get("configured_limits") or {}).items() if isinstance(v, int) and v >= 0},
            "revision_number": revision_number, "parent_revision_id": run.current_plan_revision_id,
            "supersedes_revision_id": run.current_plan_revision_id,
        }
        digest = content_hash(data)
        plan = PlanRevision(plan_revision_id=f"plan-{digest[:24]}", content_hash=digest, created_at=self._now(), **data)
        connection.execute(
            "INSERT INTO project_plan_revisions_v3 (plan_revision_id, project_run_id, scope_revision_id, revision_number, content_hash, required_manifest_hash, schema_version, revision_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (plan.plan_revision_id, plan.project_run_id, plan.scope_revision_id, plan.revision_number,
             plan.content_hash, plan.required_manifest_hash, PLAN_REVISION_VERSION,
             plan.model_dump_json(), plan.created_at.isoformat()),
        )
        return plan

    def _create_approval(self, connection: sqlite3.Connection, run: ProjectRun, command: ProjectCommand, approval_type: ApprovalType) -> ApprovalGrant:
        self._require_manifest(run)
        if not run.current_plan_revision_id or not run.current_scope_revision_id or not run.specification_hash or not run.current_manifest_hash:
            raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "The approval is missing current project bindings.")
        authority = _bounded_object(command.authority_scope or command.payload.get("authority") or {})
        if not authority:
            raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "An exact non-empty authority scope is required.")
        authority_hash = content_hash({
            "type": approval_type.value, "project": run.project_run_id,
            "plan": run.current_plan_revision_id, "scope": run.current_scope_revision_id,
            "manifest": run.current_manifest_hash, "authority": authority,
        })
        grant = ApprovalGrant(
            approval_grant_id=f"approval-{authority_hash[:24]}", project_run_id=run.project_run_id,
            approval_type=approval_type, actor_id=run.actor_id, conversation_id=run.conversation_id,
            workspace_id=run.workspace_id, repository_root=run.repository_root,
            repository_root_fingerprint=run.repository_root_fingerprint,
            plan_revision_id=run.current_plan_revision_id, scope_revision_id=run.current_scope_revision_id,
            specification_hash=run.specification_hash, manifest_hash=run.current_manifest_hash,
            expected_state_version=command.expected_state_version, authority=authority,
            authority_hash=authority_hash, created_at=self._now(),
        )
        connection.execute(
            "INSERT INTO project_approval_grants (approval_grant_id, project_run_id, approval_type, plan_revision_id, scope_revision_id, manifest_hash, authority_hash, schema_version, grant_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (grant.approval_grant_id, grant.project_run_id, grant.approval_type.value,
             grant.plan_revision_id, grant.scope_revision_id, grant.manifest_hash,
             grant.authority_hash, APPROVAL_GRANT_VERSION, grant.model_dump_json(), grant.created_at.isoformat()),
        )
        return grant

    def _create_attempt(self, connection: sqlite3.Connection, run: ProjectRun, command: ProjectCommand,
                        attempt_type: ExecutionAttemptType, *, terminal: bool = False, succeeded: bool = False) -> ExecutionAttempt:
        if not run.current_plan_revision_id or not run.current_scope_revision_id or not run.current_manifest_hash:
            raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "Execution requires current plan, scope and manifest bindings.")
        number = int(connection.execute(
            "SELECT COALESCE(MAX(attempt_number), 0) + 1 AS value FROM project_execution_attempts WHERE project_run_id = ? AND attempt_type = ?",
            (run.project_run_id, attempt_type.value),
        ).fetchone()["value"])
        now = self._now()
        attempt = ExecutionAttempt(
            execution_attempt_id=f"attempt-{content_hash([run.project_run_id, attempt_type.value, command.idempotency_key])[:24]}",
            project_run_id=run.project_run_id, attempt_type=attempt_type, actor_id=run.actor_id,
            conversation_id=run.conversation_id, workspace_id=run.workspace_id,
            repository_root_fingerprint=run.repository_root_fingerprint,
            plan_revision_id=run.current_plan_revision_id, scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash, expected_state_version=command.expected_state_version,
            authority=_bounded_object(command.authority_scope), attempt_number=number,
            idempotency_key=command.idempotency_key,
            status=ExecutionAttemptStatus.COMPLETED if terminal and succeeded else (ExecutionAttemptStatus.FAILED if terminal else ExecutionAttemptStatus.ACTIVE),
            result_reference=_bounded_object(command.payload.get("result_reference")) or None,
            failure_classification=None if succeeded else _text(command.payload.get("failure_classification")),
            resulting_manifest_hash=_text(command.payload.get("resulting_manifest_hash")),
            started_at=now, finished_at=now if terminal else None,
        )
        connection.execute(
            "INSERT INTO project_execution_attempts (execution_attempt_id, project_run_id, attempt_type, status, idempotency_key, plan_revision_id, scope_revision_id, attempt_number, schema_version, attempt_json, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (attempt.execution_attempt_id, attempt.project_run_id, attempt.attempt_type.value,
             attempt.status.value, attempt.idempotency_key, attempt.plan_revision_id,
             attempt.scope_revision_id, attempt.attempt_number, attempt.schema_version,
             attempt.model_dump_json(), attempt.started_at.isoformat(),
             attempt.finished_at.isoformat() if attempt.finished_at else None),
        )
        if not terminal and command.payload.get("worker_dispatch") is not None:
            self._create_execution_dispatch(
                connection, run, command, attempt, now,
            )

        return attempt
    def _create_execution_dispatch(
        self,
        connection: sqlite3.Connection,
        run: ProjectRun,
        command: ProjectCommand,
        attempt: ExecutionAttempt,
        now: datetime,
    ) -> ExecutionDispatch:
        raw = command.payload.get("worker_dispatch")
        if not isinstance(raw, dict):
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "worker_dispatch must be an object.",
            )
        worker_payload = _dispatch_object(
            raw.get("payload"),
            "worker_dispatch.payload",
            262_144,
        )
        limits = _dispatch_object(
            raw.get("limits") or {},
            "worker_dispatch.limits",
            16_384,
        )
        priority_value = raw.get("priority", 0)
        if isinstance(priority_value, bool) or not isinstance(priority_value, int):
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "worker_dispatch.priority must be an integer.",
            )
        if priority_value < -100 or priority_value > 100:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "worker_dispatch.priority must be between -100 and 100.",
            )
        enqueue_key = _text(raw.get("idempotency_key")) or (
            f"dispatch:{attempt.execution_attempt_id}"
        )
        dispatch = ExecutionDispatch(
            execution_dispatch_id=(
                f"dispatch-{content_hash([attempt.execution_attempt_id, enqueue_key])[:24]}"
            ),
            project_run_id=run.project_run_id,
            execution_attempt_id=attempt.execution_attempt_id,
            attempt_type=attempt.attempt_type,
            conversation_id=run.conversation_id,
            workspace_id=run.workspace_id,
            repository_root=run.repository_root,
            repository_root_fingerprint=run.repository_root_fingerprint,
            actor_id=run.actor_id,
            plan_revision_id=attempt.plan_revision_id,
            scope_revision_id=attempt.scope_revision_id,
            manifest_hash=attempt.manifest_hash,
            expected_project_state_version=run.state_version + 1,
            authority=attempt.authority,
            payload=worker_payload,
            priority=priority_value,
            limits=limits,
            enqueue_idempotency_key=enqueue_key,
            status=ExecutionDispatchStatus.PENDING,
            available_at=now,
            created_at=now,
        )
        connection.execute(
            "INSERT INTO project_execution_dispatches (execution_dispatch_id, project_run_id, execution_attempt_id, attempt_type, status, expected_project_state_version, priority, enqueue_idempotency_key, available_at, schema_version, dispatch_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch.execution_dispatch_id,
                dispatch.project_run_id,
                dispatch.execution_attempt_id,
                dispatch.attempt_type.value,
                dispatch.status.value,
                dispatch.expected_project_state_version,
                dispatch.priority,
                dispatch.enqueue_idempotency_key,
                dispatch.available_at.isoformat(),
                dispatch.schema_version,
                dispatch.model_dump_json(),
                dispatch.created_at.isoformat(),
            ),
        )
        return dispatch


    def _finish_or_create_attempt(
        self,
        connection: sqlite3.Connection,
        run: ProjectRun,
        command: ProjectCommand,
        attempt_type: ExecutionAttemptType,
        *,
        succeeded: bool,
    ) -> ExecutionAttempt:
        finished = self._finish_active_attempt(
            connection, run, attempt_type, succeeded=succeeded, command=command,
        )
        return finished or self._create_attempt(
            connection, run, command, attempt_type, terminal=True, succeeded=succeeded,
        )

    def _finish_active_attempt(
        self,
        connection: sqlite3.Connection,
        run: ProjectRun,
        attempt_type: ExecutionAttemptType,
        *,
        succeeded: bool,
        command: ProjectCommand | None = None,
    ) -> ExecutionAttempt | None:
        row = connection.execute(
            "SELECT attempt_json FROM project_execution_attempts WHERE project_run_id = ? AND attempt_type = ? AND status IN ('pending', 'active') ORDER BY attempt_number DESC LIMIT 1",
            (run.project_run_id, attempt_type.value),
        ).fetchone()
        if row is None:
            return None
        attempt = self._stored_model(ExecutionAttempt, row["attempt_json"], "attempt")
        payload = command.payload if command else {}
        finished = attempt.model_copy(update={
            "status": ExecutionAttemptStatus.COMPLETED if succeeded else ExecutionAttemptStatus.FAILED,
            "result_reference": _bounded_object(payload.get("result_reference")) or attempt.result_reference,
            "failure_classification": None if succeeded else (_text(payload.get("failure_classification")) or "execution_failed"),
            "resulting_manifest_hash": _text(payload.get("resulting_manifest_hash")) or attempt.resulting_manifest_hash,
            "finished_at": self._now(),
        })
        connection.execute(
            "UPDATE project_execution_attempts SET status = ?, attempt_json = ?, finished_at = ? WHERE execution_attempt_id = ? AND status IN ('pending', 'active')",
            (finished.status.value, finished.model_dump_json(), finished.finished_at.isoformat(), finished.execution_attempt_id),
        )
        return finished

    def _invalidate_approvals(self, connection: sqlite3.Connection, project_run_id: str, reason: str) -> None:
        rows = connection.execute(
            "SELECT g.approval_grant_id FROM project_approval_grants g LEFT JOIN project_approval_invalidations i ON i.approval_grant_id = g.approval_grant_id WHERE g.project_run_id = ? AND i.approval_grant_id IS NULL",
            (project_run_id,),
        ).fetchall()
        now = self._now().isoformat()
        for row in rows:
            connection.execute(
                "INSERT INTO project_approval_invalidations (invalidation_id, approval_grant_id, project_run_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (uuid4().hex, row["approval_grant_id"], project_run_id, reason, now),
            )

    def _require_approval(self, connection: sqlite3.Connection, run: ProjectRun, approval_type: ApprovalType) -> ApprovalGrant:
        manifest_binding = run.current_manifest_hash
        if approval_type == ApprovalType.PLAN:
            manifest_binding = self._load_plan(connection, run.current_plan_revision_id).required_manifest_hash
        row = connection.execute(
            "SELECT g.grant_json FROM project_approval_grants g LEFT JOIN project_approval_invalidations i ON i.approval_grant_id = g.approval_grant_id WHERE g.project_run_id = ? AND g.approval_type = ? AND g.plan_revision_id = ? AND g.scope_revision_id = ? AND g.manifest_hash = ? AND i.approval_grant_id IS NULL ORDER BY g.created_at DESC LIMIT 1",
            (run.project_run_id, approval_type.value, run.current_plan_revision_id, run.current_scope_revision_id, manifest_binding),
        ).fetchone()
        if row is None:
            raise ProjectControlError(ProjectControlErrorCode.MISSING_APPROVAL, f"A current {approval_type.value} approval is required.")
        grant = self._stored_model(ApprovalGrant, row["grant_json"], "approval")
        if grant.actor_id != run.actor_id or grant.conversation_id != run.conversation_id or grant.workspace_id != run.workspace_id:
            raise ProjectControlError(ProjectControlErrorCode.STALE_APPROVAL, "The approval identity binding is stale.")
        return grant

    def _require_authority_approval(self, connection: sqlite3.Connection, run: ProjectRun,
                                    approval_type: ApprovalType, field: str, value: str) -> ApprovalGrant:
        grants = connection.execute(
            "SELECT g.grant_json FROM project_approval_grants g LEFT JOIN project_approval_invalidations i ON i.approval_grant_id = g.approval_grant_id WHERE g.project_run_id = ? AND g.approval_type = ? AND g.plan_revision_id = ? AND g.scope_revision_id = ? AND g.manifest_hash = ? AND i.approval_grant_id IS NULL ORDER BY g.created_at DESC",
            (run.project_run_id, approval_type.value, run.current_plan_revision_id, run.current_scope_revision_id, run.current_manifest_hash),
        ).fetchall()
        for row in grants:
            grant = self._stored_model(ApprovalGrant, row["grant_json"], "approval")
            if str(grant.authority.get(field) or "") == value:
                return grant
        raise ProjectControlError(ProjectControlErrorCode.MISSING_APPROVAL, f"No current approval grants authority for this {approval_type.value} action.")

    def _validate_verifier_result(self, connection: sqlite3.Connection, run: ProjectRun,
                                  payload: dict[str, Any], criterion_id: str) -> None:
        self._require_manifest(run)
        bindings = (
            (_text(payload.get("plan_revision_id")), run.current_plan_revision_id, ProjectControlErrorCode.PLAN_REVISION_MISMATCH),
            (_text(payload.get("scope_revision_id")), run.current_scope_revision_id, ProjectControlErrorCode.SCOPE_REVISION_MISMATCH),
            (_text(payload.get("manifest_hash")), run.current_manifest_hash, ProjectControlErrorCode.MANIFEST_MISMATCH),
        )
        for actual, expected, code in bindings:
            if actual != expected:
                raise ProjectControlError(code, "The verifier evidence is bound to stale project state.")
        plan = self._load_plan(connection, run.current_plan_revision_id)
        criterion = next((item for item in plan.acceptance_criteria if str(item.get("criterion_id") or item.get("id")) == criterion_id), None)
        if criterion is None:
            raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "The verifier criterion is not in the current plan.")
        if self._hash(payload, "criterion_hash") != content_hash(criterion):
            raise ProjectControlError(ProjectControlErrorCode.STALE_VERIFICATION, "The acceptance criterion definition changed.")
        self._hash(payload, "result_hash")
        if str(criterion.get("verification_mode") or "") == "manual_user_verification_required" and payload.get("outcome") == "passed":
            raise ProjectControlError(ProjectControlErrorCode.STALE_VERIFICATION, "Manual criteria cannot pass from automated verifier evidence.")

    def _validate_handoff(self, connection: sqlite3.Connection, run: ProjectRun, payload: dict[str, Any]) -> None:
        self._require_manifest(run)
        if _text(payload.get("final_manifest_hash")) != run.current_manifest_hash:
            raise ProjectControlError(ProjectControlErrorCode.STALE_VERIFICATION, "The final live repository recheck does not match the current manifest.")
        self._require_approval(connection, run, ApprovalType.PLAN)
        if not run.work_unit_state or any(item.get("status") != "completed" for item in run.work_unit_state.values()):
            raise ProjectControlError(ProjectControlErrorCode.ILLEGAL_TRANSITION, "All required work units must be complete before handoff.")
        plan = self._load_plan(connection, run.current_plan_revision_id)
        for criterion in plan.acceptance_criteria:
            if criterion.get("required", True) is False:
                continue
            criterion_id = str(criterion.get("criterion_id") or criterion.get("id") or "")
            mode = str(criterion.get("verification_mode") or "")
            evidence = run.verification_state.get(criterion_id) or {}
            if mode == "manual_user_verification_required":
                self._require_authority_approval(connection, run, ApprovalType.MANUAL_VERIFICATION, "criterion_id", criterion_id)
                continue
            if not evidence or evidence.get("outcome") != "passed":
                raise ProjectControlError(ProjectControlErrorCode.STALE_VERIFICATION, "A required criterion lacks fresh passing verifier evidence.")
            if (evidence.get("plan_revision_id") != run.current_plan_revision_id
                    or evidence.get("scope_revision_id") != run.current_scope_revision_id
                    or evidence.get("manifest_hash") != run.current_manifest_hash
                    or evidence.get("criterion_hash") != content_hash(criterion)):
                raise ProjectControlError(ProjectControlErrorCode.STALE_VERIFICATION, "A required verifier result is stale.")

    def _verified_transition_artifact(
        self,
        run: ProjectRun,
        command: ProjectCommand,
    ) -> ProjectArtifact | None:
        from backend.app.project_artifacts.contracts import ProjectArtifactType
        from backend.app.project_artifacts.store import ProjectArtifactStoreError

        expected: dict[ProjectCommandType, frozenset[ProjectArtifactType]] = {
            ProjectCommandType.ATTACH_SPECIFICATION: frozenset({ProjectArtifactType.SPECIFICATION}),
            ProjectCommandType.REVISE_SCOPE: frozenset({ProjectArtifactType.SPECIFICATION}),
            ProjectCommandType.REGISTER_MANIFEST: frozenset({ProjectArtifactType.MANIFEST}),
            ProjectCommandType.PROPOSE_PLAN_REVISION: frozenset({ProjectArtifactType.PLAN}),
            ProjectCommandType.RECORD_PATCH_PREVIEW: frozenset({
                ProjectArtifactType.PATCH_PREVIEW,
                ProjectArtifactType.REPAIR_PREVIEW,
            }),
            ProjectCommandType.RECORD_COMMAND_PREVIEW: frozenset({ProjectArtifactType.COMMAND_PREVIEW}),
            ProjectCommandType.RECORD_VERIFIER_RESULT: frozenset({ProjectArtifactType.VERIFIER_RESULT}),
            ProjectCommandType.REQUEST_HANDOFF: frozenset({ProjectArtifactType.HANDOFF}),
        }
        allowed = expected.get(command.command_type)
        supplied = command.artifact_id is not None
        required = run.canonical_generation == "canonical" and allowed is not None
        if not supplied:
            if required:
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    "Canonical project transitions require an immutable artifact reference.",
                )
            return None
        if not (
            command.artifact_type
            and command.artifact_hash
            and command.artifact_binding_hash
        ):
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "Artifact references require the exact type, content hash, and binding hash.",
            )
        if allowed is None or self.artifact_store is None:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "This project transition does not accept an artifact reference.",
            )
        try:
            artifact = self.artifact_store.verify(
                command.artifact_id or "",
                expected_binding_hash=command.artifact_binding_hash,
                expected_content_hash=command.artifact_hash,
            )
        except ProjectArtifactStoreError as exc:
            raise ProjectControlError(
                ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                "The referenced project artifact is missing, stale, or corrupted.",
            ) from exc
        if artifact.artifact_type not in allowed or command.artifact_type != artifact.artifact_type.value:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "The referenced artifact type is not valid for this transition.",
            )
        binding = artifact.binding
        if binding.project_run_id != run.project_run_id:
            raise ProjectControlError(
                ProjectControlErrorCode.REPOSITORY_ROOT_MISMATCH,
                "The project artifact belongs to a different project.",
            )
        expected_manifest = run.current_manifest_hash
        if command.command_type == ProjectCommandType.REGISTER_MANIFEST:
            expected_manifest = _text(command.payload.get("manifest_hash"))
        exact_bindings = (
            (binding.plan_revision_id, run.current_plan_revision_id, ProjectControlErrorCode.PLAN_REVISION_MISMATCH),
            (binding.scope_revision_id, run.current_scope_revision_id, ProjectControlErrorCode.SCOPE_REVISION_MISMATCH),
            (binding.manifest_hash, expected_manifest, ProjectControlErrorCode.MANIFEST_MISMATCH),
        )
        for actual, current, code in exact_bindings:
            if actual is not None and actual != current:
                raise ProjectControlError(code, "The project artifact is bound to stale project state.")
        return artifact

    @staticmethod
    def _with_artifact(run: ProjectRun, artifact: ProjectArtifact | None) -> ProjectRun:
        if artifact is None:
            return run
        key = artifact.artifact_type.value
        ids = dict(run.current_artifact_ids)
        hashes = dict(run.current_artifact_hashes)
        ids[key] = artifact.artifact_id
        hashes[key] = artifact.content_hash
        return run.model_copy(
            update={"current_artifact_ids": ids, "current_artifact_hashes": hashes}
        )

    def _read_model(self, connection: sqlite3.Connection, run: ProjectRun) -> ProjectReadModel:
        approval_fresh = False
        if run.current_plan_revision_id and run.current_scope_revision_id and run.current_manifest_hash:
            plan_manifest = self._load_plan(connection, run.current_plan_revision_id).required_manifest_hash
            row = connection.execute(
                "SELECT 1 FROM project_approval_grants g LEFT JOIN project_approval_invalidations i ON i.approval_grant_id = g.approval_grant_id WHERE g.project_run_id = ? AND g.approval_type = 'plan' AND g.plan_revision_id = ? AND g.scope_revision_id = ? AND g.manifest_hash = ? AND i.approval_grant_id IS NULL LIMIT 1",
                (run.project_run_id, run.current_plan_revision_id, run.current_scope_revision_id, plan_manifest),
            ).fetchone()
            approval_fresh = row is not None
        completed = sum(1 for item in run.work_unit_state.values() if item.get("status") == "completed")
        active = next((key for key, item in run.work_unit_state.items() if item.get("status") == "in_progress"), None)
        outcomes = [str(item.get("outcome") or "pending") for item in run.verification_state.values()]
        handoff_eligible = run.handoff_eligible
        if not handoff_eligible and run.lifecycle_status in {ProjectLifecycle.READY_FOR_WORK, ProjectLifecycle.VERIFICATION_PENDING}:
            try:
                self._validate_handoff(connection, run, {"final_manifest_hash": run.current_manifest_hash})
                handoff_eligible = True
            except ProjectControlError:
                handoff_eligible = False
        attempt_row = connection.execute(
            "SELECT attempt_json FROM project_execution_attempts "
            "WHERE project_run_id = ? AND status IN ('pending', 'active') "
            "ORDER BY started_at DESC, execution_attempt_id DESC LIMIT 1",
            (run.project_run_id,),
        ).fetchone()
        active_attempt = (
            self._stored_model(ExecutionAttempt, attempt_row["attempt_json"], "attempt")
            if attempt_row is not None else None
        )
        dispatch_row = connection.execute(
            "SELECT dispatch_json FROM project_execution_dispatches "
            "WHERE project_run_id = ? "
            "ORDER BY created_at DESC, execution_dispatch_id DESC LIMIT 1",
            (run.project_run_id,),
        ).fetchone()
        active_dispatch = (
            self._stored_model(
                ExecutionDispatch,
                dispatch_row["dispatch_json"],
                "execution dispatch",
            )
            if dispatch_row is not None else None
        )
        worker_status: str | None = None
        worker_failure: str | None = None
        worker_result: dict[str, Any] = {}
        worker_updated_at: str | None = None
        if active_dispatch is not None and active_dispatch.worker_request_id:
            worker_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_worker_requests'"
            ).fetchone()
            if worker_table is not None:
                worker_row = connection.execute(
                    "SELECT status, failure_classification, result_json, updated_at "
                    "FROM project_worker_requests WHERE worker_request_id = ?",
                    (active_dispatch.worker_request_id,),
                ).fetchone()
                if worker_row is not None:
                    worker_status = str(worker_row["status"])
                    worker_failure = _text(worker_row["failure_classification"]) or None
                    worker_updated_at = _text(worker_row["updated_at"]) or None
                    try:
                        parsed_result = json.loads(str(worker_row["result_json"] or "{}"))
                        if isinstance(parsed_result, dict):
                            reference = parsed_result.get("result_reference")
                            worker_result = reference if isinstance(reference, dict) else {}
                    except (TypeError, ValueError, json.JSONDecodeError):
                        worker_result = {}
        return ProjectReadModel(
            project_run_id=run.project_run_id, conversation_id=run.conversation_id,
            lifecycle_state=run.lifecycle_status, plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id, manifest_hash=run.current_manifest_hash,
            manifest_complete=run.manifest_complete,
            approval_state="approved" if approval_fresh else ("reapproval_required" if run.requires_reapproval else "not_approved"),
            approval_fresh=approval_fresh, current_work_unit=active,
            progress={"completed_work_units": completed, "total_work_units": len(run.work_unit_state)},
            pending_user_action=run.pending_user_action,
            verification_summary={
                "passed": outcomes.count("passed"), "failed": outcomes.count("failed"),
                "manual_required": outcomes.count("manual_required"), "total": len(outcomes),
            },
            criterion_states={
                criterion_id: {
                    "outcome": str(evidence.get("outcome") or "pending"),
                    "result_hash": str(evidence.get("result_hash") or ""),
                }
                for criterion_id, evidence in run.verification_state.items()
            },
            blocked_reason=run.blocked_reason, handoff_eligible=handoff_eligible,
            state_version=run.state_version,
            terminal=run.lifecycle_status in {ProjectLifecycle.CANCELLED, ProjectLifecycle.COMPLETED},
            active_execution_attempt_id=(
                active_attempt.execution_attempt_id if active_attempt else None
            ),
            active_execution_attempt_type=(active_attempt.attempt_type if active_attempt else None),
            active_execution_attempt_status=(active_attempt.status if active_attempt else None),
            execution_dispatch_id=(
                active_dispatch.execution_dispatch_id if active_dispatch else None
            ),
            execution_dispatch_status=(active_dispatch.status if active_dispatch else None),
            worker_request_id=(active_dispatch.worker_request_id if active_dispatch else None),
            worker_request_status=worker_status,
            execution_failure_classification=(
                worker_failure
                or (active_dispatch.failure_classification if active_dispatch else None)
                or (active_attempt.failure_classification if active_attempt else None)
            ),
            artifact_references=run.current_artifact_ids,
            artifact_hashes=run.current_artifact_hashes,
            current_specification_artifact_id=run.current_artifact_ids.get("specification"),
            current_specification_artifact_hash=run.current_artifact_hashes.get("specification"),
            current_manifest_artifact_id=run.current_artifact_ids.get("manifest"),
            current_manifest_artifact_hash=run.current_artifact_hashes.get("manifest"),
            current_plan_artifact_id=run.current_artifact_ids.get("plan"),
            current_plan_artifact_hash=run.current_artifact_hashes.get("plan"),
            current_patch_preview_artifact_id=run.current_artifact_ids.get("patch_preview"),
            current_patch_preview_artifact_hash=run.current_artifact_hashes.get("patch_preview"),
            current_command_preview_artifact_id=run.current_artifact_ids.get("command_preview"),
            current_command_preview_artifact_hash=run.current_artifact_hashes.get("command_preview"),
            current_verifier_result_artifact_id=run.current_artifact_ids.get("verifier_result"),
            current_verifier_result_artifact_hash=run.current_artifact_hashes.get("verifier_result"),
            current_repair_preview_artifact_id=run.current_artifact_ids.get("repair_preview"),
            current_repair_preview_artifact_hash=run.current_artifact_hashes.get("repair_preview"),
            current_handoff_artifact_id=run.current_artifact_ids.get("handoff"),
            current_handoff_artifact_hash=run.current_artifact_hashes.get("handoff"),
            execution_evidence_references=worker_result,
            execution_timestamps={
                "attempt_started_at": (
                    active_attempt.started_at.isoformat() if active_attempt else None
                ),
                "dispatch_created_at": (
                    active_dispatch.created_at.isoformat() if active_dispatch else None
                ),
                "dispatch_delivered_at": (
                    active_dispatch.dispatched_at.isoformat()
                    if active_dispatch and active_dispatch.dispatched_at else None
                ),
                "worker_updated_at": worker_updated_at,
            },
            next_permitted_action=run.pending_user_action,
        )

    def _load_plan(self, connection: sqlite3.Connection, plan_revision_id: str | None) -> PlanRevision:
        if not plan_revision_id:
            raise ProjectControlError(ProjectControlErrorCode.PLAN_REVISION_MISMATCH, "No current plan revision exists.")
        row = connection.execute(
            "SELECT schema_version, revision_json FROM project_plan_revisions_v3 WHERE plan_revision_id = ?", (plan_revision_id,)
        ).fetchone()
        if row is None:
            raise ProjectControlError(ProjectControlErrorCode.CORRUPTED_STORED_STATE, "The current plan revision is missing.")
        if row["schema_version"] != PLAN_REVISION_VERSION:
            raise ProjectControlError(ProjectControlErrorCode.UNSUPPORTED_STORED_STATE, "The stored plan schema is not supported.")
        plan = self._stored_model(PlanRevision, row["revision_json"], "plan revision")
        check = plan.model_dump(mode="json", exclude={"schema_version", "plan_revision_id", "content_hash", "created_at"})
        if content_hash(check) != plan.content_hash:
            raise ProjectControlError(ProjectControlErrorCode.CORRUPTED_STORED_STATE, "The stored plan content hash is invalid.")
        return plan

    def _require_manifest(self, run: ProjectRun) -> None:
        if not run.manifest_complete or not run.current_manifest_hash:
            raise ProjectControlError(ProjectControlErrorCode.INCOMPLETE_MANIFEST, "A complete live repository manifest is required.")

    def _with_grant(self, run: ProjectRun, grant: ApprovalGrant) -> ProjectRun:
        return run.model_copy(update={"active_approval_grant_ids": (*run.active_approval_grant_ids, grant.approval_grant_id)})

    def _with_attempt(self, run: ProjectRun, attempt: ExecutionAttempt) -> ProjectRun:
        if attempt.execution_attempt_id in run.execution_attempt_ids:
            return run
        return run.model_copy(update={"execution_attempt_ids": (*run.execution_attempt_ids, attempt.execution_attempt_id)})

    def _cancel_active_attempts(self, connection: sqlite3.Connection, project_run_id: str) -> None:
        rows = connection.execute(
            "SELECT attempt_json FROM project_execution_attempts WHERE project_run_id = ? AND status IN ('pending', 'active')",
            (project_run_id,),
        ).fetchall()
        for row in rows:
            attempt = self._stored_model(ExecutionAttempt, row["attempt_json"], "attempt")
            finished = attempt.model_copy(update={"status": ExecutionAttemptStatus.CANCELLED, "finished_at": self._now()})
            connection.execute(
                "UPDATE project_execution_attempts SET status = ?, attempt_json = ?, finished_at = ? WHERE execution_attempt_id = ?",
                (finished.status.value, finished.model_dump_json(), finished.finished_at.isoformat(), finished.execution_attempt_id),
            )

    def _cancel_pending_dispatches(
        self,
        connection: sqlite3.Connection,
        project_run_id: str,
        *,
        execution_attempt_id: str | None = None,
    ) -> None:
        sql = (
            "SELECT dispatch_json FROM project_execution_dispatches "
            "WHERE project_run_id = ? AND status = 'pending'"
        )
        parameters: tuple[Any, ...] = (project_run_id,)
        if execution_attempt_id:
            sql += " AND execution_attempt_id = ?"
            parameters = (project_run_id, execution_attempt_id)
        rows = connection.execute(sql, parameters).fetchall()
        now = self._now()
        for row in rows:
            dispatch = self._stored_model(
                ExecutionDispatch,
                row["dispatch_json"],
                "execution dispatch",
            )
            cancelled = dispatch.model_copy(update={
                "status": ExecutionDispatchStatus.CANCELLED,
                "cancelled_at": now,
                "failure_classification": "canonical_attempt_cancelled",
            })
            connection.execute(
                "UPDATE project_execution_dispatches SET status = ?, dispatch_json = ?, cancelled_at = ? WHERE execution_dispatch_id = ? AND status = 'pending'",
                (
                    cancelled.status.value,
                    cancelled.model_dump_json(),
                    now.isoformat(),
                    cancelled.execution_dispatch_id,
                ),
            )

    def _interrupt_attempt(self, connection: sqlite3.Connection, run: ProjectRun, attempt_id: str) -> None:
        row = connection.execute(
            "SELECT attempt_json FROM project_execution_attempts WHERE execution_attempt_id = ? AND project_run_id = ?",
            (attempt_id, run.project_run_id),
        ).fetchone()
        if row is None:
            raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "Execution attempt not found.")
        attempt = self._stored_model(ExecutionAttempt, row["attempt_json"], "attempt")
        if attempt.status not in {ExecutionAttemptStatus.PENDING, ExecutionAttemptStatus.ACTIVE}:
            raise ProjectControlError(ProjectControlErrorCode.ILLEGAL_TRANSITION, "Only an active attempt can be recovered as interrupted.")
        finished = attempt.model_copy(update={"status": ExecutionAttemptStatus.INTERRUPTED, "finished_at": self._now(), "failure_classification": "process_interrupted"})
        connection.execute(
            "UPDATE project_execution_attempts SET status = ?, attempt_json = ?, finished_at = ? WHERE execution_attempt_id = ?",
            (finished.status.value, finished.model_dump_json(), finished.finished_at.isoformat(), attempt_id),
        )
        self._cancel_pending_dispatches(
            connection,
            run.project_run_id,
            execution_attempt_id=attempt_id,
        )

    def _event_metadata(self, command: ProjectCommand, created: tuple[str, ...]) -> dict[str, Any]:
        allowed = (
            "work_unit_id", "patch_id", "rollback_id", "command_id", "criterion_id",
            "execution_attempt_id", "reason",
        )
        metadata = {key: _text(command.payload.get(key))[:180] for key in allowed if _text(command.payload.get(key))}
        metadata["created_record_ids"] = list(created)[:12]
        return metadata

    def _hash(self, payload: dict[str, Any], key: str) -> str:
        value = _text(payload.get(key))
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
            raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, f"{key} must be a SHA-256 hex digest.")
        return value.lower()

    def _followup(self, kind: ProjectCommandType, run: ProjectRun, base: dict[str, str], key: str,
                  payload: dict[str, Any]) -> ProjectCommand:
        return ProjectCommand(
            command_type=kind, expected_state_version=run.state_version, idempotency_key=key,
            plan_revision_id=run.current_plan_revision_id, scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash, payload=payload, **base,
        )

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)


def _required(value: dict[str, Any], key: str) -> str:
    result = _text(value.get(key))
    if not result:
        raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, f"{key} is required.")
    return result


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, (str, int)) else ""


def _strings(value: Any) -> list[str]:
    return [_text(item)[:500] for item in value] if isinstance(value, (list, tuple)) else []


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [_bounded_object(item) for item in value if isinstance(item, dict)] if isinstance(value, (list, tuple)) else []


def _bounded_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in list(value.items())[:40]:
        name = str(key)[:100]
        if isinstance(item, str):
            result[name] = item[:2000]
        elif isinstance(item, (int, float, bool, type(None))):
            result[name] = item
        elif isinstance(item, (list, tuple)):
            result[name] = [entry[:500] if isinstance(entry, str) else entry for entry in list(item)[:40] if isinstance(entry, (str, int, float, bool, type(None)))]
        elif isinstance(item, dict):
            result[name] = _bounded_object(item)
    return result


def _dispatch_object(
    value: Any,
    label: str,
    max_bytes: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectControlError(
            ProjectControlErrorCode.INVALID_COMMAND,
            f"{label} must be an object.",
        )
    try:
        copied = json.loads(json.dumps(value))
        encoded = canonical_json(copied).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProjectControlError(
            ProjectControlErrorCode.INVALID_COMMAND,
            f"{label} must contain JSON values.",
        ) from error
    if len(encoded) > max_bytes:
        raise ProjectControlError(
            ProjectControlErrorCode.INVALID_COMMAND,
            f"{label} exceeds its bounded size limit.",
        )
    return copied


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value))
