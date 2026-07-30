from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.project_control.contracts import canonical_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimePersistence:
    """Raw-sqlite3 DAO for the migration-17 runtime tables. Every write here
    targets an append-only table (protected by the no-update/no-delete
    triggers created in migration 17) except `runtime_background_jobs`,
    which is a live queue -- see background/queue.py for its claim/lease
    writes.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def record_state_event(
        self,
        *,
        event_id: str,
        from_state: str,
        to_state: str,
        trigger: str,
        reason: str | None,
        detail: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_state_events "
                "(event_id, from_state, to_state, transition_trigger, reason, event_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, from_state, to_state, trigger, reason, canonical_json(detail), _now_iso()),
            )

    def recent_state_events(self, *, limit: int = 20) -> tuple[sqlite3.Row, ...]:
        with self._connect() as connection:
            return tuple(
                connection.execute(
                    "SELECT * FROM runtime_state_events ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            )

    def record_recovery_event(
        self,
        *,
        recovery_id: str,
        failure_class: str,
        subsystem_id: str,
        action: str,
        outcome: str,
        detail: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_recovery_events "
                "(recovery_id, failure_class, subsystem_id, action, outcome, recovery_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (recovery_id, failure_class, subsystem_id, action, outcome, canonical_json(detail), _now_iso()),
            )

    def recent_recovery_events(self, *, limit: int = 20) -> tuple[sqlite3.Row, ...]:
        with self._connect() as connection:
            return tuple(
                connection.execute(
                    "SELECT * FROM runtime_recovery_events ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            )

    def record_cache_statistics(
        self,
        *,
        stat_id: str,
        cache_id: str,
        hits: int,
        misses: int,
        evictions: int,
        size: int,
        version_tag: str | None,
        detail: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_cache_statistics "
                "(stat_id, cache_id, hits, misses, evictions, size, version_tag, stat_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (stat_id, cache_id, hits, misses, evictions, size, version_tag, canonical_json(detail), _now_iso()),
            )

    def latest_cache_statistics(self) -> tuple[sqlite3.Row, ...]:
        with self._connect() as connection:
            return tuple(
                connection.execute(
                    "SELECT t1.* FROM runtime_cache_statistics t1 "
                    "INNER JOIN ("
                    "  SELECT cache_id, MAX(created_at) AS created_at "
                    "  FROM runtime_cache_statistics GROUP BY cache_id"
                    ") t2 ON t1.cache_id = t2.cache_id AND t1.created_at = t2.created_at"
                )
            )

    def record_indexing_history(
        self,
        *,
        history_id: str,
        project_run_id: str,
        generation_id: str | None,
        trigger: str,
        files_changed: int,
        duration_ms: int | None,
        outcome: str,
        detail: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_indexing_history "
                "(history_id, project_run_id, generation_id, reindex_trigger, files_changed, "
                "duration_ms, outcome, history_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    history_id, project_run_id, generation_id, trigger, files_changed,
                    duration_ms, outcome, canonical_json(detail), _now_iso(),
                ),
            )

    def indexing_history_for_project(
        self, project_run_id: str, *, limit: int = 20
    ) -> tuple[sqlite3.Row, ...]:
        with self._connect() as connection:
            return tuple(
                connection.execute(
                    "SELECT * FROM runtime_indexing_history WHERE project_run_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (project_run_id, limit),
                )
            )

    def record_telemetry_snapshot(self, *, snapshot_id: str, detail: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_telemetry_snapshots (snapshot_id, snapshot_json, created_at) "
                "VALUES (?, ?, ?)",
                (snapshot_id, canonical_json(detail), _now_iso()),
            )

    def latest_telemetry_snapshot(self) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM runtime_telemetry_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
