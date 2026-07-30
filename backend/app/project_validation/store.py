from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from backend.app.project_validation.contracts import (
    HumanReviewDecision,
    ValidationAuditEvent,
    ValidationCampaign,
    ValidationRun,
)


class ValidationStoreError(RuntimeError):
    pass


class ValidationConflictError(ValidationStoreError):
    pass


class ProjectValidationStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_validation_campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    engagement_id TEXT NOT NULL,
                    delivery_job_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    active_run_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_validation_campaign_conversation
                    ON project_validation_campaigns(conversation_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_validation_campaign_engagement
                    ON project_validation_campaigns(engagement_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_validation_campaign_delivery
                    ON project_validation_campaigns(delivery_job_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS project_validation_runs (
                    run_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    run_number INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    result_hash TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(campaign_id, run_number),
                    FOREIGN KEY(campaign_id) REFERENCES project_validation_campaigns(campaign_id)
                );
                CREATE INDEX IF NOT EXISTS idx_validation_runs_campaign
                    ON project_validation_runs(campaign_id, run_number DESC);

                CREATE TABLE IF NOT EXISTS project_validation_reviews (
                    review_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    validation_result_hash TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES project_validation_campaigns(campaign_id),
                    FOREIGN KEY(run_id) REFERENCES project_validation_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS project_validation_idempotency (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(scope, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS project_validation_audit (
                    event_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    run_id TEXT,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_validation_audit_campaign
                    ON project_validation_audit(campaign_id, created_at DESC);
                """
            )

    def create_campaign(self, campaign: ValidationCampaign) -> None:
        payload = campaign.model_dump_json()
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO project_validation_campaigns
                    (campaign_id, conversation_id, engagement_id, delivery_job_id, state, state_version, active_run_id, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        campaign.campaign_id, campaign.conversation_id,
                        campaign.scope_reference.engagement_id, campaign.project_reference.delivery_job_id,
                        campaign.state.value, campaign.state_version, campaign.active_run_id, payload,
                        campaign.created_at.isoformat(), campaign.updated_at.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                connection.execute("ROLLBACK")
                raise ValidationConflictError("The validation campaign already exists.") from error

    def get_campaign(self, campaign_id: str) -> ValidationCampaign | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM project_validation_campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
        return ValidationCampaign.model_validate_json(row["payload_json"]) if row else None

    def save_campaign(self, campaign: ValidationCampaign, *, expected_version: int) -> None:
        if campaign.state_version != expected_version + 1:
            raise ValueError("Campaign state_version must increment exactly once.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE project_validation_campaigns
                SET state = ?, state_version = ?, active_run_id = ?, payload_json = ?, updated_at = ?
                WHERE campaign_id = ? AND state_version = ?""",
                (
                    campaign.state.value, campaign.state_version, campaign.active_run_id,
                    campaign.model_dump_json(), campaign.updated_at.isoformat(), campaign.campaign_id, expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ValidationConflictError("The validation campaign changed before this action completed.")
            connection.execute("COMMIT")

    def create_run(self, run: ValidationRun) -> None:
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO project_validation_runs
                    (run_id, campaign_id, run_number, state, state_version, result_hash, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run.run_id, run.campaign_id, run.run_number, run.state.value,
                        run.state_version, run.result_hash, run.model_dump_json(),
                        run.started_at.isoformat(), run.updated_at.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                connection.execute("ROLLBACK")
                raise ValidationConflictError("The validation run already exists.") from error

    def get_run(self, run_id: str) -> ValidationRun | None:
        with self._connection() as connection:
            row = connection.execute("SELECT payload_json FROM project_validation_runs WHERE run_id = ?", (run_id,)).fetchone()
        return ValidationRun.model_validate_json(row["payload_json"]) if row else None

    def save_run(self, run: ValidationRun, *, expected_version: int) -> None:
        if run.state_version != expected_version + 1:
            raise ValueError("Run state_version must increment exactly once.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE project_validation_runs
                SET state = ?, state_version = ?, result_hash = ?, payload_json = ?, updated_at = ?
                WHERE run_id = ? AND state_version = ?""",
                (
                    run.state.value, run.state_version, run.result_hash,
                    run.model_dump_json(), run.updated_at.isoformat(), run.run_id, expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ValidationConflictError("The validation run changed before this action completed.")
            connection.execute("COMMIT")

    def list_runs(self, campaign_id: str) -> list[ValidationRun]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM project_validation_runs WHERE campaign_id = ? ORDER BY run_number", (campaign_id,)
            ).fetchall()
        return [ValidationRun.model_validate_json(row["payload_json"]) for row in rows]

    def save_review(self, review: HumanReviewDecision) -> None:
        with self._connection() as connection:
            try:
                connection.execute(
                    """INSERT INTO project_validation_reviews
                    (review_id, campaign_id, run_id, validation_result_hash, action, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        review.review_id, review.campaign_id, review.run_id,
                        review.validation_result_hash, review.action.value,
                        review.model_dump_json(), review.reviewed_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValidationConflictError("A human review already exists for this validation run.") from error

    def get_idempotent(self, scope: str, idempotency_key: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT response_json FROM project_validation_idempotency WHERE scope = ? AND idempotency_key = ?",
                (scope, idempotency_key),
            ).fetchone()
        return json.loads(row["response_json"]) if row else None

    def save_idempotent(self, scope: str, idempotency_key: str, response: dict) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO project_validation_idempotency
                (scope, idempotency_key, response_json, created_at) VALUES (?, ?, ?, ?)""",
                (scope, idempotency_key, json.dumps(response, sort_keys=True, separators=(",", ":")), datetime.now(timezone.utc).isoformat()),
            )

    def audit(self, *, campaign_id: str, event_type: str, actor_id: str, run_id: str | None = None, payload: dict | None = None) -> ValidationAuditEvent:
        safe_payload = _bounded_audit_payload(payload or {})
        event = ValidationAuditEvent(
            event_id=f"validation-audit-{uuid4().hex}", campaign_id=campaign_id, run_id=run_id,
            event_type=event_type, actor_id=actor_id, payload=safe_payload, created_at=datetime.now(timezone.utc),
        )
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO project_validation_audit
                (event_id, campaign_id, run_id, event_type, actor_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id, event.campaign_id, event.run_id, event.event_type,
                    event.actor_id, json.dumps(event.payload, sort_keys=True, separators=(",", ":")), event.created_at.isoformat(),
                ),
            )
        return event

    def audit_history(self, campaign_id: str, *, limit: int = 100) -> list[ValidationAuditEvent]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM project_validation_audit WHERE campaign_id = ? ORDER BY created_at DESC LIMIT ?",
                (campaign_id, max(1, min(limit, 500))),
            ).fetchall()
        return [ValidationAuditEvent(
            event_id=row["event_id"], campaign_id=row["campaign_id"], run_id=row["run_id"],
            event_type=row["event_type"], actor_id=row["actor_id"], payload=json.loads(row["payload_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        ) for row in rows]


def _bounded_audit_payload(payload: dict) -> dict:
    blocked = {"content", "file_content", "stdout", "stderr", "secret", "token", "environment"}
    result: dict = {}
    for key, value in list(payload.items())[:40]:
        lowered = str(key).lower()
        if any(term in lowered for term in blocked):
            result[key] = "[redacted]"
        elif isinstance(value, str):
            result[key] = value[:1000]
        elif isinstance(value, list):
            result[key] = value[:50]
        elif isinstance(value, dict):
            result[key] = {str(k): (str(v)[:500] if not isinstance(v, (int, float, bool, type(None))) else v) for k, v in list(value.items())[:30]}
        else:
            result[key] = value
    return result


__all__ = ["ProjectValidationStore", "ValidationConflictError", "ValidationStoreError"]
