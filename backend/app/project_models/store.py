from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from backend.app.database.migrations import (
    apply_schema_migrations,
    assert_schema_compatible,
)
from backend.app.project_control.contracts import content_hash
from backend.app.project_models.contracts import (
    ProjectModelInvocation,
    ProjectModelInvocationStatus,
)


class ProjectModelInvocationError(RuntimeError):
    pass


class ProjectModelInvocationStore:
    """Durable idempotency and lease authority for bounded model calls."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path, timeout=30, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        apply_schema_migrations(self.database_path)
        assert_schema_compatible(self.database_path)

    def create(self, invocation: ProjectModelInvocation) -> ProjectModelInvocation:
        candidate = self._validated_invocation(invocation)
        if candidate.status != ProjectModelInvocationStatus.PENDING:
            raise ProjectModelInvocationError("new model invocations must be pending")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM project_model_invocations "
                    "WHERE project_run_id = ? AND idempotency_key = ?",
                    (candidate.project_run_id, candidate.idempotency_key),
                ).fetchone()
                if row is not None:
                    stored = self._parse_row(row)
                    if self._request_binding(stored) != self._request_binding(
                        candidate
                    ):
                        raise ProjectModelInvocationError(
                            "model invocation idempotency key is bound to a different request"
                        )
                    connection.commit()
                    return stored
                connection.execute(
                    """INSERT INTO project_model_invocations (
                           invocation_id, project_run_id, coordinator_intent_id, purpose,
                           evidence_hash, provider, model_profile, request_hash,
                           idempotency_key, status, lease_owner, lease_token_hash,
                           lease_expires_at, schema_version, invocation_json,
                           created_at, updated_at, completed_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    self._values(candidate),
                )
                connection.commit()
                return candidate
            except Exception:
                connection.rollback()
                raise

    def get(self, invocation_id: str) -> ProjectModelInvocation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM project_model_invocations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
        return self._parse_row(row) if row is not None else None

    def list_for_project(
        self, project_run_id: str, *, limit: int = 100
    ) -> list[ProjectModelInvocation]:
        bounded_limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM project_model_invocations "
                "WHERE project_run_id = ? ORDER BY created_at, invocation_id LIMIT ?",
                (project_run_id, bounded_limit),
            ).fetchall()
        return [self._parse_row(row) for row in rows]

    def claim(
        self,
        invocation_id: str,
        *,
        lease_owner: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> tuple[ProjectModelInvocation, str]:
        owner = " ".join(lease_owner.split())[:200]
        if not owner or lease_seconds < 1 or lease_seconds > 3600:
            raise ProjectModelInvocationError("invalid model invocation lease")
        current_time = self._utc(now)
        token = uuid4().hex + uuid4().hex
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._require(connection, invocation_id)
                if current.status != ProjectModelInvocationStatus.PENDING:
                    raise ProjectModelInvocationError("model invocation is not pending")
                claimed = self._validated_update(
                    current,
                    status=ProjectModelInvocationStatus.CLAIMED,
                    lease_owner=owner,
                    lease_token_hash=token_hash,
                    lease_expires_at=current_time + timedelta(seconds=lease_seconds),
                    updated_at=current_time,
                )
                self._update(connection, claimed)
                connection.commit()
                return claimed, token
            except Exception:
                connection.rollback()
                raise

    def heartbeat(
        self,
        invocation_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> ProjectModelInvocation:
        current_time = self._utc(now)
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ProjectModelInvocationError("invalid model invocation lease")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._require_claim(
                    connection, invocation_id, lease_owner, lease_token, current_time
                )
                updated = self._validated_update(
                    current,
                    lease_expires_at=current_time + timedelta(seconds=lease_seconds),
                    updated_at=current_time,
                )
                self._update(connection, updated)
                connection.commit()
                return updated
            except Exception:
                connection.rollback()
                raise

    def succeed(
        self,
        invocation_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        result_payload: object,
        result_reference: dict[str, object] | None = None,
        usage: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> ProjectModelInvocation:
        current_time = self._utc(now)
        return self._finish(
            invocation_id,
            lease_owner,
            lease_token,
            current_time,
            status=ProjectModelInvocationStatus.SUCCEEDED,
            result_hash=content_hash(result_payload),
            result_reference=result_reference,
            usage=usage or {},
        )

    def fail(
        self,
        invocation_id: str,
        *,
        lease_owner: str,
        lease_token: str,
        failure_classification: str,
        error_message: str,
        usage: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> ProjectModelInvocation:
        current_time = self._utc(now)
        classification = " ".join(failure_classification.split())[:120]
        if not classification:
            raise ProjectModelInvocationError("failure classification is required")
        return self._finish(
            invocation_id,
            lease_owner,
            lease_token,
            current_time,
            status=ProjectModelInvocationStatus.FAILED,
            failure_classification=classification,
            error_message=" ".join(error_message.split())[:4096],
            usage=usage or {},
        )

    def recover_expired_leases(self, *, now: datetime | None = None) -> int:
        current_time = self._utc(now)
        recovered = 0
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    "SELECT * FROM project_model_invocations "
                    "WHERE status = ? AND lease_expires_at <= ?",
                    (
                        ProjectModelInvocationStatus.CLAIMED.value,
                        current_time.isoformat(),
                    ),
                ).fetchall()
                for row in rows:
                    current = self._parse_row(row)
                    pending = self._validated_update(
                        current,
                        status=ProjectModelInvocationStatus.PENDING,
                        lease_owner=None,
                        lease_token_hash=None,
                        lease_expires_at=None,
                        updated_at=current_time,
                    )
                    self._update(connection, pending)
                    recovered += 1
                connection.commit()
                return recovered
            except Exception:
                connection.rollback()
                raise

    def _finish(
        self,
        invocation_id: str,
        lease_owner: str,
        lease_token: str,
        current_time: datetime,
        *,
        status: ProjectModelInvocationStatus,
        result_hash: str | None = None,
        result_reference: dict[str, object] | None = None,
        failure_classification: str | None = None,
        error_message: str | None = None,
        usage: dict[str, object],
    ) -> ProjectModelInvocation:
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._require_claim(
                    connection, invocation_id, lease_owner, lease_token, current_time
                )
                finished = self._validated_update(
                    current,
                    status=status,
                    lease_owner=None,
                    lease_token_hash=None,
                    lease_expires_at=None,
                    result_hash=result_hash,
                    result_reference=result_reference,
                    failure_classification=failure_classification,
                    error_message=error_message,
                    usage=usage,
                    updated_at=current_time,
                    completed_at=current_time,
                )
                self._update(connection, finished)
                connection.commit()
                return finished
            except Exception:
                connection.rollback()
                raise

    def _require_claim(
        self,
        connection: sqlite3.Connection,
        invocation_id: str,
        lease_owner: str,
        lease_token: str,
        now: datetime,
    ) -> ProjectModelInvocation:
        current = self._require(connection, invocation_id)
        supplied_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        if (
            current.status != ProjectModelInvocationStatus.CLAIMED
            or current.lease_owner != lease_owner
            or not current.lease_token_hash
            or not secrets.compare_digest(current.lease_token_hash, supplied_hash)
            or not current.lease_expires_at
            or current.lease_expires_at <= now
        ):
            raise ProjectModelInvocationError(
                "model invocation lease is stale or invalid"
            )
        return current

    def _require(
        self, connection: sqlite3.Connection, invocation_id: str
    ) -> ProjectModelInvocation:
        row = connection.execute(
            "SELECT * FROM project_model_invocations WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if row is None:
            raise ProjectModelInvocationError("model invocation does not exist")
        return self._parse_row(row)

    def _update(
        self, connection: sqlite3.Connection, invocation: ProjectModelInvocation
    ) -> None:
        cursor = connection.execute(
            """UPDATE project_model_invocations SET
                   status = ?, lease_owner = ?, lease_token_hash = ?, lease_expires_at = ?,
                   invocation_json = ?, updated_at = ?, completed_at = ?
               WHERE invocation_id = ?""",
            (
                invocation.status.value,
                invocation.lease_owner,
                invocation.lease_token_hash,
                (
                    invocation.lease_expires_at.isoformat()
                    if invocation.lease_expires_at
                    else None
                ),
                invocation.model_dump_json(),
                invocation.updated_at.isoformat(),
                (
                    invocation.completed_at.isoformat()
                    if invocation.completed_at
                    else None
                ),
                invocation.invocation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ProjectModelInvocationError("model invocation update was lost")

    @staticmethod
    def _values(invocation: ProjectModelInvocation) -> tuple[object, ...]:
        return (
            invocation.invocation_id,
            invocation.project_run_id,
            invocation.coordinator_intent_id,
            invocation.purpose,
            invocation.evidence_hash,
            invocation.provider,
            invocation.model_profile,
            invocation.request_hash,
            invocation.idempotency_key,
            invocation.status.value,
            invocation.lease_owner,
            invocation.lease_token_hash,
            (
                invocation.lease_expires_at.isoformat()
                if invocation.lease_expires_at
                else None
            ),
            invocation.schema_version,
            invocation.model_dump_json(),
            invocation.created_at.isoformat(),
            invocation.updated_at.isoformat(),
            invocation.completed_at.isoformat() if invocation.completed_at else None,
        )

    @staticmethod
    def _request_binding(invocation: ProjectModelInvocation) -> tuple[object, ...]:
        return (
            invocation.invocation_id,
            invocation.project_run_id,
            invocation.coordinator_intent_id,
            invocation.purpose,
            invocation.evidence_hash,
            invocation.provider,
            invocation.model_profile,
            invocation.request_hash,
            invocation.idempotency_key,
        )

    @staticmethod
    def _validated_invocation(
        invocation: ProjectModelInvocation,
    ) -> ProjectModelInvocation:
        plain = invocation.model_dump(mode="python", round_trip=True)
        return ProjectModelInvocation.model_validate(plain)

    @classmethod
    def _validated_update(
        cls,
        invocation: ProjectModelInvocation,
        **updates: object,
    ) -> ProjectModelInvocation:
        plain = invocation.model_dump(mode="python", round_trip=True)
        plain.update(updates)
        return ProjectModelInvocation.model_validate(plain)

    @staticmethod
    def _parse(value: str) -> ProjectModelInvocation:
        try:
            return ProjectModelInvocation.model_validate_json(value)
        except ValidationError as exc:
            raise ProjectModelInvocationError(
                "stored model invocation failed integrity validation"
            ) from exc

    @classmethod
    def _parse_row(cls, row: sqlite3.Row) -> ProjectModelInvocation:
        invocation = cls._parse(str(row["invocation_json"]))
        normalized = (
            (str(row["invocation_id"]), invocation.invocation_id),
            (str(row["project_run_id"]), invocation.project_run_id),
            (row["coordinator_intent_id"], invocation.coordinator_intent_id),
            (str(row["purpose"]), invocation.purpose),
            (str(row["evidence_hash"]), invocation.evidence_hash),
            (str(row["provider"]), invocation.provider),
            (str(row["model_profile"]), invocation.model_profile),
            (str(row["request_hash"]), invocation.request_hash),
            (str(row["idempotency_key"]), invocation.idempotency_key),
            (str(row["status"]), invocation.status.value),
            (row["lease_owner"], invocation.lease_owner),
            (row["lease_token_hash"], invocation.lease_token_hash),
            (
                row["lease_expires_at"],
                (
                    invocation.lease_expires_at.isoformat()
                    if invocation.lease_expires_at
                    else None
                ),
            ),
            (str(row["schema_version"]), invocation.schema_version),
            (str(row["created_at"]), invocation.created_at.isoformat()),
            (str(row["updated_at"]), invocation.updated_at.isoformat()),
            (
                row["completed_at"],
                (
                    invocation.completed_at.isoformat()
                    if invocation.completed_at
                    else None
                ),
            ),
        )
        if any(actual != expected for actual, expected in normalized):
            raise ProjectModelInvocationError(
                "stored model invocation normalized fields failed integrity validation"
            )
        return invocation

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        current = value or datetime.now(timezone.utc)
        return (
            current.astimezone(timezone.utc)
            if current.tzinfo
            else current.replace(tzinfo=timezone.utc)
        )
