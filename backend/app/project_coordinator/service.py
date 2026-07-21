from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.database.migrations import (
    assert_schema_compatible,
    initialize_stage3a_schema,
)
from backend.app.project_control.contracts import ProjectLifecycle, content_hash
from backend.app.project_coordinator.contracts import (
    CoordinatorIntent,
    CoordinatorIntentStatus,
    CoordinatorIntentType,
)


class CoordinatorIntentError(ValueError):
    pass


_ACTION_INTENTS = {
    "begin_work_unit": CoordinatorIntentType.PREPARE_WORK_UNIT,
    "request_verification": CoordinatorIntentType.RUN_DETERMINISTIC_VERIFICATION,
    "initiate_repair": CoordinatorIntentType.PREPARE_REPAIR,
    "request_handoff": CoordinatorIntentType.PREPARE_HANDOFF,
}
_DEFAULT_LIMITS = {
    CoordinatorIntentType.PREPARE_WORK_UNIT: 25,
    CoordinatorIntentType.RUN_DETERMINISTIC_VERIFICATION: 50,
    CoordinatorIntentType.PREPARE_REPAIR: 1,
    CoordinatorIntentType.PREPARE_HANDOFF: 1,
}
_LIMIT_KEYS = {
    CoordinatorIntentType.PREPARE_WORK_UNIT: "max_work_units",
    CoordinatorIntentType.RUN_DETERMINISTIC_VERIFICATION: "max_verifications",
    CoordinatorIntentType.PREPARE_REPAIR: "max_repair_cycles",
    CoordinatorIntentType.PREPARE_HANDOFF: "max_handoffs",
}


