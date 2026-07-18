from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from backend.app.project_control.contracts import (
    EXECUTION_ATTEMPT_VERSION,
    PROJECT_RUN_VERSION,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionAttemptType,
    ProjectRun,
    canonical_json,
    content_hash,
)
from backend.app.project_workers.contracts import (
    WORKER_EVENT_VERSION,
    WORKER_REQUEST_VERSION,
    ProjectWorkerEvent,
    ProjectWorkerRequest,
    TERMINAL_WORKER_STATUSES,
    WorkerCompletion,
    WorkerCompletionOutcome,
    WorkerEnqueueCommand,
    WorkerEventType,
    WorkerLease,
    WorkerRequestStatus,
)
from backend.app.project_workers.errors import ProjectWorkerError, ProjectWorkerErrorCode


ModelT = TypeVar("ModelT", bound=BaseModel)
_UNSUPPORTED = object()


class ProjectWorkerQueue:
    """Durable, lease-based queue bound to canonical execution attempts."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_worker_requests (
                    worker_request_id TEXT PRIMARY KEY,
                    project_run_id TEXT NOT NULL,
                    execution_attempt_id TEXT NOT NULL UNIQUE,
                    attempt_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    available_at TEXT NOT NULL,
                    delivery_count INTEGER NOT NULL CHECK(delivery_count >= 0),
                    max_deliveries INTEGER NOT NULL CHECK(max_deliveries >= 1),
                    lease_owner TEXT,
                    lease_token_hash TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    cancellation_requested_at TEXT,
                    failure_classification TEXT,
                    request_hash TEXT NOT NULL,
                    enqueue_idempotency_key TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    canonical_reconciled_at TEXT,
                    UNIQUE(project_run_id, enqueue_idempotency_key),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id),
                    FOREIGN KEY(execution_attempt_id) REFERENCES project_execution_attempts(execution_attempt_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_worker_claim
                    ON project_worker_requests(status, available_at, priority, created_at);
                CREATE INDEX IF NOT EXISTS idx_project_worker_project
                    ON project_worker_requests(project_run_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_project_worker_lease
                    ON project_worker_requests(status, lease_expires_at);

                CREATE TABLE IF NOT EXISTS project_worker_events (
                    event_id TEXT PRIMARY KEY,
                    worker_request_id TEXT NOT NULL,
                    project_run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence >= 1),
                    event_type TEXT NOT NULL,
                    worker_id TEXT,
                    schema_version TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(worker_request_id, sequence),
                    FOREIGN KEY(worker_request_id) REFERENCES project_worker_requests(worker_request_id),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_worker_events_request
                    ON project_worker_events(worker_request_id, sequence);

                CREATE TABLE IF NOT EXISTS project_worker_idempotency (
                    project_run_id TEXT NOT NULL,
                    operation_key TEXT NOT NULL,
                    operation_type TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_run_id, operation_key),
                    FOREIGN KEY(project_run_id) REFERENCES project_runs(project_run_id)
                );
                """
            )

    def enqueue(self, command: WorkerEnqueueCommand | dict[str, Any]) -> ProjectWorkerRequest:
        parsed = self._parse(WorkerEnqueueCommand, command, "worker enqueue command")
        request_hash = content_hash(parsed.model_dump(mode="json"))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_replay(
                connection,
                parsed.project_run_id,
                parsed.idempotency_key,
                "enqueue",
                request_hash,
                ProjectWorkerRequest,
            )
            if replay is not None:
                connection.execute("COMMIT")
                return replay

            self._validate_enqueue_bindings(connection, parsed)
            existing = connection.execute(
                "SELECT worker_request_id FROM project_worker_requests WHERE execution_attempt_id = ?",
                (parsed.execution_attempt_id,),
            ).fetchone()
            if existing is not None:
                raise ProjectWorkerError(
                    ProjectWorkerErrorCode.ATTEMPT_ALREADY_BOUND,
                    "The canonical execution attempt is already bound to a worker request.",
                    metadata={"worker_request_id": str(existing["worker_request_id"])},
                )

            now = self._now()
            available_at = self._as_utc(parsed.available_at or now)
            payload = self._bounded_object(parsed.payload)
            authority = json.loads(json.dumps(parsed.authority))
            request_id = f"worker-{content_hash([parsed.project_run_id, parsed.execution_attempt_id, parsed.idempotency_key])[:24]}"
            request = ProjectWorkerRequest(
                worker_request_id=request_id,
                project_run_id=parsed.project_run_id,
                execution_attempt_id=parsed.execution_attempt_id,
                attempt_type=parsed.attempt_type,
                conversation_id=parsed.conversation_id,
                workspace_id=parsed.workspace_id,
                repository_root=parsed.repository_root,
                repository_root_fingerprint=parsed.repository_root_fingerprint,
                actor_id=parsed.actor_id,
                plan_revision_id=parsed.plan_revision_id,
                scope_revision_id=parsed.scope_revision_id,
                manifest_hash=parsed.manifest_hash.lower(),
                expected_project_state_version=parsed.expected_project_state_version,
                authority=authority,
                payload=payload,
                payload_hash=content_hash(payload),
                request_hash=request_hash,
                enqueue_idempotency_key=parsed.idempotency_key,
                priority=parsed.priority,
                limits=parsed.limits,
                status=WorkerRequestStatus.QUEUED,
                available_at=available_at,
                delivery_count=0,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """
                INSERT INTO project_worker_requests (
                    worker_request_id, project_run_id, execution_attempt_id, attempt_type,
                    status, priority, available_at, delivery_count, max_deliveries,
                    request_hash, enqueue_idempotency_key, schema_version, request_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.worker_request_id,
                    request.project_run_id,
                    request.execution_attempt_id,
                    request.attempt_type.value,
                    request.status.value,
                    request.priority,
                    request.available_at.isoformat(),
                    request.delivery_count,
                    request.limits.max_deliveries,
                    request.request_hash,
                    request.enqueue_idempotency_key,
                    request.schema_version,
                    request.model_dump_json(),
                    request.created_at.isoformat(),
                    request.updated_at.isoformat(),
                ),
            )
            self._append_event(connection, request, WorkerEventType.ENQUEUED, metadata={
                "attempt_type": request.attempt_type.value,
                "expected_project_state_version": request.expected_project_state_version,
            })
            self._store_idempotency(
                connection,
                request.project_run_id,
                request.enqueue_idempotency_key,
                "enqueue",
                request_hash,
                request.model_dump_json(),
                now,
            )
            connection.execute("COMMIT")
            return request
        except ProjectWorkerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except (sqlite3.DatabaseError, sqlite3.IntegrityError) as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ProjectWorkerError(
                ProjectWorkerErrorCode.PERSISTENCE_CONFLICT,
                "The worker request could not be persisted atomically.",
            ) from error
        finally:
            connection.close()

    def get(self, worker_request_id: str) -> ProjectWorkerRequest:
        with self._connect() as connection:
            return self._load_request(connection, worker_request_id)

    def list_for_project(self, project_run_id: str) -> list[ProjectWorkerRequest]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT request_json FROM project_worker_requests WHERE project_run_id = ? ORDER BY created_at, worker_request_id",
                (project_run_id,),
            ).fetchall()
            return [self._stored_model(ProjectWorkerRequest, row["request_json"], "worker request") for row in rows]

    def list_events(self, worker_request_id: str) -> list[ProjectWorkerEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM project_worker_events WHERE worker_request_id = ? ORDER BY sequence",
                (worker_request_id,),
            ).fetchall()
            return [self._stored_model(ProjectWorkerEvent, row["event_json"], "worker event") for row in rows]

    def claim_next(
        self,
        worker_id: str,
        supported_attempt_types: Iterable[ExecutionAttemptType | str],
        *,
        lease_seconds: int | None = None,
        now: datetime | None = None,
    ) -> WorkerLease | None:
        worker = self._required_text(worker_id, "worker_id", 160)
        supported = tuple(sorted({ExecutionAttemptType(item).value for item in supported_attempt_types}))
        if not supported:
            return None
        current_time = self._as_utc(now or self._now())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in supported)
            row = connection.execute(
                f"""
                SELECT request_json
                FROM project_worker_requests
                WHERE status = 'queued'
                  AND available_at <= ?
                  AND cancellation_requested_at IS NULL
                  AND delivery_count < max_deliveries
                  AND attempt_type IN ({placeholders})
                ORDER BY priority DESC, created_at ASC, worker_request_id ASC
                LIMIT 1
                """,
                (current_time.isoformat(), *supported),
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            request = self._stored_model(ProjectWorkerRequest, row["request_json"], "worker request")
            effective_lease = lease_seconds if lease_seconds is not None else request.limits.lease_seconds
            if effective_lease < 5 or effective_lease > 3600:
                raise ProjectWorkerError(ProjectWorkerErrorCode.INVALID_REQUEST, "lease_seconds must be between 5 and 3600.")
            token = secrets.token_urlsafe(32)
            token_hash = self._token_hash(token)
            expires_at = current_time + timedelta(seconds=effective_lease)
            leased = request.model_copy(update={
                "status": WorkerRequestStatus.LEASED,
                "delivery_count": request.delivery_count + 1,
                "lease_owner": worker,
                "lease_expires_at": expires_at,
                "heartbeat_at": current_time,
                "updated_at": current_time,
            })
            cursor = connection.execute(
                """
                UPDATE project_worker_requests
                SET status = ?, delivery_count = ?, lease_owner = ?, lease_token_hash = ?,
                    lease_expires_at = ?, heartbeat_at = ?, request_json = ?, updated_at = ?
                WHERE worker_request_id = ? AND status = 'queued'
                """,
                (
                    leased.status.value,
                    leased.delivery_count,
                    worker,
                    token_hash,
                    expires_at.isoformat(),
                    current_time.isoformat(),
                    leased.model_dump_json(),
                    current_time.isoformat(),
                    leased.worker_request_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ProjectWorkerError(ProjectWorkerErrorCode.NOT_CLAIMABLE, "Another worker claimed the request first.")
            self._append_event(connection, leased, WorkerEventType.LEASED, worker_id=worker, metadata={
                "lease_expires_at": expires_at.isoformat(),
                "delivery_count": leased.delivery_count,
            })
            connection.execute("COMMIT")
            return WorkerLease(request=leased, lease_token=token)
        except ProjectWorkerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except (sqlite3.DatabaseError, sqlite3.IntegrityError) as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ProjectWorkerError(ProjectWorkerErrorCode.PERSISTENCE_CONFLICT, "The worker lease could not be acquired atomically.") from error
        finally:
            connection.close()

    def heartbeat(
        self,
        worker_request_id: str,
        worker_id: str,
        lease_token: str,
        *,
        extend_seconds: int | None = None,
        now: datetime | None = None,
    ) -> ProjectWorkerRequest:
        current_time = self._as_utc(now or self._now())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            request = self._validate_lease(connection, worker_request_id, worker_id, lease_token, current_time)
            extension = extend_seconds if extend_seconds is not None else request.limits.lease_seconds
            if extension < 5 or extension > 3600:
                raise ProjectWorkerError(ProjectWorkerErrorCode.INVALID_REQUEST, "extend_seconds must be between 5 and 3600.")
            updated = request.model_copy(update={
                "heartbeat_at": current_time,
                "lease_expires_at": current_time + timedelta(seconds=extension),
                "updated_at": current_time,
            })
            connection.execute(
                """
                UPDATE project_worker_requests
                SET heartbeat_at = ?, lease_expires_at = ?, request_json = ?, updated_at = ?
                WHERE worker_request_id = ? AND status IN ('leased', 'cancel_requested')
                """,
                (
                    updated.heartbeat_at.isoformat(),
                    updated.lease_expires_at.isoformat(),
                    updated.model_dump_json(),
                    updated.updated_at.isoformat(),
                    updated.worker_request_id,
                ),
            )
            self._append_event(connection, updated, WorkerEventType.HEARTBEAT, worker_id=worker_id, metadata={
                "lease_expires_at": updated.lease_expires_at.isoformat(),
                "cancel_requested": updated.status == WorkerRequestStatus.CANCEL_REQUESTED,
            })
            connection.execute("COMMIT")
            return updated
        except ProjectWorkerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def request_cancel(self, worker_request_id: str, *, requested_by: str = "local-user") -> ProjectWorkerRequest:
        actor = self._required_text(requested_by, "requested_by", 160)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            request = self._load_request(connection, worker_request_id)
            if request.status in TERMINAL_WORKER_STATUSES:
                connection.execute("COMMIT")
                return request
            now = self._now()
            if request.status == WorkerRequestStatus.QUEUED:
                status = WorkerRequestStatus.CANCELLED
                finished_at = now
                event_type = WorkerEventType.CANCELLED
            elif request.status in {WorkerRequestStatus.LEASED, WorkerRequestStatus.CANCEL_REQUESTED}:
                status = WorkerRequestStatus.CANCEL_REQUESTED
                finished_at = None
                event_type = WorkerEventType.CANCEL_REQUESTED
            else:
                raise ProjectWorkerError(ProjectWorkerErrorCode.TERMINAL_REQUEST, "The request cannot accept cancellation in its current state.")
            updated = request.model_copy(update={
                "status": status,
                "cancellation_requested_at": request.cancellation_requested_at or now,
                "updated_at": now,
                "finished_at": finished_at,
                "failure_classification": "cancelled_before_lease" if status == WorkerRequestStatus.CANCELLED else request.failure_classification,
            })
            connection.execute(
                """
                UPDATE project_worker_requests
                SET status = ?, cancellation_requested_at = ?, failure_classification = ?,
                    request_json = ?, updated_at = ?, finished_at = ?
                WHERE worker_request_id = ?
                """,
                (
                    updated.status.value,
                    updated.cancellation_requested_at.isoformat(),
                    updated.failure_classification,
                    updated.model_dump_json(),
                    now.isoformat(),
                    updated.finished_at.isoformat() if updated.finished_at else None,
                    updated.worker_request_id,
                ),
            )
            self._append_event(connection, updated, event_type, worker_id=actor)
            connection.execute("COMMIT")
            return updated
        except ProjectWorkerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def complete(self, completion: WorkerCompletion | dict[str, Any], *, now: datetime | None = None) -> ProjectWorkerRequest:
        parsed = self._parse(WorkerCompletion, completion, "worker completion")
        completion_binding = parsed.model_dump(mode="json", exclude={"lease_token"})
        completion_binding["lease_token_hash"] = self._token_hash(parsed.lease_token)
        request_hash = content_hash(completion_binding)
        current_time = self._as_utc(now or self._now())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT project_run_id FROM project_worker_requests WHERE worker_request_id = ?",
                (parsed.worker_request_id,),
            ).fetchone()
            if row is None:
                raise ProjectWorkerError(ProjectWorkerErrorCode.REQUEST_NOT_FOUND, "Worker request not found.")
            project_run_id = str(row["project_run_id"])
            replay = self._idempotent_replay(
                connection,
                project_run_id,
                parsed.idempotency_key,
                "complete",
                request_hash,
                ProjectWorkerRequest,
            )
            if replay is not None:
                current = self._load_request(connection, parsed.worker_request_id)
                connection.execute("COMMIT")
                return current

            request = self._validate_lease(
                connection,
                parsed.worker_request_id,
                parsed.worker_id,
                parsed.lease_token,
                current_time,
            )
            if request.status == WorkerRequestStatus.CANCEL_REQUESTED and parsed.outcome != WorkerCompletionOutcome.CANCELLED:
                raise ProjectWorkerError(
                    ProjectWorkerErrorCode.INVALID_REQUEST,
                    "A cancellation-requested lease may only acknowledge cancellation.",
                )
            if request.status == WorkerRequestStatus.LEASED and parsed.outcome == WorkerCompletionOutcome.CANCELLED:
                raise ProjectWorkerError(
                    ProjectWorkerErrorCode.CANCEL_NOT_REQUESTED,
                    "The worker cannot report cancellation before cancellation is requested.",
                )
            result_payload = {
                "outcome": parsed.outcome.value,
                "result_reference": self._bounded_object(parsed.result_reference),
                "output_summary": self._bounded_object(parsed.output_summary),
                "resulting_manifest_hash": parsed.resulting_manifest_hash,
                "failure_classification": parsed.failure_classification,
            }
            if len(canonical_json(result_payload).encode("utf-8")) > request.limits.max_output_bytes:
                raise ProjectWorkerError(
                    ProjectWorkerErrorCode.RESULT_TOO_LARGE,
                    "The worker result exceeds the request output limit.",
                    metadata={"max_output_bytes": request.limits.max_output_bytes},
                )
            status = WorkerRequestStatus(parsed.outcome.value)
            failure = parsed.failure_classification
            if status == WorkerRequestStatus.FAILED and not failure:
                failure = "execution_failed"
            elif status == WorkerRequestStatus.TIMED_OUT and not failure:
                failure = "execution_timed_out"
            elif status == WorkerRequestStatus.CANCELLED and not failure:
                failure = "execution_cancelled"
            elif status == WorkerRequestStatus.SUCCEEDED:
                failure = None
            updated = request.model_copy(update={
                "status": status,
                "lease_owner": None,
                "lease_expires_at": None,
                "heartbeat_at": request.heartbeat_at,
                "result_reference": result_payload["result_reference"] or None,
                "resulting_manifest_hash": parsed.resulting_manifest_hash,
                "failure_classification": failure,
                "updated_at": current_time,
                "finished_at": current_time,
            })
            connection.execute(
                """
                UPDATE project_worker_requests
                SET status = ?, lease_owner = NULL, lease_token_hash = NULL, lease_expires_at = NULL,
                    result_json = ?, failure_classification = ?, request_json = ?, updated_at = ?, finished_at = ?
                WHERE worker_request_id = ? AND status IN ('leased', 'cancel_requested')
                """,
                (
                    updated.status.value,
                    canonical_json(result_payload),
                    updated.failure_classification,
                    updated.model_dump_json(),
                    current_time.isoformat(),
                    current_time.isoformat(),
                    updated.worker_request_id,
                ),
            )
            event_type = WorkerEventType(updated.status.value)
            self._append_event(connection, updated, event_type, worker_id=parsed.worker_id, metadata={
                "failure_classification": updated.failure_classification,
                "resulting_manifest_hash": updated.resulting_manifest_hash,
            })
            self._store_idempotency(
                connection,
                updated.project_run_id,
                parsed.idempotency_key,
                "complete",
                request_hash,
                updated.model_dump_json(),
                current_time,
            )
            connection.execute("COMMIT")
            return updated
        except ProjectWorkerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except (sqlite3.DatabaseError, sqlite3.IntegrityError) as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ProjectWorkerError(ProjectWorkerErrorCode.PERSISTENCE_CONFLICT, "The worker result could not be persisted atomically.") from error
        finally:
            connection.close()

    def recover_expired_leases(self, *, now: datetime | None = None) -> list[ProjectWorkerRequest]:
        current_time = self._as_utc(now or self._now())
        recovered: list[ProjectWorkerRequest] = []
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT request_json
                FROM project_worker_requests
                WHERE status IN ('leased', 'cancel_requested')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at, worker_request_id
                """,
                (current_time.isoformat(),),
            ).fetchall()
            for row in rows:
                request = self._stored_model(ProjectWorkerRequest, row["request_json"], "worker request")
                cancelled = request.status == WorkerRequestStatus.CANCEL_REQUESTED
                status = WorkerRequestStatus.CANCELLED if cancelled else WorkerRequestStatus.INTERRUPTED
                failure = "cancellation_lease_expired" if cancelled else "worker_lease_expired"
                updated = request.model_copy(update={
                    "status": status,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "failure_classification": failure,
                    "updated_at": current_time,
                    "finished_at": current_time,
                })
                connection.execute(
                    """
                    UPDATE project_worker_requests
                    SET status = ?, lease_owner = NULL, lease_token_hash = NULL, lease_expires_at = NULL,
                        failure_classification = ?, request_json = ?, updated_at = ?, finished_at = ?
                    WHERE worker_request_id = ? AND status IN ('leased', 'cancel_requested')
                    """,
                    (
                        updated.status.value,
                        failure,
                        updated.model_dump_json(),
                        current_time.isoformat(),
                        current_time.isoformat(),
                        updated.worker_request_id,
                    ),
                )
                self._append_event(
                    connection,
                    updated,
                    WorkerEventType.CANCELLED if cancelled else WorkerEventType.INTERRUPTED,
                    metadata={"failure_classification": failure},
                )
                recovered.append(updated)
            connection.execute("COMMIT")
            return recovered
        except ProjectWorkerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def list_unreconciled_failures(self) -> list[ProjectWorkerRequest]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_json
                FROM project_worker_requests
                WHERE status IN ('failed', 'cancelled', 'interrupted', 'timed_out')
                  AND canonical_reconciled_at IS NULL
                ORDER BY finished_at, worker_request_id
                """
            ).fetchall()
            return [self._stored_model(ProjectWorkerRequest, row["request_json"], "worker request") for row in rows]

    def mark_canonical_reconciled(
        self,
        worker_request_id: str,
        *,
        now: datetime | None = None,
    ) -> ProjectWorkerRequest:
        current_time = self._as_utc(now or self._now())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            request = self._load_request(connection, worker_request_id)
            if request.status not in {
                WorkerRequestStatus.FAILED,
                WorkerRequestStatus.CANCELLED,
                WorkerRequestStatus.INTERRUPTED,
                WorkerRequestStatus.TIMED_OUT,
            }:
                raise ProjectWorkerError(
                    ProjectWorkerErrorCode.INVALID_REQUEST,
                    "Only a terminal non-success request can be marked canonically reconciled.",
                )
            if request.canonical_reconciled_at is not None:
                connection.execute("COMMIT")
                return request
            updated = request.model_copy(update={
                "canonical_reconciled_at": current_time,
                "updated_at": current_time,
            })
            connection.execute(
                """
                UPDATE project_worker_requests
                SET canonical_reconciled_at = ?, request_json = ?, updated_at = ?
                WHERE worker_request_id = ? AND canonical_reconciled_at IS NULL
                """,
                (
                    current_time.isoformat(),
                    updated.model_dump_json(),
                    current_time.isoformat(),
                    worker_request_id,
                ),
            )
            self._append_event(
                connection,
                updated,
                WorkerEventType.CANONICAL_RECONCILED,
                metadata={"execution_attempt_id": updated.execution_attempt_id},
            )
            connection.execute("COMMIT")
            return updated
        except ProjectWorkerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _validate_enqueue_bindings(self, connection: sqlite3.Connection, command: WorkerEnqueueCommand) -> None:
        run_row = connection.execute(
            "SELECT schema_version, run_json FROM project_runs WHERE project_run_id = ?",
            (command.project_run_id,),
        ).fetchone()
        if run_row is None:
            raise ProjectWorkerError(ProjectWorkerErrorCode.INVALID_BINDING, "The canonical project run does not exist.")
        if run_row["schema_version"] != PROJECT_RUN_VERSION:
            raise ProjectWorkerError(ProjectWorkerErrorCode.UNSUPPORTED_STORED_STATE, "The stored project run schema is unsupported.")
        run = self._stored_model(ProjectRun, run_row["run_json"], "project run")
        if run.state_version != command.expected_project_state_version:
            raise ProjectWorkerError(
                ProjectWorkerErrorCode.STALE_PROJECT_STATE,
                "The worker request targets a stale project state version.",
                metadata={"expected": command.expected_project_state_version, "current": run.state_version},
            )
        identity_checks = (
            (run.conversation_id, command.conversation_id, "conversation"),
            (run.workspace_id, command.workspace_id, "workspace"),
            (run.repository_root, command.repository_root, "repository root"),
            (run.repository_root_fingerprint, command.repository_root_fingerprint, "repository identity"),
            (run.actor_id, command.actor_id, "actor"),
            (run.current_plan_revision_id, command.plan_revision_id, "plan revision"),
            (run.current_scope_revision_id, command.scope_revision_id, "scope revision"),
            (run.current_manifest_hash, command.manifest_hash, "manifest"),
        )
        for expected, actual, label in identity_checks:
            if expected != actual:
                raise ProjectWorkerError(ProjectWorkerErrorCode.INVALID_BINDING, f"The worker request {label} binding is stale.")
        if command.execution_attempt_id not in run.execution_attempt_ids:
            raise ProjectWorkerError(ProjectWorkerErrorCode.INVALID_BINDING, "The execution attempt is not owned by the canonical project run.")

        attempt_row = connection.execute(
            "SELECT schema_version, attempt_json FROM project_execution_attempts WHERE execution_attempt_id = ? AND project_run_id = ?",
            (command.execution_attempt_id, command.project_run_id),
        ).fetchone()
        if attempt_row is None:
            raise ProjectWorkerError(ProjectWorkerErrorCode.INVALID_BINDING, "The canonical execution attempt does not exist.")
        if attempt_row["schema_version"] != EXECUTION_ATTEMPT_VERSION:
            raise ProjectWorkerError(ProjectWorkerErrorCode.UNSUPPORTED_STORED_STATE, "The stored execution attempt schema is unsupported.")
        attempt = self._stored_model(ExecutionAttempt, attempt_row["attempt_json"], "execution attempt")
        if attempt.status not in {ExecutionAttemptStatus.PENDING, ExecutionAttemptStatus.ACTIVE}:
            raise ProjectWorkerError(ProjectWorkerErrorCode.ATTEMPT_NOT_ACTIVE, "Only an active canonical attempt can be queued.")
        attempt_checks = (
            (attempt.attempt_type, command.attempt_type, "attempt type"),
            (attempt.conversation_id, command.conversation_id, "conversation"),
            (attempt.workspace_id, command.workspace_id, "workspace"),
            (attempt.repository_root_fingerprint, command.repository_root_fingerprint, "repository identity"),
            (attempt.actor_id, command.actor_id, "actor"),
            (attempt.plan_revision_id, command.plan_revision_id, "plan revision"),
            (attempt.scope_revision_id, command.scope_revision_id, "scope revision"),
            (attempt.manifest_hash, command.manifest_hash, "manifest"),
            (attempt.authority, command.authority, "authority"),
        )
        for expected, actual, label in attempt_checks:
            if expected != actual:
                raise ProjectWorkerError(ProjectWorkerErrorCode.INVALID_BINDING, f"The worker request does not match the attempt {label} binding.")

    def _validate_lease(
        self,
        connection: sqlite3.Connection,
        worker_request_id: str,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> ProjectWorkerRequest:
        request = self._load_request(connection, worker_request_id)
        if request.status in TERMINAL_WORKER_STATUSES:
            raise ProjectWorkerError(ProjectWorkerErrorCode.TERMINAL_REQUEST, "The worker request is already terminal.")
        if request.status not in {WorkerRequestStatus.LEASED, WorkerRequestStatus.CANCEL_REQUESTED}:
            raise ProjectWorkerError(ProjectWorkerErrorCode.NOT_CLAIMABLE, "The worker request has no active lease.")
        row = connection.execute(
            "SELECT lease_token_hash FROM project_worker_requests WHERE worker_request_id = ?",
            (worker_request_id,),
        ).fetchone()
        if request.lease_owner != worker_id or row is None or row["lease_token_hash"] != self._token_hash(lease_token):
            raise ProjectWorkerError(ProjectWorkerErrorCode.LEASE_MISMATCH, "The worker lease identity or token is invalid.")
        if request.lease_expires_at is None or request.lease_expires_at <= now:
            raise ProjectWorkerError(ProjectWorkerErrorCode.LEASE_EXPIRED, "The worker lease has expired and cannot mutate the request.")
        return request

    def _load_request(self, connection: sqlite3.Connection, worker_request_id: str) -> ProjectWorkerRequest:
        row = connection.execute(
            "SELECT schema_version, request_json FROM project_worker_requests WHERE worker_request_id = ?",
            (worker_request_id,),
        ).fetchone()
        if row is None:
            raise ProjectWorkerError(ProjectWorkerErrorCode.REQUEST_NOT_FOUND, "Worker request not found.")
        if row["schema_version"] != WORKER_REQUEST_VERSION:
            raise ProjectWorkerError(ProjectWorkerErrorCode.UNSUPPORTED_STORED_STATE, "The stored worker request schema is unsupported.")
        return self._stored_model(ProjectWorkerRequest, row["request_json"], "worker request")

    def _append_event(
        self,
        connection: sqlite3.Connection,
        request: ProjectWorkerRequest,
        event_type: WorkerEventType,
        *,
        worker_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectWorkerEvent:
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS value FROM project_worker_events WHERE worker_request_id = ?",
                (request.worker_request_id,),
            ).fetchone()["value"]
        )
        event = ProjectWorkerEvent(
            event_id=uuid4().hex,
            worker_request_id=request.worker_request_id,
            project_run_id=request.project_run_id,
            sequence=sequence,
            event_type=event_type,
            worker_id=worker_id,
            metadata=self._bounded_object(metadata or {}),
            created_at=self._now(),
        )
        connection.execute(
            """
            INSERT INTO project_worker_events (
                event_id, worker_request_id, project_run_id, sequence, event_type,
                worker_id, schema_version, event_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.worker_request_id,
                event.project_run_id,
                event.sequence,
                event.event_type.value,
                event.worker_id,
                WORKER_EVENT_VERSION,
                event.model_dump_json(),
                event.created_at.isoformat(),
            ),
        )
        return event

    def _idempotent_replay(
        self,
        connection: sqlite3.Connection,
        project_run_id: str,
        operation_key: str,
        operation_type: str,
        request_hash: str,
        model: type[ModelT],
    ) -> ModelT | None:
        row = connection.execute(
            """
            SELECT operation_type, request_hash, response_json
            FROM project_worker_idempotency
            WHERE project_run_id = ? AND operation_key = ?
            """,
            (project_run_id, operation_key),
        ).fetchone()
        if row is None:
            return None
        if row["operation_type"] != operation_type or row["request_hash"] != request_hash:
            raise ProjectWorkerError(
                ProjectWorkerErrorCode.IDEMPOTENCY_CONFLICT,
                "This worker idempotency key was already used for a different operation.",
            )
        return self._stored_model(model, row["response_json"], "idempotent worker response")

    def _store_idempotency(
        self,
        connection: sqlite3.Connection,
        project_run_id: str,
        operation_key: str,
        operation_type: str,
        request_hash: str,
        response_json: str,
        now: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO project_worker_idempotency (
                project_run_id, operation_key, operation_type, request_hash, response_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_run_id, operation_key, operation_type, request_hash, response_json, now.isoformat()),
        )

    def _stored_model(self, model: type[ModelT], raw: str, label: str) -> ModelT:
        try:
            value = json.loads(raw)
            expected_schema = model.model_fields["schema_version"].default
            if value.get("schema_version") != expected_schema:
                raise ProjectWorkerError(
                    ProjectWorkerErrorCode.UNSUPPORTED_STORED_STATE,
                    f"The stored {label} schema is unsupported.",
                )
            return model.model_validate(value)
        except ProjectWorkerError:
            raise
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
            raise ProjectWorkerError(
                ProjectWorkerErrorCode.CORRUPTED_STORED_STATE,
                f"The stored {label} failed integrity validation.",
            ) from error

    def _parse(self, model: type[ModelT], value: ModelT | dict[str, Any], label: str) -> ModelT:
        try:
            return value if isinstance(value, model) else model.model_validate(value)
        except ValidationError as error:
            raise ProjectWorkerError(ProjectWorkerErrorCode.INVALID_REQUEST, f"The {label} is invalid.") from error

    def _bounded_object(self, value: Any, *, depth: int = 0) -> dict[str, Any]:
        if not isinstance(value, dict) or depth > 5:
            return {}
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:60]:
            bounded = self._bounded_value(item, depth=depth + 1)
            if bounded is not _UNSUPPORTED:
                result[str(key)[:120]] = bounded
        return result

    def _bounded_value(self, value: Any, *, depth: int) -> Any:
        if depth > 5:
            return _UNSUPPORTED
        if isinstance(value, str):
            return value[:4000]
        if isinstance(value, (int, float, bool, type(None))):
            return value
        if isinstance(value, dict):
            return self._bounded_object(value, depth=depth)
        if isinstance(value, (list, tuple)):
            result: list[Any] = []
            for item in list(value)[:60]:
                bounded = self._bounded_value(item, depth=depth + 1)
                if bounded is not _UNSUPPORTED:
                    result.append(bounded)
            return result
        return _UNSUPPORTED

    def _required_text(self, value: str, field: str, maximum: int) -> str:
        result = " ".join(str(value).split())
        if not result:
            raise ProjectWorkerError(ProjectWorkerErrorCode.INVALID_REQUEST, f"{field} is required.")
        return result[:maximum]

    def _token_hash(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _as_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


__all__ = ["ProjectWorkerQueue"]
