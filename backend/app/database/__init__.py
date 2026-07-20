"""SQLite persistence for analysis metadata."""

from .repository import AnalysisRepository

from .migrations import (
    LATEST_SCHEMA_VERSION,
    MigrationError,
    MigrationResult,
    SchemaMigration,
    apply_schema_migrations,
    assert_schema_compatible,
    current_schema_version,
)

__all__ = [
    "AnalysisRepository",
    "LATEST_SCHEMA_VERSION",
    "MigrationError",
    "MigrationResult",
    "SchemaMigration",
    "apply_schema_migrations",
    "assert_schema_compatible",
    "current_schema_version",
]