class ProjectCoordinatorService:
    """Creates typed durable work intents but never writes project lifecycle state."""

    def __init__(self, database_path: str | Path, control) -> None:
        self.database_path = Path(database_path)
        self.control = control

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
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
                CREATE TABLE IF NOT EXISTS project_coordinator_intents (
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
                );
                CREATE INDEX IF NOT EXISTS idx_coordinator_pending
                    ON project_coordinator_intents(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_coordinator_project
                    ON project_coordinator_intents(project_run_id, created_at);
                """
            )
        assert_schema_compatible(self.database_path)

    def reconcile(self, project_run_id: str) -> CoordinatorIntent | None:
        run = self.control.get_project(project_run_id)
        if run.lifecycle_status in {
            ProjectLifecycle.COMPLETED,
            ProjectLifecycle.CANCELLED,
            ProjectLifecycle.BLOCKED,
        }:
            return None
        intent_type = _ACTION_INTENTS.get(str(run.pending_user_action or ""))
        if intent_type is None:
            return None
        if not run.current_plan_revision_id or not run.current_scope_revision_id or not run.current_manifest_hash:
            return None
        events = self.control.list_events(project_run_id)
        if not events:
            return None
        event = events[-1]
        payload = {
            "pending_user_action": run.pending_user_action,
            "lifecycle_state": run.lifecycle_status.value,
            "event_sequence": event.sequence,
        }
        payload_hash = content_hash(payload)
        idempotency_key = (
            f"coordinator:{intent_type.value}:{event.event_id}:"
            f"{run.current_plan_revision_id}:{run.current_scope_revision_id}:"
            f"{run.current_manifest_hash}"
        )
        now = datetime.now(timezone.utc)
        intent = CoordinatorIntent(
            coordinator_intent_id=f"intent-{content_hash([project_run_id, idempotency_key])[:24]}",
            project_run_id=project_run_id,
            intent_type=intent_type,
            status=CoordinatorIntentStatus.PENDING,
            trigger_event_id=event.event_id,
            idempotency_key=idempotency_key,
            plan_revision_id=run.current_plan_revision_id,
            scope_revision_id=run.current_scope_revision_id,
            manifest_hash=run.current_manifest_hash,
            expected_project_state_version=run.state_version,
            payload=payload,
            payload_hash=payload_hash,
            created_at=now,
            updated_at=now,
        )
        limit = self._intent_limit(run, intent_type)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT intent_json FROM project_coordinator_intents WHERE project_run_id = ? AND idempotency_key = ?",
                (project_run_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                parsed = CoordinatorIntent.model_validate_json(str(existing["intent_json"]))
                if parsed.payload_hash != payload_hash:
                    raise CoordinatorIntentError("A coordinator idempotency key was reused with different work.")
                return parsed
            count = int(connection.execute(
                "SELECT COUNT(*) FROM project_coordinator_intents WHERE project_run_id = ? AND intent_type = ? AND status != 'cancelled'",
                (project_run_id, intent_type.value),
            ).fetchone()[0])
            if count >= limit:
                raise CoordinatorIntentError(
                    f"The durable {intent_type.value} budget of {limit} was reached."
                )
            connection.execute(
                "INSERT INTO project_coordinator_intents (coordinator_intent_id, project_run_id, intent_type, status, trigger_event_id, idempotency_key, plan_revision_id, scope_revision_id, manifest_hash, expected_project_state_version, payload_hash, schema_version, intent_json, attempt_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    intent.coordinator_intent_id, intent.project_run_id,
                    intent.intent_type.value, intent.status.value, intent.trigger_event_id,
                    intent.idempotency_key, intent.plan_revision_id, intent.scope_revision_id,
                    intent.manifest_hash, intent.expected_project_state_version,
                    intent.payload_hash, intent.schema_version, intent.model_dump_json(),
                    intent.attempt_count,
                    now.isoformat(), now.isoformat(),
                ),
            )
        return intent

    def reconcile_all(self, *, limit: int = 100) -> tuple[str, ...]:
        """Recover missing idempotent intents without processing any intent."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT project_run_id FROM project_runs "
                "WHERE lifecycle_status NOT IN ('completed', 'cancelled', 'handed_off') "
                "ORDER BY updated_at, project_run_id LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        reconciled: list[str] = []
        for row in rows:
            project_run_id = str(row["project_run_id"])
            existing_ids = {
                item.coordinator_intent_id
                for item in self.list_for_project(project_run_id)
            }
            try:
                intent = self.reconcile(project_run_id)
            except CoordinatorIntentError:
                continue
            if (
                intent is not None
                and intent.coordinator_intent_id not in existing_ids
            ):
                reconciled.append(intent.coordinator_intent_id)
        return tuple(reconciled)

    def list_for_project(self, project_run_id: str) -> list[CoordinatorIntent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT intent_json FROM project_coordinator_intents WHERE project_run_id = ? ORDER BY created_at, coordinator_intent_id",
                (project_run_id,),
            ).fetchall()
        return [CoordinatorIntent.model_validate_json(str(row["intent_json"])) for row in rows]

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 30,
    ) -> CoordinatorIntent | None:
        if not worker_id.strip() or not 5 <= lease_seconds <= 3600:
            raise CoordinatorIntentError("A worker identity and bounded lease are required.")
        self.recover_expired_leases()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT intent_json FROM project_coordinator_intents WHERE status = 'pending' ORDER BY created_at, coordinator_intent_id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            current = CoordinatorIntent.model_validate_json(str(row["intent_json"]))
            now = datetime.now(timezone.utc)
            claimed = current.model_copy(update={
                "status": CoordinatorIntentStatus.CLAIMED,
                "lease_owner": worker_id.strip()[:160],
                "lease_token": uuid4().hex,
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
                "heartbeat_at": now,
                "attempt_count": current.attempt_count + 1,
                "updated_at": now,
            })
            connection.execute(
                "UPDATE project_coordinator_intents SET status = ?, intent_json = ?, lease_expires_at = ?, heartbeat_at = ?, attempt_count = ?, updated_at = ? WHERE coordinator_intent_id = ? AND status = 'pending'",
                (
                    claimed.status.value, claimed.model_dump_json(),
                    claimed.lease_expires_at.isoformat(), now.isoformat(),
                    claimed.attempt_count, now.isoformat(),
                    claimed.coordinator_intent_id,
                ),
            )
            connection.execute("COMMIT")
            return claimed
        finally:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            connection.close()

    def heartbeat(
        self,
        coordinator_intent_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_seconds: int = 30,
    ) -> CoordinatorIntent:
        if not 5 <= lease_seconds <= 3600:
            raise CoordinatorIntentError("A bounded coordinator lease is required.")
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._require_intent(connection, coordinator_intent_id)
            self._require_lease(current, worker_id, lease_token)
            updated = current.model_copy(update={
                "heartbeat_at": now,
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
                "updated_at": now,
            })
            connection.execute(
                "UPDATE project_coordinator_intents SET intent_json = ?, "
                "lease_expires_at = ?, heartbeat_at = ?, updated_at = ? "
                "WHERE coordinator_intent_id = ? AND status = 'claimed'",
                (
                    updated.model_dump_json(),
                    updated.lease_expires_at.isoformat(), now.isoformat(),
                    now.isoformat(),
                    coordinator_intent_id,
                ),
            )
        return updated

    def release_for_retry(
        self,
        coordinator_intent_id: str,
        *,
        worker_id: str,
        lease_token: str,
        failure_classification: str,
        max_attempts: int = 3,
    ) -> CoordinatorIntent:
        classification = " ".join(failure_classification.split())[:120]
        if not classification:
            raise CoordinatorIntentError("A retry classification is required.")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._require_intent(connection, coordinator_intent_id)
            self._require_lease(current, worker_id, lease_token)
            if current.attempt_count >= max(1, min(int(max_attempts), 20)):
                connection.execute("ROLLBACK")
                return self.fail(
                    coordinator_intent_id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    failure_classification=classification,
                )
            now = datetime.now(timezone.utc)
            pending = current.model_copy(update={
                "status": CoordinatorIntentStatus.PENDING,
                "lease_owner": None,
                "lease_token": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "failure_classification": classification,
                "updated_at": now,
            })
            connection.execute(
                "UPDATE project_coordinator_intents SET status = 'pending', "
                "intent_json = ?, lease_expires_at = NULL, heartbeat_at = NULL, "
                "last_failure_classification = ?, updated_at = ? "
                "WHERE coordinator_intent_id = ? AND status = 'claimed'",
                (
                    pending.model_dump_json(), classification, now.isoformat(),
                    coordinator_intent_id,
                ),
            )
        return pending

    def cancel_stale_for_project(self, project_run_id: str) -> tuple[str, ...]:
        run = self.control.get_project(project_run_id)
        cancelled: list[str] = []
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT intent_json FROM project_coordinator_intents "
                "WHERE project_run_id = ? AND status = 'pending'",
                (project_run_id,),
            ).fetchall()
            for row in rows:
                current = CoordinatorIntent.model_validate_json(str(row["intent_json"]))
                if self._binding_is_current(current, run):
                    continue
                stale = current.model_copy(update={
                    "status": CoordinatorIntentStatus.CANCELLED,
                    "failure_classification": "stale_project_binding",
                    "updated_at": now,
                    "completed_at": now,
                })
                connection.execute(
                    "UPDATE project_coordinator_intents SET status = 'cancelled', "
                    "intent_json = ?, updated_at = ?, completed_at = ? "
                    "WHERE coordinator_intent_id = ? AND status = 'pending'",
                    (
                        stale.model_dump_json(), now.isoformat(), now.isoformat(),
                        stale.coordinator_intent_id,
                    ),
                )
                cancelled.append(stale.coordinator_intent_id)
        return tuple(cancelled)

    def complete(
        self,
        coordinator_intent_id: str,
        *,
        worker_id: str,
        lease_token: str,
        result_reference: dict[str, Any],
    ) -> CoordinatorIntent:
        return self._finish(
            coordinator_intent_id,
            worker_id=worker_id,
            lease_token=lease_token,
            status=CoordinatorIntentStatus.COMPLETED,
            result_reference=result_reference,
        )

    def fail(
        self,
        coordinator_intent_id: str,
        *,
        worker_id: str,
        lease_token: str,
        failure_classification: str,
    ) -> CoordinatorIntent:
        return self._finish(
            coordinator_intent_id,
            worker_id=worker_id,
            lease_token=lease_token,
            status=CoordinatorIntentStatus.FAILED,
            failure_classification=failure_classification,
        )

    def recover_expired_leases(self, *, now: datetime | None = None) -> int:
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        recovered = 0
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT intent_json FROM project_coordinator_intents WHERE status = 'claimed' AND lease_expires_at <= ?",
                (current_time.isoformat(),),
            ).fetchall()
            for row in rows:
                current = CoordinatorIntent.model_validate_json(str(row["intent_json"]))
                pending = current.model_copy(update={
                    "status": CoordinatorIntentStatus.PENDING,
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "updated_at": current_time,
                })
                connection.execute(
                    "UPDATE project_coordinator_intents SET status = 'pending', intent_json = ?, lease_expires_at = NULL, heartbeat_at = NULL, updated_at = ? WHERE coordinator_intent_id = ? AND status = 'claimed'",
                    (pending.model_dump_json(), current_time.isoformat(), pending.coordinator_intent_id),
                )
                recovered += 1
        return recovered

    def _finish(
        self,
        coordinator_intent_id: str,
        *,
        worker_id: str,
        lease_token: str,
        status: CoordinatorIntentStatus,
        result_reference: dict[str, Any] | None = None,
        failure_classification: str | None = None,
    ) -> CoordinatorIntent:
        bounded_result = dict(result_reference or {})
        if len(json.dumps(bounded_result, sort_keys=True, default=str)) > 65_536:
            raise CoordinatorIntentError("Coordinator result evidence exceeds the durable limit.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT intent_json FROM project_coordinator_intents WHERE coordinator_intent_id = ?",
                (coordinator_intent_id,),
            ).fetchone()
            if row is None:
                raise CoordinatorIntentError("Coordinator intent not found.")
            current = CoordinatorIntent.model_validate_json(str(row["intent_json"]))
            if current.status == status:
                return current
            if (
                current.status != CoordinatorIntentStatus.CLAIMED
                or current.lease_owner != worker_id
                or current.lease_token != lease_token
            ):
                raise CoordinatorIntentError("The coordinator lease is missing or stale.")
            now = datetime.now(timezone.utc)
            finished = current.model_copy(update={
                "status": status,
                "result_reference": bounded_result or None,
                "failure_classification": failure_classification,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "updated_at": now,
                "completed_at": now,
            })
            connection.execute(
                "UPDATE project_coordinator_intents SET status = ?, intent_json = ?, "
                "lease_expires_at = NULL, heartbeat_at = NULL, "
                "last_failure_classification = ?, updated_at = ?, completed_at = ? "
                "WHERE coordinator_intent_id = ?",
                (
                    status.value, finished.model_dump_json(), failure_classification,
                    now.isoformat(), now.isoformat(), coordinator_intent_id,
                ),
            )
        return finished

    @staticmethod
    def _binding_is_current(intent: CoordinatorIntent, run) -> bool:
        return (
            intent.plan_revision_id == run.current_plan_revision_id
            and intent.scope_revision_id == run.current_scope_revision_id
            and intent.manifest_hash == run.current_manifest_hash
            and intent.expected_project_state_version == run.state_version
        )

    @staticmethod
    def _require_lease(
        intent: CoordinatorIntent,
        worker_id: str,
        lease_token: str,
    ) -> None:
        if (
            intent.status != CoordinatorIntentStatus.CLAIMED
            or intent.lease_owner != worker_id
            or intent.lease_token != lease_token
            or intent.lease_expires_at is None
            or intent.lease_expires_at <= datetime.now(timezone.utc)
        ):
            raise CoordinatorIntentError("The coordinator lease is missing or stale.")

    @staticmethod
    def _require_intent(
        connection: sqlite3.Connection,
        coordinator_intent_id: str,
    ) -> CoordinatorIntent:
        row = connection.execute(
            "SELECT intent_json FROM project_coordinator_intents "
            "WHERE coordinator_intent_id = ?",
            (coordinator_intent_id,),
        ).fetchone()
        if row is None:
            raise CoordinatorIntentError("Coordinator intent not found.")
        return CoordinatorIntent.model_validate_json(str(row["intent_json"]))

    def _intent_limit(self, run, intent_type: CoordinatorIntentType) -> int:
        configured: dict[str, Any] = {}
        if run.current_plan_revision_id:
            configured = dict(
                self.control.get_plan_revision(run.current_plan_revision_id).configured_limits
            )
        value = configured.get(_LIMIT_KEYS[intent_type], _DEFAULT_LIMITS[intent_type])
        try:
            return max(1, min(int(value), 10_000))
        except (TypeError, ValueError):
            return _DEFAULT_LIMITS[intent_type]


__all__ = ["CoordinatorIntentError", "ProjectCoordinatorService"]
