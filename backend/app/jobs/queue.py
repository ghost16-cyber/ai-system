from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.schemas.api import JobResponse, JobStatus


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    job_type: str
    payload: dict[str, object]


class JobQueue:
    """SQLite-backed queue for locally executed allowlisted jobs."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    cancel_requested_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at
                ON jobs (status, created_at)
                """
            )

    def enqueue(self, job_type: str, payload: dict[str, object]) -> JobResponse:
        job_id = str(uuid4())
        created_at = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, job_type, status, payload_json, created_at
                ) VALUES (?, ?, 'queued', ?, ?)
                """,
                (job_id, job_type, json.dumps(payload, sort_keys=True), created_at),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> JobResponse:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Job not found.")
        return _to_response(row)

    def list_jobs(
        self,
        *,
        limit: int,
        status: JobStatus | None = None,
    ) -> list[JobResponse]:
        with self._connect() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
        return [_to_response(row) for row in rows]

    def request_cancel(self, job_id: str) -> JobResponse:
        requested_at = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise LookupError("Job not found.")
            if row["status"] == "queued":
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'cancelled',
                        cancel_requested_at = ?,
                        finished_at = ?
                    WHERE job_id = ?
                    """,
                    (requested_at, requested_at, job_id),
                )
            elif row["status"] == "running":
                connection.execute(
                    """
                    UPDATE jobs
                    SET cancel_requested_at = COALESCE(cancel_requested_at, ?)
                    WHERE job_id = ?
                    """,
                    (requested_at, job_id),
                )
        return self.get(job_id)

    def claim_next(self) -> ClaimedJob | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT job_id, job_type, payload_json
                FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE jobs
                SET status = 'running', started_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (_now(), row["job_id"]),
            )

        return ClaimedJob(
            job_id=str(row["job_id"]),
            job_type=str(row["job_type"]),
            payload=json.loads(row["payload_json"]),
        )

    def succeed(self, job_id: str, result: dict[str, object]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', result_json = ?, finished_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (json.dumps(result, sort_keys=True), _now(), job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', error = ?, finished_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (error, _now(), job_id),
            )

    def recover_interrupted(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error = 'Worker stopped while this job was running.',
                    finished_at = ?
                WHERE status = 'running'
                """,
                (_now(),),
            )
        return cursor.rowcount


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_response(row: sqlite3.Row) -> JobResponse:
    return JobResponse(
        job_id=str(row["job_id"]),
        job_type=str(row["job_type"]),
        status=row["status"],
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        cancel_requested_at=row["cancel_requested_at"],
    )
