from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.app.database.migrations import assert_schema_compatible


class RetrievalPersistenceError(RuntimeError):
    pass


class RetrievalStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        assert_schema_compatible(self.database_path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

