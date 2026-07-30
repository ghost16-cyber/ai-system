from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.project_control import (
    ExecutionAttemptStatus,
    ExecutionAttemptType,
    ProjectControlPlane,
    ProjectEvent,
)
from backend.app.project_delivery import (
    build_delivery_action,
    cancel_delivery,
    record_patch_applied,
    record_rollback,
)


class ProjectProjectionService:
    """Projects ordered canonical events into legacy delivery/chat read models only."""

    def __init__(self, database_path: str | Path, control: ProjectControlPlane) -> None:
        self.database_path = Path(database_path)
        self.control = control

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def project_event(self, event: ProjectEvent) -> bool:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            checkpoint = connection.execute(
                "SELECT last_event_sequence FROM project_projection_checkpoints "
                "WHERE project_run_id = ?",
                (event.project_run_id,),
            ).fetchone()
            last_sequence = int(checkpoint["last_event_sequence"]) if checkpoint else 0
            if event.sequence <= last_sequence:
                connection.execute("COMMIT")
                return False
            if event.sequence != last_sequence + 1:
                raise ValueError(
                    f"projection_event_gap:{last_sequence + 1}:{event.sequence}"
                )

            row = connection.execute(
                "SELECT job_json FROM project_delivery_jobs WHERE delivery_job_id = ?",
                (event.project_run_id,),
            ).fetchone()
            if row is not None:
                job = json.loads(str(row["job_json"]))
                if not isinstance(job, dict):
                    raise ValueError("malformed_compatibility_projection")
                projected = self._project_job(job)
                connection.execute(
                    "UPDATE project_delivery_jobs SET status = ?, canonical_generation = ?, "
                    "job_json = ?, updated_at = ? WHERE delivery_job_id = ?",
                    (
                        str(projected.get("status") or job.get("status") or "blocked"),
                        str(projected.get("canonical_generation") or "canonical"),
                        json.dumps(projected, sort_keys=True),
                        str(projected.get("updated_at") or event.created_at.isoformat()),
                        event.project_run_id,
                    ),
                )
                self._project_chat_action(connection, projected)

            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                "INSERT INTO project_projection_checkpoints "
                "(project_run_id, last_event_sequence, last_event_id, status, failure_message, updated_at) "
                "VALUES (?, ?, ?, 'current', NULL, ?) "
                "ON CONFLICT(project_run_id) DO UPDATE SET "
                "last_event_sequence = excluded.last_event_sequence, "
                "last_event_id = excluded.last_event_id, status = 'current', "
                "failure_message = NULL, updated_at = excluded.updated_at",
                (event.project_run_id, event.sequence, event.event_id, now),
            )
            connection.execute("COMMIT")
            return True
        except Exception as error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            self._pause(event.project_run_id, error)
            raise
        finally:
            connection.close()

    def rebuild_project(self, project_run_id: str) -> tuple[str, ...]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_event_sequence FROM project_projection_checkpoints WHERE project_run_id = ?",
                (project_run_id,),
            ).fetchone()
            last_sequence = int(row["last_event_sequence"]) if row else 0
        projected: list[str] = []
        for event in self.control.list_events(project_run_id):
            if event.sequence <= last_sequence:
                continue
            if self.project_event(event):
                projected.append(event.event_id)
            last_sequence = event.sequence
        return tuple(projected)

    def rebuild_all(self, *, limit: int = 100) -> dict[str, tuple[str, ...]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT r.project_run_id FROM project_runs r "
                "LEFT JOIN project_projection_checkpoints p ON p.project_run_id = r.project_run_id "
                "WHERE p.project_run_id IS NULL OR p.status != 'current' OR "
                "p.last_event_sequence < (SELECT COALESCE(MAX(e.sequence), 0) "
                "FROM project_events e WHERE e.project_run_id = r.project_run_id) "
                "ORDER BY r.updated_at, r.project_run_id LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        rebuilt: dict[str, tuple[str, ...]] = {}
        for row in rows:
            project_run_id = str(row["project_run_id"])
            try:
                rebuilt[project_run_id] = self.rebuild_project(project_run_id)
            except (LookupError, RuntimeError, ValueError, sqlite3.DatabaseError):
                continue
        return rebuilt

    def _project_job(self, job: dict[str, Any]) -> dict[str, Any]:
        project_run_id = str(job["delivery_job_id"])
        projected = json.loads(json.dumps(job))
        attempts = self.control.list_attempts(project_run_id)
        completed_rollback = next((
            item for item in reversed(attempts)
            if item.attempt_type == ExecutionAttemptType.ROLLBACK
            and item.status == ExecutionAttemptStatus.COMPLETED
            and item.resulting_manifest_hash
        ), None)
        if completed_rollback is not None:
            patch_id = str(
                completed_rollback.authority.get("rollback_id") or ""
            ).removeprefix("rollback:")
            reference = next((
                item for item in projected.get("patch_references") or []
                if item.get("patch_id") == patch_id
            ), None)
            if reference is not None and reference.get("status") == "applied":
                projected = record_rollback(
                    projected,
                    patch_id=patch_id,
                    restored_state_hash=str(completed_rollback.resulting_manifest_hash),
                )
        else:
            completed_patch = next((
                item for item in reversed(attempts)
                if item.attempt_type == ExecutionAttemptType.PATCH
                and item.status == ExecutionAttemptStatus.COMPLETED
                and item.resulting_manifest_hash
            ), None)
            if completed_patch is not None:
                patch_id = str(completed_patch.authority.get("patch_id") or "")
                reference = next((
                    item for item in projected.get("patch_references") or []
                    if item.get("patch_id") == patch_id
                ), None)
                if reference is not None and reference.get("status") != "applied":
                    projected = record_patch_applied(
                        projected,
                        patch_id=patch_id,
                        current_state_hash=str(completed_patch.resulting_manifest_hash),
                    )
        read_model = self.control.get_read_model(project_run_id)
        if read_model.lifecycle_state.value == "cancelled" and projected.get("status") != "cancelled":
            projected = cancel_delivery(projected)
        control_projection = read_model.model_dump(mode="json")
        control_projection["projection_lag"] = max(
            0, int(control_projection.get("projection_lag") or 0) - 1
        )
        control_projection["projection_status"] = (
            "current"
            if control_projection["projection_lag"] == 0
            else "catching_up"
        )
        control_projection["projection_failure_classification"] = None
        projected["project_control"] = control_projection
        projected["canonical_generation"] = "canonical"
        projected["updated_at"] = datetime.now(timezone.utc).isoformat()
        return projected

    @staticmethod
    def _project_chat_action(connection: sqlite3.Connection, job: dict[str, Any]) -> None:
        action = build_delivery_action(job)
        rows = connection.execute(
            "SELECT run_id, action_json FROM chat_runs WHERE conversation_id = ? "
            "AND action_json IS NOT NULL",
            (str(job.get("conversation_id") or ""),),
        ).fetchall()
        for row in rows:
            try:
                current = json.loads(str(row["action_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(current, dict) and current.get("action_id") == job.get("delivery_job_id"):
                connection.execute(
                    "UPDATE chat_runs SET action_json = ? WHERE run_id = ?",
                    (json.dumps(action, sort_keys=True), str(row["run_id"])),
                )

    def _pause(self, project_run_id: str, error: Exception) -> None:
        failure = (
            "projection_event_gap"
            if str(error).startswith("projection_event_gap:")
            else f"projection_failed:{type(error).__name__}"
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO project_projection_checkpoints "
                "(project_run_id, last_event_sequence, last_event_id, status, failure_message, updated_at) "
                "VALUES (?, 0, NULL, 'paused', ?, ?) "
                "ON CONFLICT(project_run_id) DO UPDATE SET status = 'paused', "
                "failure_message = excluded.failure_message, updated_at = excluded.updated_at",
                (project_run_id, failure, now),
            )


__all__ = ["ProjectProjectionService"]
