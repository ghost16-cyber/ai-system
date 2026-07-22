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
    TRANSITION_RESULT_VERSION,
    ApprovalGrant,
    ExecutionDispatch,
    ExecutionDispatchStatus,
    ApprovalType,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionAttemptType,
    ManualEvidenceInvalidation,
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
from backend.app.project_control.cancellation import (
    ExecutionCancellation,
    ExecutionCancellationStatus,
    build_execution_cancellation,
)
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
        initialize_stage3a_schema(self.database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
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

    def list_manual_evidence(self, project_run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._load_project(connection, project_run_id)
            return self._manual_evidence_history(connection, project_run_id)

    def get_plan_revision(self, plan_revision_id: str) -> PlanRevision:
        with self._connect() as connection:
            return self._load_plan(connection, plan_revision_id)

    def has_idempotency_key(self, project_run_id: str, idempotency_key: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM project_action_replays WHERE project_run_id = ? AND idempotency_key = ?",
                (project_run_id, idempotency_key),
            ).fetchone() is not None

    def replay_completed(self, command: ProjectCommand) -> TransitionResult | None:
        """Return a completed exact command result without evaluating live state.

        Identity is still checked against the canonical project. The normalized
        command hash includes every request/version/artifact/authority binding,
        so a reused key with any changed binding fails closed.
        """
        parsed = ProjectCommand.model_validate(command)
        request_hash = content_hash(parsed.model_dump(mode="json"))
        with self._connect() as connection:
            run = self._load_project(connection, parsed.project_run_id)
            self._validate_identity(run, parsed)
            return self._idempotent_result(connection, parsed, request_hash)

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

    def get_execution_cancellation(self, cancellation_id: str) -> ExecutionCancellation:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancellation_json FROM project_execution_cancellations WHERE cancellation_id = ?",
                (cancellation_id,),
            ).fetchone()
        if row is None:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "Execution cancellation not found.",
            )
        return self._stored_model(
            ExecutionCancellation, row["cancellation_json"], "execution cancellation"
        )

    def get_execution_cancellation_for_attempt(
        self, execution_attempt_id: str
    ) -> ExecutionCancellation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancellation_json FROM project_execution_cancellations "
                "WHERE execution_attempt_id = ?",
                (execution_attempt_id,),
            ).fetchone()
        return (
            self._stored_model(
                ExecutionCancellation,
                row["cancellation_json"],
                "execution cancellation",
            )
            if row is not None else None
        )

    def list_execution_cancellations(
        self,
        *,
        statuses: tuple[ExecutionCancellationStatus, ...] | None = None,
        limit: int = 100,
    ) -> list[ExecutionCancellation]:
        bounded = max(1, min(int(limit), 500))
        with self._connect() as connection:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = connection.execute(
                    f"SELECT cancellation_json FROM project_execution_cancellations "
                    f"WHERE status IN ({placeholders}) ORDER BY created_at, cancellation_id LIMIT ?",
                    (*[item.value for item in statuses], bounded),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT cancellation_json FROM project_execution_cancellations "
                    "ORDER BY created_at, cancellation_id LIMIT ?",
                    (bounded,),
                ).fetchall()
        return [
            self._stored_model(
                ExecutionCancellation, row["cancellation_json"], "execution cancellation"
            )
            for row in rows
        ]

    def mark_execution_cancellation_dispatched(
        self, cancellation_id: str
    ) -> ExecutionCancellation:
        return self._update_cancellation_delivery(
            cancellation_id, status=ExecutionCancellationStatus.DISPATCHED
        )

    def mark_execution_cancellation_failed(
        self, cancellation_id: str, *, failure_classification: str
    ) -> ExecutionCancellation:
        return self._update_cancellation_delivery(
            cancellation_id,
            status=ExecutionCancellationStatus.FAILED,
            failure_classification=failure_classification,
        )

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
            created.extend(self._invalidate_manual_evidence(
                connection,
                run.project_run_id,
                "plan_revision_superseded",
                superseding={
                    "plan_revision_id": plan.plan_revision_id,
                    "scope_revision_id": run.current_scope_revision_id,
                    "manifest_hash": run.current_manifest_hash,
                    "artifact_id": artifact.artifact_id if artifact else None,
                },
            ))
            work_state = {
                str(unit.get("work_unit_id") or unit.get("id")): {"status": "pending", "attempts": 0}
                for unit in plan.work_units if str(unit.get("work_unit_id") or unit.get("id"))
            }
            return self._with_artifact(run.model_copy(update={
                "current_plan_revision_id": plan.plan_revision_id,
                "active_approval_grant_ids": (),
                "work_unit_state": work_state,
                "verification_state": {},
                "repair_state": {},
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
            updated = self._with_attempt(run, attempt).model_copy(update={
                "work_unit_state": state, "lifecycle_status": ProjectLifecycle.WORK_IN_PROGRESS,
                "pending_user_action": "record_patch_preview",
            })
            if artifact is not None:
                patch_id = _required(payload, "patch_id")
                updated = self._with_artifact(updated, artifact).model_copy(update={
                    "lifecycle_status": ProjectLifecycle.AWAITING_PATCH_APPROVAL,
                    "pending_user_action": f"approve_patch:{patch_id}",
                })
            return updated, tuple(created)
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
            if manifest_hash != run.current_manifest_hash:
                created.extend(self._invalidate_manual_evidence(
                    connection,
                    run.project_run_id,
                    "execution_manifest_superseded",
                    superseding={
                        "plan_revision_id": run.current_plan_revision_id,
                        "scope_revision_id": run.current_scope_revision_id,
                        "manifest_hash": manifest_hash,
                        "execution_attempt_id": attempt.execution_attempt_id,
                        "artifact_id": command.artifact_id,
                    },
                ))
            status = ProjectLifecycle.WORK_IN_PROGRESS if succeeded else ProjectLifecycle.REPAIR_REQUIRED
            updated = self._with_artifact(self._with_attempt(run, attempt).model_copy(update={
                "current_manifest_hash": manifest_hash,
                "lifecycle_status": status,
                "pending_user_action": "request_verification" if succeeded else "initiate_repair",
                "verification_state": {} if manifest_hash != run.current_manifest_hash else run.verification_state,
                "handoff_eligible": False,
            }), artifact)
            if not succeeded:
                updated = self._with_failure_artifact(updated, payload)
            return updated, tuple(created)
        if kind == ProjectCommandType.RECORD_COMMAND_PREVIEW:
            _required(payload, "command_id")
            return self._with_artifact(run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.AWAITING_COMMAND_APPROVAL,
                "pending_user_action": f"approve_command:{payload['command_id']}",
            }), artifact), ()
        if kind == ProjectCommandType.APPROVE_COMMAND:
            # Client-supplied payload can never select the approval type: manual
            # verification has its own dedicated SUBMIT_MANUAL_EVIDENCE command
            # and criterion binding. Honoring a payload override here would let
            # an approve_command request skip execution/verification entirely.
            grant = self._create_approval(connection, run, command, ApprovalType.COMMAND)
            created.append(grant.approval_grant_id)
            return self._with_grant(run, grant).model_copy(update={
                "lifecycle_status": ProjectLifecycle.WORK_IN_PROGRESS,
                "pending_user_action": "record_command_result",
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
            next_action = (
                "request_verification"
                if succeeded and run.canonical_generation == "canonical"
                else ("record_verifier_result" if succeeded else "initiate_repair")
            )
            updated = self._with_artifact(self._with_attempt(run, attempt).model_copy(update={
                "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING if succeeded else ProjectLifecycle.REPAIR_REQUIRED,
                "pending_user_action": next_action,
            }), artifact)
            if not succeeded:
                updated = self._with_failure_artifact(updated, payload)
            return updated, tuple(created)
        if kind == ProjectCommandType.REQUEST_VERIFICATION:
            attempt = self._create_attempt(connection, run, command, ExecutionAttemptType.VERIFICATION)
            created.append(attempt.execution_attempt_id)
            criterion_id = _text(
                payload.get("criterion_id") or command.authority_scope.get("criterion_id")
            )
            if criterion_id:
                created.extend(self._invalidate_manual_evidence(
                    connection,
                    run.project_run_id,
                    "verification_attempt_superseded",
                    criterion_ids={criterion_id},
                    superseding={
                        "plan_revision_id": run.current_plan_revision_id,
                        "scope_revision_id": run.current_scope_revision_id,
                        "manifest_hash": run.current_manifest_hash,
                        "execution_attempt_id": attempt.execution_attempt_id,
                    },
                ))
            return self._with_attempt(run, attempt).model_copy(update={
                "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING,
                "pending_user_action": "record_verifier_result",
            }), tuple(created)
        if kind == ProjectCommandType.RECORD_VERIFIER_RESULT:
            criterion_id = _required(payload, "criterion_id")
            self._validate_verifier_result(connection, run, payload, criterion_id)
            verifier_attempt = self._active_attempt(connection, run)
            created.extend(self._invalidate_manual_evidence(
                connection,
                run.project_run_id,
                "verification_artifact_superseded",
                criterion_ids={criterion_id},
                superseding={
                    "plan_revision_id": run.current_plan_revision_id,
                    "scope_revision_id": run.current_scope_revision_id,
                    "manifest_hash": run.current_manifest_hash,
                    "execution_attempt_id": (
                        verifier_attempt.execution_attempt_id if verifier_attempt else None
                    ),
                    "artifact_id": command.artifact_id,
                },
            ))
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
            if outcome == "manual_required":
                active_attempt = self._active_attempt(connection, run)
                if active_attempt is None:
                    raise ProjectControlError(
                        ProjectControlErrorCode.STALE_VERIFICATION,
                        "Manual evidence requires an active verification attempt.",
                    )
                verification[criterion_id]["outcome"] = "manual_evidence_required"
                verification[criterion_id]["verification_artifact_id"] = command.artifact_id
                verification[criterion_id]["verification_artifact_hash"] = command.artifact_hash
                verification[criterion_id]["execution_attempt_id"] = active_attempt.execution_attempt_id
                updated = self._with_artifact(run.model_copy(update={
                    "verification_state": verification,
                    "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING,
                    "pending_user_action": f"submit_manual_evidence:{criterion_id}",
                    "handoff_eligible": False,
                }), artifact)
                return updated, tuple(created)
            attempt = self._finish_or_create_attempt(connection, run, command, ExecutionAttemptType.VERIFICATION, succeeded=succeeded)
            created.append(attempt.execution_attempt_id)
            updated = self._with_artifact(self._with_attempt(run, attempt).model_copy(update={
                "verification_state": verification,
                "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING if succeeded else ProjectLifecycle.REPAIR_REQUIRED,
                "pending_user_action": "complete_work_unit" if succeeded else "initiate_repair",
                "handoff_eligible": False,
            }), artifact)
            if succeeded and run.canonical_generation == "canonical":
                updated = self._advance_after_canonical_verification(
                    connection, updated, criterion_id
                )
            elif not succeeded:
                updated = self._with_failure_artifact(updated, payload)
                if int(updated.repair_state.get("cycle_count") or 0) >= 1:
                    updated = updated.model_copy(update={
                        "lifecycle_status": ProjectLifecycle.BLOCKED,
                        "pending_user_action": "review_failed_repair",
                        "blocked_reason": (
                            "The single approved repair did not pass fresh verification."
                        ),
                        "repair_state": {
                            **updated.repair_state,
                            "status": "failed",
                        },
                    })
            return updated, tuple(created)
        if kind == ProjectCommandType.SUBMIT_MANUAL_EVIDENCE:
            criterion_id = _required(payload, "criterion_id")
            current = _copy(run.verification_state.get(criterion_id)) or {}
            active_attempt = self._active_attempt(connection, run)
            active_attempt_id = active_attempt.execution_attempt_id if active_attempt else None
            if current.get("outcome") != "manual_evidence_required":
                raise ProjectControlError(
                    ProjectControlErrorCode.STALE_VERIFICATION,
                    "The criterion is not awaiting manual evidence.",
                )
            exact = {
                "plan_revision_id": run.current_plan_revision_id,
                "scope_revision_id": run.current_scope_revision_id,
                "manifest_hash": run.current_manifest_hash,
                "execution_attempt_id": active_attempt_id,
                "criterion_hash": current.get("criterion_hash"),
                "verification_artifact_id": current.get("verification_artifact_id"),
                "verification_artifact_hash": current.get("verification_artifact_hash"),
            }
            for field, expected_value in exact.items():
                if payload.get(field) != expected_value:
                    raise ProjectControlError(
                        ProjectControlErrorCode.STALE_VERIFICATION,
                        f"Manual evidence has a stale {field.replace('_', ' ')} binding.",
                    )
            decision = _required(payload, "decision")
            if decision not in {"passed", "failed"}:
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    "Manual evidence requires an explicit passed or failed reviewer decision.",
                )
            evidence_id = _required(payload, "evidence_id")
            evidence_hash = self._hash(payload, "evidence_hash")
            now = self._now()
            evidence_record = {
                "schema_version": "astra.project-control.manual-evidence.v1",
                "evidence_id": evidence_id,
                "project_run_id": run.project_run_id,
                "criterion_id": criterion_id,
                "criterion_hash": current.get("criterion_hash"),
                "work_unit_id": payload.get("work_unit_id"),
                "execution_attempt_id": active_attempt_id,
                "plan_revision_id": run.current_plan_revision_id,
                "scope_revision_id": run.current_scope_revision_id,
                "manifest_hash": run.current_manifest_hash,
                "verification_artifact_id": current.get("verification_artifact_id"),
                "evidence_artifact_id": command.artifact_id,
                "evidence_hash": evidence_hash,
                "decision": decision,
                "evidence": _bounded_object(payload.get("evidence")),
                "reviewer_id": command.actor_id,
                "created_at": now.isoformat(),
            }
            connection.execute(
                "INSERT INTO project_manual_evidence (evidence_id, project_run_id, criterion_id, execution_attempt_id, evidence_hash, status, idempotency_key, evidence_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (evidence_id, run.project_run_id, criterion_id, str(active_attempt_id),
                 evidence_hash, decision, command.idempotency_key, canonical_json(evidence_record),
                 now.isoformat(), now.isoformat()),
            )
            verification = _copy(run.verification_state)
            verification[criterion_id] = {
                **current,
                "outcome": decision,
                "manual_status": "verification_passed" if decision == "passed" else "verification_failed",
                "evidence_id": evidence_id,
                "evidence_artifact_id": command.artifact_id,
                "evidence_hash": evidence_hash,
                "reviewer_id": command.actor_id,
            }
            attempt = self._finish_or_create_attempt(
                connection, run, command, ExecutionAttemptType.VERIFICATION,
                succeeded=decision == "passed",
            )
            created.extend((evidence_id, attempt.execution_attempt_id))
            updated = self._with_artifact(self._with_attempt(run, attempt).model_copy(update={
                "verification_state": verification,
                "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING,
                "pending_user_action": "complete_work_unit" if decision == "passed" else f"review_manual_failure:{criterion_id}",
                "handoff_eligible": False,
            }), artifact)
            if decision == "passed" and run.canonical_generation == "canonical":
                updated = self._advance_after_canonical_verification(connection, updated, criterion_id)
            return updated, tuple(created)
        if kind == ProjectCommandType.REQUEST_CLARIFICATION:
            return run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.CLARIFICATION_REQUIRED,
                "pending_user_action": "answer_clarification",
                "blocked_reason": _text(payload.get("reason")) or "Clarification is required.",
            }), ()
        if kind == ProjectCommandType.MARK_BLOCKED:
            return self._with_artifact(run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.BLOCKED,
                "pending_user_action": _text(payload.get("pending_user_action")) or "review_block",
                "blocked_reason": _text(payload.get("reason")) or "Project work is blocked.",
                "handoff_eligible": False,
            }), artifact), ()
        if kind == ProjectCommandType.REVISE_SCOPE:
            specification_hash = _text(payload.get("specification_hash")) or run.specification_hash
            if not specification_hash:
                raise ProjectControlError(ProjectControlErrorCode.INVALID_COMMAND, "A scope revision requires a specification binding.")
            scope = self._create_scope(connection, run, command, specification_hash)
            created.append(scope.scope_revision_id)
            self._invalidate_approvals(connection, run.project_run_id, "scope_revision_superseded")
            created.extend(self._invalidate_manual_evidence(
                connection,
                run.project_run_id,
                "scope_revision_superseded",
                superseding={
                    "scope_revision_id": scope.scope_revision_id,
                    "manifest_hash": run.current_manifest_hash,
                    "artifact_id": artifact.artifact_id if artifact else None,
                },
            ))
            return self._with_artifact(run.model_copy(update={
                "specification_hash": specification_hash,
                "current_scope_revision_id": scope.scope_revision_id,
                "current_plan_revision_id": None,
                "active_approval_grant_ids": (),
                "work_unit_state": {}, "verification_state": {}, "repair_state": {},
                "handoff_eligible": False,
                "lifecycle_status": ProjectLifecycle.PLANNING,
                "pending_user_action": "propose_plan_revision",
                "requires_reapproval": True,
            }), artifact), tuple(created)
        if kind == ProjectCommandType.INITIATE_REPAIR:
            if int(run.repair_state.get("cycle_count") or 0) >= 1:
                raise ProjectControlError(
                    ProjectControlErrorCode.ILLEGAL_TRANSITION,
                    "The automatic one-repair budget has been exhausted.",
                )
            failure_artifact_id = _required(payload, "failure_artifact_id")
            if run.current_artifact_ids.get("failure_evidence") != failure_artifact_id:
                raise ProjectControlError(
                    ProjectControlErrorCode.STALE_VERIFICATION,
                    "The repair is bound to stale failure evidence.",
                )
            attempt = self._create_attempt(connection, run, command, ExecutionAttemptType.REPAIR)
            created.append(attempt.execution_attempt_id)
            created.extend(self._invalidate_manual_evidence(
                connection,
                run.project_run_id,
                "repair_attempt_started",
                superseding={
                    "plan_revision_id": run.current_plan_revision_id,
                    "scope_revision_id": run.current_scope_revision_id,
                    "manifest_hash": run.current_manifest_hash,
                    "execution_attempt_id": attempt.execution_attempt_id,
                    "artifact_id": failure_artifact_id,
                },
            ))
            updated = self._with_attempt(run, attempt).model_copy(update={
                "lifecycle_status": ProjectLifecycle.WORK_IN_PROGRESS,
                "pending_user_action": "record_patch_preview",
                "repair_state": {
                    **run.repair_state,
                    "cycle_count": 1,
                    "repair_cycle_id": _required(payload, "repair_cycle_id"),
                    "failure_artifact_id": failure_artifact_id,
                    "status": "preparing",
                },
            })
            if artifact is not None:
                patch_id = _required(payload, "patch_id")
                updated = self._with_artifact(updated, artifact).model_copy(update={
                    "lifecycle_status": ProjectLifecycle.AWAITING_PATCH_APPROVAL,
                    "pending_user_action": f"approve_patch:{patch_id}",
                    "repair_state": {
                        **updated.repair_state,
                        "repair_preview_artifact_id": artifact.artifact_id,
                        "status": "awaiting_approval",
                    },
                })
            return updated, tuple(created)
        if kind == ProjectCommandType.RECORD_ROLLBACK_PREVIEW:
            rollback_id = _required(payload, "rollback_id")
            return self._with_artifact(run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.ROLLBACK_PENDING,
                "pending_user_action": f"approve_rollback:{rollback_id}",
            }), artifact), ()
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
            resulting_manifest_hash = _text(payload.get("resulting_manifest_hash")) or run.current_manifest_hash
            created.extend(self._invalidate_manual_evidence(
                connection,
                run.project_run_id,
                "rollback_changed_manifest",
                superseding={
                    "plan_revision_id": run.current_plan_revision_id,
                    "scope_revision_id": run.current_scope_revision_id,
                    "manifest_hash": resulting_manifest_hash,
                    "execution_attempt_id": attempt.execution_attempt_id,
                    "artifact_id": command.artifact_id,
                },
            ))
            return self._with_artifact(self._with_attempt(run, attempt).model_copy(update={
                "current_manifest_hash": resulting_manifest_hash,
                "verification_state": {}, "handoff_eligible": False,
                "lifecycle_status": ProjectLifecycle.READY_FOR_WORK if succeeded else ProjectLifecycle.REPAIR_REQUIRED,
                "pending_user_action": "begin_work_unit" if succeeded else "initiate_repair",
            }), artifact), tuple(created)
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
            if run.canonical_generation == "canonical":
                if artifact is None:
                    raise ProjectControlError(
                        ProjectControlErrorCode.INVALID_COMMAND,
                        "Canonical finalization requires the exact handoff artifact.",
                    )
                if run.current_artifact_ids.get("handoff") != artifact.artifact_id:
                    raise ProjectControlError(
                        ProjectControlErrorCode.STALE_VERIFICATION,
                        "The handoff artifact is stale or no longer current.",
                    )
                return self._with_artifact(run.model_copy(update={
                    "lifecycle_status": ProjectLifecycle.COMPLETED,
                    "pending_user_action": None,
                    "terminal_reason": "Project handoff finalized.",
                }), artifact), ()
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
            cancellation = self._begin_execution_cancellation(
                connection,
                run,
                requested_by=command.actor_id,
                reason=_text(payload.get("reason")) or "Cancelled by the authorized actor.",
            )
            if cancellation is not None:
                created.append(cancellation.cancellation_id)
                return run.model_copy(update={
                    "pending_user_action": "cancelling",
                    "handoff_eligible": False,
                }), tuple(created)
            self._cancel_active_attempts(connection, run.project_run_id)
            self._cancel_pending_dispatches(connection, run.project_run_id)
            return run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.CANCELLED,
                "pending_user_action": None,
                "handoff_eligible": False,
                "terminal_reason": _text(payload.get("reason")) or "Cancelled by the authorized actor.",
            }), ()
        if kind == ProjectCommandType.ACKNOWLEDGE_EXECUTION_CANCELLATION:
            cancellation_id = _required(payload, "cancellation_id")
            cancellation = self._load_execution_cancellation(connection, cancellation_id)
            if cancellation.project_run_id != run.project_run_id:
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    "The cancellation belongs to another project.",
                )
            if cancellation.status == ExecutionCancellationStatus.ACKNOWLEDGED:
                return run, ()
            worker_status = self._terminal_worker_cancellation_status(
                connection, cancellation
            )
            if worker_status is None:
                raise ProjectControlError(
                    ProjectControlErrorCode.ILLEGAL_TRANSITION,
                    "The worker has not durably acknowledged cancellation.",
                )
            attempt_status = (
                ExecutionAttemptStatus.CANCELLED
                if worker_status == "cancelled"
                else ExecutionAttemptStatus.INTERRUPTED
            )
            failure = _text(payload.get("failure_classification")) or (
                "execution_cancelled"
                if attempt_status == ExecutionAttemptStatus.CANCELLED
                else f"cancellation_acknowledged_after_{worker_status}"
            )
            self._finish_cancelled_attempt(
                connection,
                cancellation.execution_attempt_id,
                status=attempt_status,
                failure_classification=failure,
            )
            now = self._now()
            acknowledged = cancellation.model_copy(update={
                "status": ExecutionCancellationStatus.ACKNOWLEDGED,
                "failure_classification": None,
                "updated_at": now,
                "acknowledged_at": now,
            })
            connection.execute(
                "UPDATE project_execution_cancellations SET status = ?, cancellation_json = ?, "
                "updated_at = ?, acknowledged_at = ? WHERE cancellation_id = ?",
                (
                    acknowledged.status.value,
                    acknowledged.model_dump_json(),
                    now.isoformat(),
                    now.isoformat(),
                    cancellation_id,
                ),
            )
            return self._with_artifact(run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.CANCELLED,
                "pending_user_action": None,
                "handoff_eligible": False,
                "terminal_reason": cancellation.reason,
            }), artifact), ()
        if kind == ProjectCommandType.RECOVER_ATTEMPT:
            attempt_id = _required(payload, "execution_attempt_id")
            self._interrupt_attempt(connection, run, attempt_id)
            return self._with_artifact(run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.BLOCKED,
                "blocked_reason": "An active execution attempt was interrupted and requires review.",
                "pending_user_action": "review_interrupted_attempt",
            }), artifact), ()
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
        replay_payload = {
            "request_fingerprint": request_hash,
            "command": command.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "artifact_binding": {
                "artifact_id": command.artifact_id,
                "artifact_type": command.artifact_type,
                "artifact_hash": command.artifact_hash,
                "artifact_binding_hash": command.artifact_binding_hash,
            },
            "authority": command.authority_scope,
        }
        connection.execute(
            "INSERT INTO project_action_replays (project_run_id, idempotency_key, action_type, request_fingerprint, terminal_status, state_version_before, state_version_after, event_id, result_schema_version, replay_json, created_at) VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)",
            (resulting.project_run_id, command.idempotency_key, command.command_type.value,
             request_hash, event.previous_state_version, resulting.state_version,
             event.event_id, result.schema_version, canonical_json(replay_payload), now.isoformat()),
        )
        return result

    def _idempotent_result(self, connection: sqlite3.Connection, command: ProjectCommand, request_hash: str) -> TransitionResult | None:
        row = connection.execute(
            "SELECT action_type, request_fingerprint, terminal_status, "
            "state_version_before, state_version_after, event_id, "
            "result_schema_version, replay_json FROM project_action_replays "
            "WHERE project_run_id = ? AND idempotency_key = ?",
            (command.project_run_id, command.idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if (
            str(row["action_type"]) != command.command_type.value
            or str(row["request_fingerprint"]) != request_hash
        ):
            raise ProjectControlError(
                ProjectControlErrorCode.IDEMPOTENCY_CONFLICT,
                "This idempotency key was already used for a different project command.",
            )
        if str(row["terminal_status"]) != "completed":
            raise ProjectControlError(
                ProjectControlErrorCode.UNSUPPORTED_STORED_STATE,
                "The stored action replay terminal status is unsupported.",
            )
        if str(row["result_schema_version"]) != TRANSITION_RESULT_VERSION:
            raise ProjectControlError(
                ProjectControlErrorCode.UNSUPPORTED_STORED_STATE,
                "The stored action replay result schema is unsupported.",
            )
        try:
            replay = json.loads(str(row["replay_json"]))
            if not isinstance(replay, dict) or not isinstance(replay.get("result"), dict):
                raise TypeError("action replay payload is incomplete")
            if replay.get("request_fingerprint") != request_hash:
                raise ValueError("action replay fingerprint is inconsistent")
            result = self._stored_model(
                TransitionResult,
                canonical_json(replay["result"]),
                "action replay transition result",
            )
        except ProjectControlError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise ProjectControlError(
                ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                "The stored action replay failed integrity validation.",
            ) from error
        event_row = connection.execute(
            "SELECT event_type, request_id, previous_state_version, "
            "resulting_state_version, event_json FROM project_events "
            "WHERE event_id = ? AND project_run_id = ?",
            (row["event_id"], command.project_run_id),
        ).fetchone()
        if event_row is None:
            raise ProjectControlError(
                ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                "The stored action replay references a missing project event.",
            )
        event = self._stored_model(ProjectEvent, event_row["event_json"], "action replay event")
        consistent = (
            result.project_run_id == command.project_run_id
            and result.command_type == command.command_type
            and result.idempotency_key == command.idempotency_key
            and result.event_id == str(row["event_id"])
            and result.previous_state_version == int(row["state_version_before"])
            and result.state_version == int(row["state_version_after"])
            and event.project_run_id == command.project_run_id
            and event.event_type == command.command_type.value
            and event.request_id == command.idempotency_key
            and event.actor_id == command.actor_id
            and event.conversation_id == command.conversation_id
            and event.workspace_id == command.workspace_id
            and int(event_row["previous_state_version"]) == result.previous_state_version
            and int(event_row["resulting_state_version"]) == result.state_version
        )
        if not consistent:
            raise ProjectControlError(
                ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                "The stored action replay does not match its canonical action event.",
            )
        return result.model_copy(update={"replayed": True})

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
            authority_hash=authority_hash,
            artifact_id=command.artifact_id, artifact_type=command.artifact_type,
            artifact_hash=command.artifact_hash,
            artifact_binding_hash=command.artifact_binding_hash,
            created_at=self._now(),
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

    def _active_attempt(
        self, connection: sqlite3.Connection, run: ProjectRun
    ) -> ExecutionAttempt | None:
        row = connection.execute(
            "SELECT attempt_json FROM project_execution_attempts WHERE project_run_id = ? AND status IN ('pending', 'active', 'cancelling') ORDER BY started_at DESC, execution_attempt_id DESC LIMIT 1",
            (run.project_run_id,),
        ).fetchone()
        return (
            self._stored_model(ExecutionAttempt, row["attempt_json"], "attempt")
            if row is not None else None
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

    def _invalidate_manual_evidence(
        self,
        connection: sqlite3.Connection,
        project_run_id: str,
        cause: str,
        *,
        criterion_ids: set[str] | None = None,
        superseding: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        rows = connection.execute(
            "SELECT e.evidence_id, e.criterion_id, e.evidence_hash, e.evidence_json "
            "FROM project_manual_evidence e "
            "LEFT JOIN project_manual_evidence_invalidations i "
            "ON i.evidence_id = e.evidence_id "
            "WHERE e.project_run_id = ? AND i.evidence_id IS NULL "
            "ORDER BY e.created_at, e.evidence_id",
            (project_run_id,),
        ).fetchall()
        now = self._now()
        superseding = superseding or {}
        created: list[str] = []
        for row in rows:
            criterion_id = str(row["criterion_id"])
            if criterion_ids is not None and criterion_id not in criterion_ids:
                continue
            try:
                evidence = json.loads(str(row["evidence_json"]))
                criterion_hash = str(evidence["criterion_hash"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ProjectControlError(
                    ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                    "Stored manual evidence failed integrity validation.",
                ) from error
            identity = {
                "project_run_id": project_run_id,
                "evidence_id": str(row["evidence_id"]),
                "criterion_id": criterion_id,
                "criterion_hash": criterion_hash,
                "cause": cause,
                "superseding": superseding,
            }
            invalidation = ManualEvidenceInvalidation(
                invalidation_id=content_hash(identity),
                project_run_id=project_run_id,
                evidence_id=str(row["evidence_id"]),
                evidence_hash=str(row["evidence_hash"]),
                criterion_id=criterion_id,
                criterion_hash=criterion_hash,
                cause=cause,
                superseding_plan_revision_id=_text(superseding.get("plan_revision_id")),
                superseding_scope_revision_id=_text(superseding.get("scope_revision_id")),
                superseding_manifest_hash=_text(superseding.get("manifest_hash")),
                superseding_execution_attempt_id=_text(superseding.get("execution_attempt_id")),
                superseding_artifact_id=_text(superseding.get("artifact_id")),
                created_at=now,
            )
            connection.execute(
                "INSERT OR IGNORE INTO project_manual_evidence_invalidations "
                "(invalidation_id, project_run_id, evidence_id, criterion_id, criterion_hash, "
                "cause, superseding_plan_revision_id, superseding_scope_revision_id, "
                "superseding_manifest_hash, superseding_execution_attempt_id, "
                "superseding_artifact_id, invalidation_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invalidation.invalidation_id, invalidation.project_run_id,
                    invalidation.evidence_id, invalidation.criterion_id,
                    invalidation.criterion_hash, invalidation.cause,
                    invalidation.superseding_plan_revision_id,
                    invalidation.superseding_scope_revision_id,
                    invalidation.superseding_manifest_hash,
                    invalidation.superseding_execution_attempt_id,
                    invalidation.superseding_artifact_id,
                    invalidation.model_dump_json(), now.isoformat(),
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0]:
                created.append(invalidation.invalidation_id)
        return tuple(created)

    def _manual_evidence_history(
        self, connection: sqlite3.Connection, project_run_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT e.evidence_json, e.status AS stored_status, i.invalidation_json "
            "FROM project_manual_evidence e "
            "LEFT JOIN project_manual_evidence_invalidations i "
            "ON i.evidence_id = e.evidence_id "
            "WHERE e.project_run_id = ? ORDER BY e.created_at, e.evidence_id",
            (project_run_id,),
        ).fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            try:
                evidence = json.loads(str(row["evidence_json"]))
                if not isinstance(evidence, dict):
                    raise TypeError("evidence is not an object")
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ProjectControlError(
                    ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                    "Stored manual evidence failed integrity validation.",
                ) from error
            evidence = {**evidence, "stored_status": str(row["stored_status"])}
            if row["invalidation_json"] is not None:
                invalidation = self._stored_model(
                    ManualEvidenceInvalidation,
                    row["invalidation_json"],
                    "manual evidence invalidation",
                )
                evidence.update({
                    "status": "verification_invalidated",
                    "invalidation": invalidation.model_dump(mode="json"),
                })
            else:
                evidence["status"] = str(row["stored_status"])
            history.append(_bounded_object(evidence))
        return history

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

    def _advance_after_canonical_verification(
        self,
        connection: sqlite3.Connection,
        run: ProjectRun,
        criterion_id: str,
    ) -> ProjectRun:
        active_work_unit_id = next(
            (
                key for key, value in run.work_unit_state.items()
                if value.get("status") == "in_progress"
            ),
            None,
        )
        if active_work_unit_id is None:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "Canonical verification has no active work unit.",
            )
        plan = self._load_plan(connection, run.current_plan_revision_id)
        work_unit = next(
            (
                item for item in plan.work_units
                if str(item.get("work_unit_id") or item.get("id") or "")
                == active_work_unit_id
            ),
            None,
        )
        if work_unit is None:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "The active work unit is not in the current plan.",
            )
        configured = (
            work_unit.get("acceptance_criteria_ids")
            or work_unit.get("criterion_references")
            or work_unit.get("acceptance_criteria")
        )
        relevant = (
            {str(item) for item in configured}
            if isinstance(configured, (list, tuple))
            else None
        )
        required_ids = [
            str(item.get("criterion_id") or item.get("id") or "")
            for item in plan.acceptance_criteria
            if item.get("required", True) is not False
            and (
                relevant is None
                or str(item.get("criterion_id") or item.get("id") or "") in relevant
            )
        ]
        if criterion_id not in required_ids and required_ids:
            return run.model_copy(update={"pending_user_action": "request_verification"})
        if any(
            (run.verification_state.get(item) or {}).get("outcome") != "passed"
            for item in required_ids
        ):
            return run.model_copy(update={
                "lifecycle_status": ProjectLifecycle.VERIFICATION_PENDING,
                "pending_user_action": "request_verification",
            })
        state = _copy(run.work_unit_state)
        state[active_work_unit_id] = {
            **state[active_work_unit_id],
            "status": "completed",
        }
        self._finish_active_attempt(
            connection,
            run,
            ExecutionAttemptType.WORK_UNIT,
            succeeded=True,
        )
        all_complete = bool(state) and all(
            item.get("status") == "completed" for item in state.values()
        )
        return run.model_copy(update={
            "work_unit_state": state,
            "lifecycle_status": (
                ProjectLifecycle.VERIFICATION_PENDING
                if all_complete
                else ProjectLifecycle.READY_FOR_WORK
            ),
            "pending_user_action": (
                "request_handoff" if all_complete else "begin_work_unit"
            ),
        })

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
                if evidence.get("outcome") != "passed" or not evidence.get("evidence_id"):
                    raise ProjectControlError(
                        ProjectControlErrorCode.STALE_VERIFICATION,
                        "A required manual criterion lacks fresh accepted evidence.",
                    )
                if (
                    evidence.get("plan_revision_id") != run.current_plan_revision_id
                    or evidence.get("scope_revision_id") != run.current_scope_revision_id
                    or evidence.get("manifest_hash") != run.current_manifest_hash
                    or evidence.get("criterion_hash") != content_hash(criterion)
                ):
                    raise ProjectControlError(
                        ProjectControlErrorCode.STALE_VERIFICATION,
                        "Required manual evidence is stale.",
                    )
                evidence_row = connection.execute(
                    "SELECT e.status, e.evidence_json, i.invalidation_id "
                    "FROM project_manual_evidence e "
                    "LEFT JOIN project_manual_evidence_invalidations i "
                    "ON i.evidence_id = e.evidence_id "
                    "WHERE e.evidence_id = ? AND e.project_run_id = ? "
                    "AND e.criterion_id = ?",
                    (evidence.get("evidence_id"), run.project_run_id, criterion_id),
                ).fetchone()
                if (
                    evidence_row is None
                    or str(evidence_row["status"]) != "passed"
                    or evidence_row["invalidation_id"] is not None
                ):
                    raise ProjectControlError(
                        ProjectControlErrorCode.STALE_VERIFICATION,
                        "Required manual evidence has been invalidated.",
                    )
                try:
                    stored_evidence = json.loads(str(evidence_row["evidence_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise ProjectControlError(
                        ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                        "Stored manual evidence failed integrity validation.",
                    ) from error
                if (
                    stored_evidence.get("criterion_hash") != content_hash(criterion)
                    or stored_evidence.get("plan_revision_id") != run.current_plan_revision_id
                    or stored_evidence.get("scope_revision_id") != run.current_scope_revision_id
                    or stored_evidence.get("manifest_hash") != run.current_manifest_hash
                    or stored_evidence.get("execution_attempt_id")
                    != evidence.get("execution_attempt_id")
                    or stored_evidence.get("verification_artifact_id")
                    != evidence.get("verification_artifact_id")
                ):
                    raise ProjectControlError(
                        ProjectControlErrorCode.STALE_VERIFICATION,
                        "Required manual evidence persistence bindings are stale.",
                    )
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
            ProjectCommandType.APPROVE_PLAN: frozenset({ProjectArtifactType.PLAN}),
            ProjectCommandType.APPROVE_PATCH: frozenset({
                ProjectArtifactType.PATCH_PREVIEW,
                ProjectArtifactType.REPAIR_PREVIEW,
            }),
            ProjectCommandType.RECORD_ROLLBACK_PREVIEW: frozenset({ProjectArtifactType.ROLLBACK_PREVIEW}),
            ProjectCommandType.APPROVE_ROLLBACK: frozenset({ProjectArtifactType.ROLLBACK_PREVIEW}),
            ProjectCommandType.BEGIN_WORK_UNIT: frozenset({ProjectArtifactType.PATCH_PREVIEW}),
            ProjectCommandType.INITIATE_REPAIR: frozenset({ProjectArtifactType.REPAIR_PREVIEW}),
            ProjectCommandType.RECORD_PATCH_PREVIEW: frozenset({
                ProjectArtifactType.PATCH_PREVIEW,
                ProjectArtifactType.REPAIR_PREVIEW,
            }),
            ProjectCommandType.RECORD_COMMAND_PREVIEW: frozenset({ProjectArtifactType.COMMAND_PREVIEW}),
            ProjectCommandType.RECORD_VERIFIER_RESULT: frozenset({ProjectArtifactType.VERIFIER_RESULT}),
            ProjectCommandType.SUBMIT_MANUAL_EVIDENCE: frozenset({ProjectArtifactType.MANUAL_EVIDENCE}),
            ProjectCommandType.REQUEST_HANDOFF: frozenset({ProjectArtifactType.HANDOFF}),
            ProjectCommandType.FINALIZE_PROJECT: frozenset({ProjectArtifactType.HANDOFF}),
            ProjectCommandType.MARK_BLOCKED: frozenset({ProjectArtifactType.COORDINATOR_DECISION}),
            ProjectCommandType.RECORD_PATCH_RESULT: frozenset({ProjectArtifactType.EXECUTION_RESULT}),
            ProjectCommandType.RECORD_COMMAND_RESULT: frozenset({ProjectArtifactType.EXECUTION_RESULT}),
            ProjectCommandType.RECORD_ROLLBACK: frozenset({ProjectArtifactType.EXECUTION_RESULT}),
            ProjectCommandType.ACKNOWLEDGE_EXECUTION_CANCELLATION: frozenset({
                ProjectArtifactType.EXECUTION_RESULT
            }),
            ProjectCommandType.RECOVER_ATTEMPT: frozenset({ProjectArtifactType.EXECUTION_RESULT}),
        }
        allowed = expected.get(command.command_type)
        if command.command_type == ProjectCommandType.APPROVE_COMMAND:
            allowed = frozenset({ProjectArtifactType.COMMAND_PREVIEW})
        supplied = command.artifact_id is not None
        required = (
            run.canonical_generation == "canonical"
            and allowed is not None
            and command.command_type not in {
                ProjectCommandType.BEGIN_WORK_UNIT,
                ProjectCommandType.RECOVER_ATTEMPT,
                ProjectCommandType.RECORD_ROLLBACK_PREVIEW,
            }
        )
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
        expected_attempt = _text(command.payload.get("execution_attempt_id"))
        if binding.execution_attempt_id is not None:
            if not expected_attempt:
                expected_attempt = _text(command.authority_scope.get("execution_attempt_id"))
            if binding.execution_attempt_id != expected_attempt:
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    "The execution-result artifact is bound to a different attempt.",
                )
        for field in ("work_unit_id", "criterion_id", "criterion_hash"):
            bound = getattr(binding, field, None)
            if bound is not None and bound != _text(command.payload.get(field)):
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    f"The artifact is bound to a different {field.replace('_', ' ')}.",
                )
        approval_commands = {
            ProjectCommandType.APPROVE_PLAN,
            ProjectCommandType.APPROVE_PATCH,
            ProjectCommandType.APPROVE_COMMAND,
            ProjectCommandType.APPROVE_ROLLBACK,
        }
        if command.command_type in approval_commands:
            current_artifact_id = run.current_artifact_ids.get(artifact.artifact_type.value)
            if current_artifact_id != artifact.artifact_id:
                raise ProjectControlError(
                    ProjectControlErrorCode.NON_CURRENT_ARTIFACT,
                    "The approval must reference the current artifact of its type, not a superseded one.",
                )
            # A plan artifact is the immutable input that creates its plan
            # revision, so it cannot self-reference that revision. The current
            # artifact pointer plus the command's exact plan revision supplies
            # that link. All later approval artifacts must carry it directly.
            plan_binding_matches = (
                artifact.artifact_type == ProjectArtifactType.PLAN
                and binding.plan_revision_id is None
            ) or binding.plan_revision_id == run.current_plan_revision_id
            if (
                not plan_binding_matches
                or binding.scope_revision_id != run.current_scope_revision_id
                or binding.manifest_hash != run.current_manifest_hash
            ):
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    "Approval artifacts require complete bindings to the current plan, scope, and manifest.",
                )
            bound_object_fields = {
                "work_unit_id": artifact.payload.get("work_unit_id"),
                "criterion_id": artifact.payload.get("criterion_id"),
                "patch_id": artifact.payload.get("patch_id"),
                "command_id": artifact.payload.get("command_id"),
                "rollback_id": artifact.payload.get("rollback_id"),
            }
            for field, artifact_value in bound_object_fields.items():
                if artifact_value is None:
                    continue
                requested_value = command.payload.get(field)
                if requested_value is None:
                    requested_value = command.authority_scope.get(field)
                if str(requested_value or "") != str(artifact_value):
                    raise ProjectControlError(
                        ProjectControlErrorCode.INVALID_COMMAND,
                        f"The approval is not bound to the artifact's exact {field}.",
                    )
            coordinator_bound_types = {
                ProjectArtifactType.PATCH_PREVIEW,
                ProjectArtifactType.REPAIR_PREVIEW,
                ProjectArtifactType.COMMAND_PREVIEW,
                ProjectArtifactType.VERIFIER_RESULT,
            }
            if (
                artifact.artifact_type in coordinator_bound_types
                and not binding.coordinator_intent_id
            ):
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    "Coordinator-produced approval artifacts require an exact coordinator-intent binding.",
                )
        return artifact

    def _with_failure_artifact(
        self,
        run: ProjectRun,
        payload: dict[str, Any],
    ) -> ProjectRun:
        failure_artifact_id = _text(payload.get("failure_artifact_id"))
        if not failure_artifact_id:
            if run.canonical_generation == "canonical":
                raise ProjectControlError(
                    ProjectControlErrorCode.INVALID_COMMAND,
                    "Canonical domain failures require bounded failure evidence.",
                )
            return run
        if self.artifact_store is None:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "Failure evidence storage is unavailable.",
            )
        from backend.app.project_artifacts.contracts import ProjectArtifactType
        from backend.app.project_artifacts.store import ProjectArtifactStoreError

        try:
            failure = self.artifact_store.verify(failure_artifact_id)
        except ProjectArtifactStoreError as exc:
            raise ProjectControlError(
                ProjectControlErrorCode.CORRUPTED_STORED_STATE,
                "The failure-evidence artifact is missing or corrupted.",
            ) from exc
        binding = failure.binding
        if (
            failure.artifact_type != ProjectArtifactType.FAILURE_EVIDENCE
            or binding.project_run_id != run.project_run_id
            or binding.plan_revision_id != run.current_plan_revision_id
            or binding.scope_revision_id != run.current_scope_revision_id
            or binding.manifest_hash != run.current_manifest_hash
        ):
            raise ProjectControlError(
                ProjectControlErrorCode.STALE_VERIFICATION,
                "Failure evidence is bound to stale project state.",
            )
        updated = self._with_artifact(run, failure)
        return updated.model_copy(update={
            "repair_state": {
                **updated.repair_state,
                "failure_artifact_id": failure.artifact_id,
                "failure_artifact_hash": failure.content_hash,
                "status": "repair_required",
            }
        })

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
            "WHERE project_run_id = ? AND status IN ('pending', 'active', 'cancelling') "
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
        cancellation_row = connection.execute(
            "SELECT cancellation_json FROM project_execution_cancellations "
            "WHERE project_run_id = ? ORDER BY created_at DESC, cancellation_id DESC LIMIT 1",
            (run.project_run_id,),
        ).fetchone()
        cancellation = (
            self._stored_model(
                ExecutionCancellation,
                cancellation_row["cancellation_json"],
                "execution cancellation",
            )
            if cancellation_row is not None else None
        )
        max_event_sequence = int(connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM project_events WHERE project_run_id = ?",
            (run.project_run_id,),
        ).fetchone()[0])
        projection_row = connection.execute(
            "SELECT last_event_sequence, status, failure_message "
            "FROM project_projection_checkpoints WHERE project_run_id = ?",
            (run.project_run_id,),
        ).fetchone()
        projected_sequence = int(projection_row["last_event_sequence"]) if projection_row else 0
        manual_evidence_history = self._manual_evidence_history(
            connection, run.project_run_id
        )
        invalidated_manual_count = sum(
            1
            for evidence in manual_evidence_history
            if evidence.get("status") == "verification_invalidated"
        )
        return ProjectReadModel(
            project_run_id=run.project_run_id, conversation_id=run.conversation_id,
            workspace_id=run.workspace_id, actor_id=run.actor_id,
            repository_root_fingerprint=run.repository_root_fingerprint,
            lifecycle_state=run.lifecycle_status, plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id, manifest_hash=run.current_manifest_hash,
            manifest_complete=run.manifest_complete,
            approval_state="approved" if approval_fresh else ("reapproval_required" if run.requires_reapproval else "not_approved"),
            approval_fresh=approval_fresh, current_work_unit=active,
            progress={"completed_work_units": completed, "total_work_units": len(run.work_unit_state)},
            pending_user_action=run.pending_user_action,
            verification_summary={
                "passed": outcomes.count("passed"), "failed": outcomes.count("failed"),
                "manual_required": outcomes.count("manual_required") + outcomes.count("manual_evidence_required"),
                "manual_evidence_required": outcomes.count("manual_evidence_required"),
                "stale": outcomes.count("verification_stale") + outcomes.count("stale"),
                "invalidated": invalidated_manual_count,
                "total": len(outcomes),
            },
            criterion_states={
                criterion_id: _bounded_object(evidence)
                for criterion_id, evidence in run.verification_state.items()
            },
            manual_evidence_history=tuple(manual_evidence_history),
            repair_state=_copy(run.repair_state),
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
            execution_cancellation_id=(cancellation.cancellation_id if cancellation else None),
            execution_cancellation_status=(cancellation.status.value if cancellation else None),
            projection_status=(str(projection_row["status"]) if projection_row else "pending"),
            projection_lag=max(0, max_event_sequence - projected_sequence),
            projection_failure_classification=(
                _text(projection_row["failure_message"]) or None
                if projection_row else None
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
            current_rollback_preview_artifact_id=run.current_artifact_ids.get("rollback_preview"),
            current_rollback_preview_artifact_hash=run.current_artifact_hashes.get("rollback_preview"),
            current_verifier_result_artifact_id=run.current_artifact_ids.get("verifier_result"),
            current_verifier_result_artifact_hash=run.current_artifact_hashes.get("verifier_result"),
            current_repair_preview_artifact_id=run.current_artifact_ids.get("repair_preview"),
            current_repair_preview_artifact_hash=run.current_artifact_hashes.get("repair_preview"),
            current_handoff_artifact_id=run.current_artifact_ids.get("handoff"),
            current_handoff_artifact_hash=run.current_artifact_hashes.get("handoff"),
            current_execution_result_artifact_id=run.current_artifact_ids.get("execution_result"),
            current_execution_result_artifact_hash=run.current_artifact_hashes.get("execution_result"),
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

    def _load_execution_cancellation(
        self, connection: sqlite3.Connection, cancellation_id: str
    ) -> ExecutionCancellation:
        row = connection.execute(
            "SELECT cancellation_json FROM project_execution_cancellations WHERE cancellation_id = ?",
            (cancellation_id,),
        ).fetchone()
        if row is None:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND,
                "Execution cancellation not found.",
            )
        return self._stored_model(
            ExecutionCancellation, row["cancellation_json"], "execution cancellation"
        )

    def _update_cancellation_delivery(
        self,
        cancellation_id: str,
        *,
        status: ExecutionCancellationStatus,
        failure_classification: str | None = None,
    ) -> ExecutionCancellation:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._load_execution_cancellation(connection, cancellation_id)
            if current.status == ExecutionCancellationStatus.ACKNOWLEDGED:
                connection.execute("COMMIT")
                return current
            now = self._now()
            updated = current.model_copy(update={
                "status": status,
                "failure_classification": failure_classification,
                "updated_at": now,
                "acknowledged_at": None,
            })
            connection.execute(
                "UPDATE project_execution_cancellations SET status = ?, cancellation_json = ?, "
                "updated_at = ? WHERE cancellation_id = ?",
                (status.value, updated.model_dump_json(), now.isoformat(), cancellation_id),
            )
            connection.execute("COMMIT")
            return updated

    def _begin_execution_cancellation(
        self,
        connection: sqlite3.Connection,
        run: ProjectRun,
        *,
        requested_by: str,
        reason: str,
    ) -> ExecutionCancellation | None:
        row = connection.execute(
            "SELECT attempt_json FROM project_execution_attempts WHERE project_run_id = ? "
            "AND status IN ('pending', 'active', 'cancelling') "
            "ORDER BY started_at DESC, execution_attempt_id DESC LIMIT 1",
            (run.project_run_id,),
        ).fetchone()
        if row is None:
            return None
        attempt = self._stored_model(ExecutionAttempt, row["attempt_json"], "attempt")
        existing = connection.execute(
            "SELECT cancellation_json FROM project_execution_cancellations "
            "WHERE project_run_id = ? AND execution_attempt_id = ?",
            (run.project_run_id, attempt.execution_attempt_id),
        ).fetchone()
        if existing is not None:
            return self._stored_model(
                ExecutionCancellation,
                existing["cancellation_json"],
                "execution cancellation",
            )
        dispatch_row = connection.execute(
            "SELECT dispatch_json, worker_request_id, status FROM project_execution_dispatches "
            "WHERE execution_attempt_id = ?",
            (attempt.execution_attempt_id,),
        ).fetchone()
        worker_request_id = (
            _text(dispatch_row["worker_request_id"]) if dispatch_row is not None else None
        ) or None
        if worker_request_id is None:
            worker_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_worker_requests'"
            ).fetchone()
            if worker_table is not None:
                worker_row = connection.execute(
                    "SELECT worker_request_id FROM project_worker_requests WHERE execution_attempt_id = ?",
                    (attempt.execution_attempt_id,),
                ).fetchone()
                worker_request_id = (
                    _text(worker_row["worker_request_id"]) if worker_row is not None else None
                ) or None
        if worker_request_id is None and (
            dispatch_row is None or str(dispatch_row["status"]) == "pending"
        ):
            self._cancel_pending_dispatches(
                connection,
                run.project_run_id,
                execution_attempt_id=attempt.execution_attempt_id,
            )
            self._finish_cancelled_attempt(
                connection,
                attempt.execution_attempt_id,
                status=ExecutionAttemptStatus.CANCELLED,
                failure_classification="cancelled_before_dispatch",
            )
            return None
        cancellation = build_execution_cancellation(
            project_run_id=run.project_run_id,
            execution_attempt_id=attempt.execution_attempt_id,
            worker_request_id=worker_request_id,
            requested_by=requested_by,
            reason=reason,
            created_at=self._now(),
        )
        cancelling_attempt = attempt.model_copy(update={
            "status": ExecutionAttemptStatus.CANCELLING,
            "failure_classification": "cancellation_requested",
        })
        connection.execute(
            "UPDATE project_execution_attempts SET status = ?, attempt_json = ? "
            "WHERE execution_attempt_id = ? AND status IN ('pending', 'active')",
            (
                cancelling_attempt.status.value,
                cancelling_attempt.model_dump_json(),
                cancelling_attempt.execution_attempt_id,
            ),
        )
        connection.execute(
            "INSERT INTO project_execution_cancellations "
            "(cancellation_id, project_run_id, execution_attempt_id, worker_request_id, status, "
            "request_hash, schema_version, cancellation_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cancellation.cancellation_id,
                cancellation.project_run_id,
                cancellation.execution_attempt_id,
                cancellation.worker_request_id,
                cancellation.status.value,
                cancellation.request_hash,
                cancellation.schema_version,
                cancellation.model_dump_json(),
                cancellation.created_at.isoformat(),
                cancellation.updated_at.isoformat(),
            ),
        )
        return cancellation

    def _terminal_worker_cancellation_status(
        self, connection: sqlite3.Connection, cancellation: ExecutionCancellation
    ) -> str | None:
        if not cancellation.worker_request_id:
            return "cancelled"
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_worker_requests'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            "SELECT status FROM project_worker_requests WHERE worker_request_id = ?",
            (cancellation.worker_request_id,),
        ).fetchone()
        if row is None:
            return None
        status = str(row["status"])
        return status if status in {
            "cancelled", "interrupted", "timed_out", "failed", "succeeded"
        } else None

    def _finish_cancelled_attempt(
        self,
        connection: sqlite3.Connection,
        execution_attempt_id: str,
        *,
        status: ExecutionAttemptStatus,
        failure_classification: str,
    ) -> None:
        row = connection.execute(
            "SELECT attempt_json FROM project_execution_attempts WHERE execution_attempt_id = ?",
            (execution_attempt_id,),
        ).fetchone()
        if row is None:
            raise ProjectControlError(
                ProjectControlErrorCode.INVALID_COMMAND, "Execution attempt not found."
            )
        attempt = self._stored_model(ExecutionAttempt, row["attempt_json"], "attempt")
        if attempt.status in {
            ExecutionAttemptStatus.CANCELLED,
            ExecutionAttemptStatus.INTERRUPTED,
        }:
            return
        now = self._now()
        finished = attempt.model_copy(update={
            "status": status,
            "failure_classification": failure_classification,
            "finished_at": now,
        })
        connection.execute(
            "UPDATE project_execution_attempts SET status = ?, attempt_json = ?, finished_at = ? "
            "WHERE execution_attempt_id = ?",
            (
                finished.status.value,
                finished.model_dump_json(),
                now.isoformat(),
                execution_attempt_id,
            ),
        )

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
