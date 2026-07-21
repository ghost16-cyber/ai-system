from __future__ import annotations

import sqlite3
from pathlib import Path


class CompatibilityClassificationService:
    """Authoritative reader of the Stage 3H `project_compatibility_records` ledger.

    Mutation routes must consult this before any adaptation, reconciliation, audit
    write, or canonical command. A row tagged ``read_only = 1`` is historical and
    cannot be mutated implicitly; it must go through explicit import-and-reapproval.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def classification(self, source_id: str) -> str:
        """Return ``historical_read_only``, ``canonical``, or ``unclassified``."""
        if not source_id:
            return "unclassified"
        with self._connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'project_compatibility_records'"
            ).fetchone()
            if table is None:
                return "unclassified"
            row = connection.execute(
                "SELECT read_only, generation FROM project_compatibility_records "
                "WHERE source_id = ? ORDER BY read_only DESC LIMIT 1",
                (source_id,),
            ).fetchone()
        if row is None:
            return "unclassified"
        if int(row["read_only"]) == 1:
            return "historical_read_only"
        return "canonical"

    def is_historical_read_only(self, source_id: str) -> bool:
        return self.classification(source_id) == "historical_read_only"


__all__ = ["CompatibilityClassificationService"]
