from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.project_control.contracts import canonical_json, content_hash
from backend.app.runtime.contracts import BackgroundJob, BackgroundJobStatus

_JOB_COLUMNS = (
    "job_id, idempotency_key, job_type, target_id, status, priority, "
    "job_json, lease_owner, lease_expires_at, created_at, updated_at"
)

MAX_QUEUE_DEPTH = 500


class RuntimeJobQueueError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_job(row: sqlite3.Row) -> BackgroundJob:
    return BackgroundJob(
        job_id=row["job_id"],
        idempotency_key=row["idempotency_key"],
        job_type=row["job_type"],
        target_id=row["target_id"],
        status=BackgroundJobStatus(row["status"]),
        priority=row["priority"],
        payload=json.loads(row["job_json"]) if row["job_json"] else {},
        lease_owner=row["lease_owner"],
        lease_expires_at=(
            datetime.fromisoformat(row["lease_expires_at"])
            if row["lease_expires_at"] else None
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class RuntimeJobQueue:
    """Durable, bounded, FIFO job queue for Phase 8's own in-process
    housekeeping (corpus reindex scheduling, cache eviction, eval
    scheduling). This is deliberately NOT `project_workers` (Docker-isolated
    project execution) or `backend/app/jobs` (legacy analysis) -- it imitates
    `local_ai_scheduler_jobs`'s claim/lease pattern one-to-one
    (`local_ai/service.py:1017-1118`) for crash recovery and reload safety,
    scoped to lightweight runtime maintenance only.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def enqueue(
        self,
        *,
        job_type: str,
        target_id: str,
        idempotency_key: str,
        payload: dict,
        priority: int = 100,
    ) -> BackgroundJob:
        request = {"job_type": job_type, "target_id": target_id, "payload": payload, "priority": priority}
        request_hash = content_hash(request)
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_hash, " + _JOB_COLUMNS + " "
                "FROM runtime_background_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                if row["request_hash"] != request_hash:
                    connection.execute("COMMIT")
                    raise RuntimeJobQueueError("idempotency_payload_mismatch")
                connection.execute("COMMIT")
                return _row_to_job(row)
            queued_count = int(connection.execute(
                "SELECT COUNT(*) FROM runtime_background_jobs WHERE status = 'queued'"
            ).fetchone()[0])
            if queued_count >= MAX_QUEUE_DEPTH:
                connection.execute("COMMIT")
                raise RuntimeJobQueueError("queue_depth_exceeded")
            job_id = f"rtj-{uuid4().hex}"
            connection.execute(
                "INSERT INTO runtime_background_jobs "
                "(job_id, idempotency_key, request_hash, job_type, target_id, status, priority, "
                "job_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                (
                    job_id, idempotency_key, request_hash, job_type, target_id, priority,
                    canonical_json(payload), now.isoformat(), now.isoformat(),
                ),
            )
            connection.execute("COMMIT")
            return BackgroundJob(
                job_id=job_id, idempotency_key=idempotency_key, job_type=job_type,
                target_id=target_id, status=BackgroundJobStatus.QUEUED, priority=priority,
                payload=payload, created_at=now, updated_at=now,
            )

    def recover_expired_jobs(self) -> int:
        now = _now()
        recovered = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT job_id, lease_expires_at FROM runtime_background_jobs "
                "WHERE status IN ('claimed', 'running')"
            ).fetchall()
            for row in rows:
                expires_at = row["lease_expires_at"]
                if expires_at is None or datetime.fromisoformat(expires_at) > now:
                    continue
                connection.execute(
                    "UPDATE runtime_background_jobs SET status = 'queued', lease_owner = NULL, "
                    "lease_expires_at = NULL, updated_at = ? WHERE job_id = ?",
                    (now.isoformat(), row["job_id"]),
                )
                recovered += 1
            connection.execute("COMMIT")
        return recovered

    def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> BackgroundJob | None:
        now = _now()
        self.recover_expired_jobs()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active_targets = {
                row["target_id"]
                for row in connection.execute(
                    "SELECT target_id FROM runtime_background_jobs WHERE status IN ('claimed', 'running')"
                )
            }
            queued = connection.execute(
                "SELECT job_id, idempotency_key, job_type, target_id, priority "
                "FROM runtime_background_jobs WHERE status = 'queued' "
                "ORDER BY priority, created_at, job_id"
            ).fetchall()
            selected = next(
                (row for row in queued if row["target_id"] not in active_targets), None
            )
            if selected is None:
                connection.execute("COMMIT")
                return None
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            cursor = connection.execute(
                "UPDATE runtime_background_jobs SET status = 'claimed', lease_owner = ?, "
                "lease_expires_at = ?, updated_at = ? WHERE job_id = ? AND status = 'queued'",
                (worker_id, lease_expires_at.isoformat(), now.isoformat(), selected["job_id"]),
            )
            if cursor.rowcount != 1:
                connection.execute("COMMIT")
                return None
            row = connection.execute(
                "SELECT " + _JOB_COLUMNS + " FROM runtime_background_jobs WHERE job_id = ?",
                (selected["job_id"],),
            ).fetchone()
            connection.execute("COMMIT")
            return _row_to_job(row)

    def complete_job(self, job_id: str, *, worker_id: str, succeeded: bool) -> BackgroundJob:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT " + _JOB_COLUMNS + " FROM runtime_background_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                raise RuntimeJobQueueError("job_not_found")
            job = _row_to_job(row)
            if job.status in {BackgroundJobStatus.COMPLETED, BackgroundJobStatus.FAILED}:
                connection.execute("COMMIT")
                return job
            if job.status != BackgroundJobStatus.CLAIMED or job.lease_owner != worker_id:
                connection.execute("COMMIT")
                raise RuntimeJobQueueError("lease_mismatch")
            new_status = BackgroundJobStatus.COMPLETED if succeeded else BackgroundJobStatus.FAILED
            connection.execute(
                "UPDATE runtime_background_jobs SET status = ?, lease_owner = NULL, "
                "lease_expires_at = NULL, updated_at = ? WHERE job_id = ?",
                (new_status.value, now.isoformat(), job_id),
            )
            connection.execute("COMMIT")
            return job.model_copy(update={"status": new_status, "lease_owner": None, "lease_expires_at": None})

    def status_summary(self, *, recent_limit: int = 20) -> dict:
        with self._connect() as connection:
            counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM runtime_background_jobs GROUP BY status"
                )
            }
            recent_rows = connection.execute(
                "SELECT " + _JOB_COLUMNS + " "
                "FROM runtime_background_jobs ORDER BY created_at DESC LIMIT ?",
                (recent_limit,),
            ).fetchall()
        return {
            "queued": counts.get("queued", 0),
            "claimed": counts.get("claimed", 0),
            "running": counts.get("running", 0),
            "recent": tuple(_row_to_job(row) for row in recent_rows),
        }
