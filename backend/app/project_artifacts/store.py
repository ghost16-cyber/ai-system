from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import ValidationError

from backend.app.database.migrations import (
    apply_schema_migrations,
    assert_schema_compatible,
)
from backend.app.project_artifacts.contracts import ProjectArtifact, ProjectArtifactType


class ProjectArtifactStoreError(RuntimeError):
    pass


class ProjectArtifactStore:
    """Immutable, content-addressed storage for project evidence and previews."""

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

    def put(self, artifact: ProjectArtifact) -> ProjectArtifact:
        candidate = self._validated_artifact(artifact)
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM project_artifacts WHERE artifact_id = ?",
                    (candidate.artifact_id,),
                ).fetchone()
                if existing is not None:
                    stored = self._parse_row(existing)
                    if (
                        stored.artifact_type != candidate.artifact_type
                        or stored.binding_hash != candidate.binding_hash
                        or stored.content_hash != candidate.content_hash
                    ):
                        raise ProjectArtifactStoreError(
                            "artifact identity is already bound to different content"
                        )
                    connection.commit()
                    return stored
                same_content = connection.execute(
                    """SELECT * FROM project_artifacts
                       WHERE project_run_id = ? AND artifact_type = ?
                         AND binding_hash = ? AND content_hash = ?""",
                    (
                        candidate.binding.project_run_id,
                        candidate.artifact_type.value,
                        candidate.binding_hash,
                        candidate.content_hash,
                    ),
                ).fetchone()
                if same_content is not None:
                    stored = self._parse_row(same_content)
                    connection.commit()
                    return stored
                if candidate.revision_number is not None:
                    same_revision = connection.execute(
                        """SELECT * FROM project_artifacts
                           WHERE project_run_id = ? AND artifact_type = ?
                             AND binding_hash = ? AND revision_number = ?""",
                        (
                            candidate.binding.project_run_id,
                            candidate.artifact_type.value,
                            candidate.binding_hash,
                            candidate.revision_number,
                        ),
                    ).fetchone()
                    if same_revision is not None:
                        raise ProjectArtifactStoreError(
                            "artifact revision is already bound to different content"
                        )
                connection.execute(
                    """INSERT INTO project_artifacts (
                           artifact_id, project_run_id, artifact_type, binding_hash,
                           content_hash, schema_version, artifact_json, created_at,
                           revision_number
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        candidate.artifact_id,
                        candidate.binding.project_run_id,
                        candidate.artifact_type.value,
                        candidate.binding_hash,
                        candidate.content_hash,
                        candidate.schema_version,
                        candidate.model_dump_json(),
                        candidate.created_at.isoformat(),
                        candidate.revision_number,
                    ),
                )
                connection.commit()
                return candidate
            except Exception:
                connection.rollback()
                raise

    def get(self, artifact_id: str) -> ProjectArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM project_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return self._parse_row(row) if row is not None else None

    def list_for_project(
        self,
        project_run_id: str,
        *,
        artifact_type: ProjectArtifactType | str | None = None,
        limit: int = 100,
    ) -> list[ProjectArtifact]:
        bounded_limit = max(1, min(int(limit), 500))
        params: list[object] = [project_run_id]
        condition = "project_run_id = ?"
        if artifact_type is not None:
            condition += " AND artifact_type = ?"
            params.append(ProjectArtifactType(artifact_type).value)
        params.append(bounded_limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM project_artifacts WHERE {condition} "
                "ORDER BY created_at, artifact_id LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._parse_row(row) for row in rows]

    def verify(
        self,
        artifact_id: str,
        *,
        expected_binding_hash: str | None = None,
        expected_content_hash: str | None = None,
    ) -> ProjectArtifact:
        artifact = self.get(artifact_id)
        if artifact is None:
            raise ProjectArtifactStoreError("project artifact does not exist")
        if (
            expected_binding_hash is not None
            and artifact.binding_hash != expected_binding_hash
        ):
            raise ProjectArtifactStoreError("project artifact binding is stale")
        if (
            expected_content_hash is not None
            and artifact.content_hash != expected_content_hash
        ):
            raise ProjectArtifactStoreError(
                "project artifact content is corrupted or stale"
            )
        return artifact

    @staticmethod
    def _parse(value: str) -> ProjectArtifact:
        try:
            return ProjectArtifact.model_validate_json(value)
        except ValidationError as exc:
            raise ProjectArtifactStoreError(
                "stored project artifact failed integrity validation"
            ) from exc

    @staticmethod
    def _validated_artifact(artifact: ProjectArtifact) -> ProjectArtifact:
        plain = artifact.model_dump(mode="python", round_trip=True)
        return ProjectArtifact.model_validate(plain)

    @classmethod
    def _parse_row(cls, row: sqlite3.Row) -> ProjectArtifact:
        artifact = cls._parse(str(row["artifact_json"]))
        normalized = (
            (str(row["artifact_id"]), artifact.artifact_id),
            (str(row["project_run_id"]), artifact.binding.project_run_id),
            (str(row["artifact_type"]), artifact.artifact_type.value),
            (str(row["binding_hash"]), artifact.binding_hash),
            (str(row["content_hash"]), artifact.content_hash),
            (str(row["schema_version"]), artifact.schema_version),
            (str(row["created_at"]), artifact.created_at.isoformat()),
        )
        if any(actual != expected for actual, expected in normalized):
            raise ProjectArtifactStoreError(
                "stored project artifact normalized fields failed integrity validation"
            )
        normalized_revision = row["revision_number"]
        if (
            (int(normalized_revision) if normalized_revision is not None else None)
            != artifact.revision_number
        ):
            raise ProjectArtifactStoreError(
                "stored project artifact revision failed integrity validation"
            )
        return artifact
