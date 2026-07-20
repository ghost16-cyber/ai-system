from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from backend.app.folders.safety import (
    ProjectSafetyError,
    resolve_project_path,
    safe_relative_path,
    validate_root_identity,
)
from backend.app.folders.scanner import classify_file
from backend.app.project_analysis.state_manifest import build_project_state_manifest
from backend.app.project_control.contracts import content_hash
from backend.app.project_workers.mutation_contracts import (
    FileMutationResult,
    FileMutationSpec,
    FileOperationKind,
    FileOperationSpec,
)


MUTATION_JOURNAL_VERSION = "astra.project-workers.mutation-journal.v1"
_SAFE_NAMES = frozenset({
    "Dockerfile", "Makefile", "Procfile", "pyproject.toml", "package.json",
    "package-lock.json", "requirements.txt", "setup.cfg", "tox.ini",
    "tsconfig.json", "eslint.config.js", ".eslintrc", ".gitignore",
})
_SAFE_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".json", ".toml",
    ".yaml", ".yml", ".ini", ".cfg", ".md", ".txt", ".css", ".html",
})


class MutationJournalStatus(StrEnum):
    PREPARING = "preparing"
    PREPARED = "prepared"
    COMMIT_STARTED = "commit_started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    RECOVERY_FAILED = "recovery_failed"


class FileMutationErrorCode(StrEnum):
    INVALID_SPEC = "invalid_spec"
    STALE_MANIFEST = "stale_manifest"
    PREIMAGE_MISMATCH = "preimage_mismatch"
    CANCELLED = "cancelled"
    COMMIT_FAILED = "commit_failed"
    RESULT_MANIFEST_MISMATCH = "result_manifest_mismatch"
    ROLLBACK_FAILED = "rollback_failed"
    PERSISTENCE_CONFLICT = "persistence_conflict"


class FileMutationError(RuntimeError):
    def __init__(self, code: FileMutationErrorCode | str, message: str) -> None:
        super().__init__(message)
        self.code = FileMutationErrorCode(code)
        self.message = " ".join(str(message).split())[:600]


class FileMutationEngine:
    """Applies an immutable exact file set with durable recovery snapshots."""

    def __init__(
        self,
        database_path: str | Path,
        journal_root: str | Path,
        *,
        replace_file: Callable[[str | Path, str | Path], None] = os.replace,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.journal_root = Path(journal_root).expanduser().resolve()
        self._replace_file = replace_file

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_file_mutation_specs (
                    file_mutation_id TEXT PRIMARY KEY,
                    project_run_id TEXT NOT NULL,
                    execution_attempt_id TEXT NOT NULL,
                    mutation_kind TEXT NOT NULL,
                    authority_id TEXT NOT NULL,
                    spec_hash TEXT NOT NULL UNIQUE,
                    schema_version TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_file_mutation_project
                    ON project_file_mutation_specs(project_run_id, created_at);

                CREATE TABLE IF NOT EXISTS project_file_mutation_journals (
                    file_mutation_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    applied_operations INTEGER NOT NULL DEFAULT 0,
                    journal_json TEXT NOT NULL,
                    result_json TEXT,
                    failure_classification TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(file_mutation_id)
                        REFERENCES project_file_mutation_specs(file_mutation_id)
                );
                CREATE INDEX IF NOT EXISTS idx_file_mutation_journal_status
                    ON project_file_mutation_journals(status, updated_at);

                CREATE TABLE IF NOT EXISTS project_file_mutation_snapshots (
                    file_mutation_id TEXT NOT NULL,
                    operation_index INTEGER NOT NULL,
                    relative_path TEXT NOT NULL,
                    existed_before INTEGER NOT NULL,
                    preimage_sha256 TEXT,
                    snapshot_path TEXT,
                    original_mode INTEGER,
                    staged_path TEXT,
                    PRIMARY KEY(file_mutation_id, operation_index),
                    FOREIGN KEY(file_mutation_id)
                        REFERENCES project_file_mutation_specs(file_mutation_id)
                );
                """
            )

    def apply(
        self,
        spec: FileMutationSpec | dict[str, Any],
        *,
        cancel_requested: Callable[[], bool] = lambda: False,
    ) -> FileMutationResult:
        parsed = self._parse_spec(spec)
        self._register_spec(parsed)
        existing = self._stored_result(parsed.file_mutation_id)
        if existing is not None:
            if not self._matches_result(parsed):
                raise FileMutationError(
                    FileMutationErrorCode.PREIMAGE_MISMATCH,
                    "A completed mutation no longer matches its exact recorded result.",
                )
            return existing.model_copy(update={"exact_replay": True})
        prior_status = self._journal_status(parsed.file_mutation_id)
        if prior_status is not None:
            raise FileMutationError(
                FileMutationErrorCode.PERSISTENCE_CONFLICT,
                f"The mutation already has a durable {prior_status.value} journal and cannot rerun.",
            )

        root = self._validate_root(parsed)
        if self._matches_result(parsed, root=root):
            result = self._result(parsed, root, exact_replay=True)
            self._complete_without_commit(parsed, result)
            return result
        self._require_manifest(root, parsed.manifest_hash, parsed.workspace_id)
        targets = self._validate_preimages(parsed, root)
        mutation_root = self.journal_root / parsed.file_mutation_id
        snapshots_root = mutation_root / "snapshots"
        snapshots_root.mkdir(parents=True, exist_ok=True)
        self._begin_journal(parsed)

        records: list[dict[str, Any]] = []
        try:
            for index, (operation, target) in enumerate(zip(parsed.operations, targets, strict=True)):
                record = self._stage_operation(
                    parsed,
                    index,
                    operation,
                    target,
                    snapshots_root,
                )
                records.append(record)
                self._store_snapshot_record(parsed.file_mutation_id, record)
            self._set_status(parsed.file_mutation_id, MutationJournalStatus.PREPARED, 0)
            if cancel_requested():
                self._set_status(
                    parsed.file_mutation_id,
                    MutationJournalStatus.CANCELLED,
                    0,
                    failure="cancelled_before_commit",
                )
                self._cleanup_staged(records)
                raise FileMutationError(
                    FileMutationErrorCode.CANCELLED,
                    "The mutation was cancelled before the commit phase.",
                )

            self._set_status(parsed.file_mutation_id, MutationJournalStatus.COMMIT_STARTED, 0)
            for index, (operation, target, record) in enumerate(
                zip(parsed.operations, targets, records, strict=True)
            ):
                self._commit_operation(operation.operation, target, record)
                self._set_status(
                    parsed.file_mutation_id,
                    MutationJournalStatus.COMMIT_STARTED,
                    index + 1,
                )

            resulting = self._manifest(root, parsed.workspace_id)
            if resulting != parsed.expected_result_manifest_hash:
                raise FileMutationError(
                    FileMutationErrorCode.RESULT_MANIFEST_MISMATCH,
                    "The committed files did not produce the approved result manifest.",
                )
            self._cleanup_staged(records)
            result = self._result(parsed, root)
            self._store_result(parsed.file_mutation_id, result)
            return result
        except FileMutationError as error:
            if error.code == FileMutationErrorCode.CANCELLED:
                raise
            self._rollback(parsed, root, records, targets, error.code.value)
            raise
        except Exception as error:
            self._rollback(parsed, root, records, targets, "commit_failed")
            raise FileMutationError(
                FileMutationErrorCode.COMMIT_FAILED,
                f"The exact file mutation failed and was rolled back: {error}",
            ) from error

    def recover_incomplete(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT file_mutation_id, status FROM project_file_mutation_journals "
                "WHERE status IN ('preparing', 'prepared', 'commit_started', 'rolling_back') "
                "ORDER BY created_at, file_mutation_id"
            ).fetchall()
        recovered: list[str] = []
        for row in rows:
            mutation_id = str(row["file_mutation_id"])
            spec = self._load_spec(mutation_id)
            root = self._validate_root(spec)
            records = self._load_snapshot_records(mutation_id)
            targets = [self._target(root, operation.relative_path, must_exist=False) for operation in spec.operations]
            if str(row["status"]) in {
                MutationJournalStatus.PREPARING.value,
                MutationJournalStatus.PREPARED.value,
            }:
                self._cleanup_staged(records)
                self._set_status(
                    mutation_id,
                    MutationJournalStatus.CANCELLED,
                    0,
                    failure="recovered_before_commit",
                )
                recovered.append(mutation_id)
                continue
            try:
                self._rollback(spec, root, records, targets, "recovered_incomplete_commit", recovered=True)
                recovered.append(mutation_id)
            except FileMutationError:
                continue
        return tuple(recovered)

    def _parse_spec(self, spec: FileMutationSpec | dict[str, Any]) -> FileMutationSpec:
        try:
            value = spec.model_dump(mode="json") if isinstance(spec, FileMutationSpec) else spec
            return FileMutationSpec.model_validate(value)
        except Exception as error:
            raise FileMutationError(
                FileMutationErrorCode.INVALID_SPEC,
                "The file mutation did not match its immutable contract.",
            ) from error

    def _register_spec(self, spec: FileMutationSpec) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT spec_hash FROM project_file_mutation_specs WHERE file_mutation_id = ?",
                (spec.file_mutation_id,),
            ).fetchone()
            if row is not None:
                if str(row["spec_hash"]) != spec.spec_hash:
                    connection.execute("ROLLBACK")
                    raise FileMutationError(
                        FileMutationErrorCode.PERSISTENCE_CONFLICT,
                        "The mutation identity is already bound to different content.",
                    )
                connection.execute("COMMIT")
                return
            connection.execute(
                "INSERT INTO project_file_mutation_specs "
                "(file_mutation_id, project_run_id, execution_attempt_id, mutation_kind, "
                "authority_id, spec_hash, schema_version, spec_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec.file_mutation_id,
                    spec.project_run_id,
                    spec.execution_attempt_id,
                    spec.mutation_kind.value,
                    spec.authority_id,
                    spec.spec_hash,
                    spec.schema_version,
                    spec.model_dump_json(),
                    spec.created_at.isoformat(),
                ),
            )
            connection.execute("COMMIT")

    def _begin_journal(self, spec: FileMutationSpec) -> None:
        now = datetime.now(timezone.utc).isoformat()
        journal = {
            "schema_version": MUTATION_JOURNAL_VERSION,
            "file_mutation_id": spec.file_mutation_id,
            "status": MutationJournalStatus.PREPARING.value,
            "applied_operations": 0,
        }
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO project_file_mutation_journals "
                "(file_mutation_id, status, applied_operations, journal_json, created_at, updated_at) "
                "VALUES (?, ?, 0, ?, ?, ?)",
                (
                    spec.file_mutation_id,
                    MutationJournalStatus.PREPARING.value,
                    json.dumps(journal, sort_keys=True),
                    now,
                    now,
                ),
            )

    def _set_status(
        self,
        mutation_id: str,
        status_value: MutationJournalStatus,
        applied: int,
        *,
        failure: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        journal = {
            "schema_version": MUTATION_JOURNAL_VERSION,
            "file_mutation_id": mutation_id,
            "status": status_value.value,
            "applied_operations": applied,
            "failure_classification": failure,
        }
        with self._connect() as connection:
            connection.execute(
                "UPDATE project_file_mutation_journals SET status = ?, applied_operations = ?, "
                "journal_json = ?, failure_classification = ?, updated_at = ? "
                "WHERE file_mutation_id = ?",
                (
                    status_value.value,
                    applied,
                    json.dumps(journal, sort_keys=True),
                    failure,
                    now,
                    mutation_id,
                ),
            )

    def _stage_operation(self, spec, index, operation, target, snapshots_root) -> dict[str, Any]:
        existed = target.exists()
        snapshot_path: Path | None = None
        original_mode: int | None = None
        if existed:
            snapshot_path = snapshots_root / f"{index:04d}.bin"
            shutil.copyfile(target, snapshot_path)
            original_mode = stat.S_IMODE(target.stat().st_mode)
        staged_path: Path | None = None
        if operation.operation != FileOperationKind.DELETE:
            staged_path = target.parent / f".astra-{spec.file_mutation_id}-{index:04d}.tmp"
            if staged_path.exists() or staged_path.is_symlink():
                raise FileMutationError(
                    FileMutationErrorCode.PERSISTENCE_CONFLICT,
                    "A mutation staging path already exists.",
                )
            staged_path.write_text(operation.new_content or "", encoding="utf-8")
            os.chmod(staged_path, original_mode if original_mode is not None else 0o644)
            with staged_path.open("rb") as handle:
                os.fsync(handle.fileno())
        return {
            "operation_index": index,
            "relative_path": operation.relative_path,
            "existed_before": existed,
            "preimage_sha256": operation.preimage_sha256,
            "snapshot_path": str(snapshot_path) if snapshot_path else None,
            "original_mode": original_mode,
            "staged_path": str(staged_path) if staged_path else None,
        }

    def _store_snapshot_record(self, mutation_id: str, record: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO project_file_mutation_snapshots "
                "(file_mutation_id, operation_index, relative_path, existed_before, "
                "preimage_sha256, snapshot_path, original_mode, staged_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mutation_id,
                    record["operation_index"],
                    record["relative_path"],
                    1 if record["existed_before"] else 0,
                    record["preimage_sha256"],
                    record["snapshot_path"],
                    record["original_mode"],
                    record["staged_path"],
                ),
            )

    def _commit_operation(self, kind: FileOperationKind, target: Path, record: dict[str, Any]) -> None:
        if kind == FileOperationKind.DELETE:
            target.unlink()
            return
        staged = Path(str(record["staged_path"]))
        self._replace_file(staged, target)

    def _rollback(self, spec, root, records, targets, failure, *, recovered=False) -> None:
        self._set_status(
            spec.file_mutation_id,
            MutationJournalStatus.ROLLING_BACK,
            0,
            failure=failure,
        )
        try:
            for target, record in reversed(list(zip(targets, records, strict=False))):
                snapshot_value = record.get("snapshot_path")
                if bool(record.get("existed_before")):
                    snapshot = Path(str(snapshot_value))
                    if not snapshot.is_file():
                        raise OSError("A required recovery snapshot is missing.")
                    restore = target.parent / f".astra-restore-{spec.file_mutation_id}.tmp"
                    shutil.copyfile(snapshot, restore)
                    if record.get("original_mode") is not None:
                        os.chmod(restore, int(record["original_mode"]))
                    os.replace(restore, target)
                elif target.exists():
                    if target.is_symlink() or not target.is_file():
                        raise OSError("A created mutation path became unsafe during rollback.")
                    target.unlink()
            self._cleanup_staged(records)
            restored = self._manifest(root, spec.workspace_id)
            if restored != spec.manifest_hash:
                raise OSError("Rollback did not restore the approved pre-mutation manifest.")
            self._set_status(
                spec.file_mutation_id,
                MutationJournalStatus.ROLLED_BACK,
                0,
                failure=failure if not recovered else "recovered_incomplete_commit",
            )
        except Exception as error:
            self._set_status(
                spec.file_mutation_id,
                MutationJournalStatus.RECOVERY_FAILED,
                0,
                failure="rollback_failed",
            )
            raise FileMutationError(
                FileMutationErrorCode.ROLLBACK_FAILED,
                f"The mutation rollback could not restore the exact preimage: {error}",
            ) from error

    def _validate_root(self, spec: FileMutationSpec) -> Path:
        try:
            return validate_root_identity(
                spec.repository_root,
                spec.repository_root_fingerprint,
            )
        except (ProjectSafetyError, OSError) as error:
            raise FileMutationError(
                FileMutationErrorCode.INVALID_SPEC,
                "The approved repository identity is no longer valid.",
            ) from error

    def _validate_preimages(self, spec: FileMutationSpec, root: Path) -> list[Path]:
        targets: list[Path] = []
        approved = {safe_relative_path(path) for path in spec.approved_paths}
        for operation in spec.operations:
            relative = safe_relative_path(operation.relative_path)
            if relative not in approved:
                raise FileMutationError(
                    FileMutationErrorCode.INVALID_SPEC,
                    "A file mutation path lacks exact approval.",
                )
            target = self._target(
                root,
                relative,
                must_exist=operation.operation != FileOperationKind.CREATE,
            )
            if target.name not in _SAFE_NAMES and target.suffix.lower() not in _SAFE_SUFFIXES:
                raise FileMutationError(
                    FileMutationErrorCode.INVALID_SPEC,
                    "The mutation contains an unsupported file type.",
                )
            if operation.operation == FileOperationKind.CREATE:
                if target.exists() or target.is_symlink() or not target.parent.is_dir():
                    raise FileMutationError(
                        FileMutationErrorCode.PREIMAGE_MISMATCH,
                        "A create target already exists or its parent is unsafe.",
                    )
            else:
                if target.is_symlink() or not target.is_file():
                    raise FileMutationError(
                        FileMutationErrorCode.PREIMAGE_MISMATCH,
                        "A mutation target is missing or no longer a regular file.",
                    )
                if _sha256_file(target) != operation.preimage_sha256:
                    raise FileMutationError(
                        FileMutationErrorCode.PREIMAGE_MISMATCH,
                        "A mutation preimage changed after approval.",
                    )
            targets.append(target)
        return targets

    def _target(self, root: Path, relative: str, *, must_exist: bool) -> Path:
        try:
            return resolve_project_path(root, relative, must_exist=must_exist)
        except (ProjectSafetyError, OSError, ValueError) as error:
            raise FileMutationError(
                FileMutationErrorCode.INVALID_SPEC,
                "A mutation path is outside the approved repository or unsafe.",
            ) from error

    def _require_manifest(self, root: Path, expected: str, workspace_id: str) -> None:
        if self._manifest(root, workspace_id) != expected:
            raise FileMutationError(
                FileMutationErrorCode.STALE_MANIFEST,
                "The repository manifest changed after mutation approval.",
            )

    def _manifest(self, root: Path, workspace_id: str) -> str:
        return build_project_state_manifest(root, workspace_id=workspace_id).manifest_hash

    def _matches_result(self, spec: FileMutationSpec, *, root: Path | None = None) -> bool:
        approved_root = root or self._validate_root(spec)
        for operation in spec.operations:
            target = self._target(approved_root, operation.relative_path, must_exist=False)
            if operation.operation == FileOperationKind.DELETE:
                if target.exists() or target.is_symlink():
                    return False
            elif not target.is_file() or target.is_symlink() or _sha256_file(target) != operation.result_sha256:
                return False
        return self._manifest(approved_root, spec.workspace_id) == spec.expected_result_manifest_hash

    def _result(self, spec, root, *, exact_replay=False, recovered=False) -> FileMutationResult:
        hashes: dict[str, str | None] = {}
        for operation in spec.operations:
            target = self._target(root, operation.relative_path, must_exist=False)
            hashes[operation.relative_path] = _sha256_file(target) if target.is_file() else None
        body = {
            "file_mutation_id": spec.file_mutation_id,
            "project_run_id": spec.project_run_id,
            "execution_attempt_id": spec.execution_attempt_id,
            "status": "completed",
            "resulting_manifest_hash": self._manifest(root, spec.workspace_id),
            "resulting_file_hashes": hashes,
            "exact_replay": exact_replay,
            "recovered": recovered,
            "failure_classification": None,
        }
        return FileMutationResult(**body, evidence_hash=content_hash(body))

    def _store_result(self, mutation_id: str, result: FileMutationResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE project_file_mutation_journals SET status = ?, applied_operations = ?, "
                "journal_json = ?, result_json = ?, failure_classification = NULL, updated_at = ? "
                "WHERE file_mutation_id = ?",
                (
                    MutationJournalStatus.COMPLETED.value,
                    len(result.resulting_file_hashes),
                    json.dumps({
                        "schema_version": MUTATION_JOURNAL_VERSION,
                        "file_mutation_id": mutation_id,
                        "status": MutationJournalStatus.COMPLETED.value,
                    }, sort_keys=True),
                    result.model_dump_json(),
                    now,
                    mutation_id,
                ),
            )

    def _complete_without_commit(self, spec, result) -> None:
        self._begin_journal(spec)
        self._store_result(spec.file_mutation_id, result)

    def _stored_result(self, mutation_id: str) -> FileMutationResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM project_file_mutation_journals "
                "WHERE file_mutation_id = ? AND status = 'completed'",
                (mutation_id,),
            ).fetchone()
        if row is None or not row["result_json"]:
            return None
        return FileMutationResult.model_validate_json(str(row["result_json"]))

    def _journal_status(self, mutation_id: str) -> MutationJournalStatus | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM project_file_mutation_journals WHERE file_mutation_id = ?",
                (mutation_id,),
            ).fetchone()
        return MutationJournalStatus(str(row["status"])) if row is not None else None

    def _load_spec(self, mutation_id: str) -> FileMutationSpec:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT spec_json FROM project_file_mutation_specs WHERE file_mutation_id = ?",
                (mutation_id,),
            ).fetchone()
        if row is None:
            raise FileMutationError(
                FileMutationErrorCode.PERSISTENCE_CONFLICT,
                "The durable mutation specification is missing.",
            )
        return FileMutationSpec.model_validate_json(str(row["spec_json"]))

    def _load_snapshot_records(self, mutation_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM project_file_mutation_snapshots WHERE file_mutation_id = ? "
                "ORDER BY operation_index",
                (mutation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def build_rollback_operations(
        self,
        file_mutation_id: str,
    ) -> tuple[FileOperationSpec, ...]:
        """Build the exact inverse of a completed UTF-8 mutation from durable snapshots."""
        spec = self._load_spec(file_mutation_id)
        result = self._stored_result(file_mutation_id)
        if result is None:
            raise FileMutationError(
                FileMutationErrorCode.PERSISTENCE_CONFLICT,
                "Rollback preparation requires a completed durable mutation.",
            )
        records = self._load_snapshot_records(file_mutation_id)
        if len(records) != len(spec.operations):
            raise FileMutationError(
                FileMutationErrorCode.PERSISTENCE_CONFLICT,
                "The durable mutation snapshot set is incomplete.",
            )

        inverse: list[FileOperationSpec] = []
        for operation, record in zip(spec.operations, records, strict=True):
            if not bool(record.get("existed_before")):
                inverse.append(FileOperationSpec(
                    relative_path=operation.relative_path,
                    operation=FileOperationKind.DELETE,
                    preimage_sha256=operation.result_sha256,
                ))
                continue
            snapshot_value = record.get("snapshot_path")
            if not snapshot_value:
                raise FileMutationError(
                    FileMutationErrorCode.PERSISTENCE_CONFLICT,
                    "A rollback preimage snapshot is missing.",
                )
            snapshot_path = Path(str(snapshot_value))
            try:
                original_content = snapshot_path.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise FileMutationError(
                    FileMutationErrorCode.INVALID_SPEC,
                    "Rollback currently supports exact UTF-8 project files only.",
                ) from error
            original_hash = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
            if operation.operation == FileOperationKind.DELETE:
                inverse.append(FileOperationSpec(
                    relative_path=operation.relative_path,
                    operation=FileOperationKind.CREATE,
                    result_sha256=original_hash,
                    new_content=original_content,
                ))
            else:
                inverse.append(FileOperationSpec(
                    relative_path=operation.relative_path,
                    operation=FileOperationKind.UPDATE,
                    preimage_sha256=operation.result_sha256,
                    result_sha256=original_hash,
                    new_content=original_content,
                ))
        return tuple(inverse)

    @staticmethod
    def _cleanup_staged(records: list[dict[str, Any]]) -> None:
        for record in records:
            value = record.get("staged_path")
            if value:
                path = Path(str(value))
                if path.exists() and path.is_file() and not path.is_symlink():
                    path.unlink()


def calculate_expected_manifest_hash(
    root: str | Path,
    *,
    workspace_id: str,
    operations,
) -> str:
    """Calculate the exact post-mutation manifest without touching the repository."""
    current = build_project_state_manifest(root, workspace_id=workspace_id)
    entries = {
        entry.normalized_relative_path: entry.model_dump(mode="json")
        for entry in current.entries
    }
    for operation in operations:
        relative = safe_relative_path(operation.relative_path)
        if operation.operation == FileOperationKind.DELETE:
            if relative not in entries:
                raise FileMutationError(
                    FileMutationErrorCode.PREIMAGE_MISMATCH,
                    "A deleted file is missing from the current manifest.",
                )
            entries.pop(relative)
            continue
        encoded = (operation.new_content or "").encode("utf-8")
        prior = entries.get(relative)
        if operation.operation == FileOperationKind.UPDATE and prior is None:
            raise FileMutationError(
                FileMutationErrorCode.PREIMAGE_MISMATCH,
                "An updated file is missing from the current manifest.",
            )
        entries[relative] = {
            "normalized_relative_path": relative,
            "file_type": (
                str(prior["file_type"])
                if prior is not None
                else classify_file(relative, Path(relative).suffix.lower())
            ),
            "size": len(encoded),
            "content_hash": operation.result_sha256,
            "mode": int(prior["mode"]) if prior is not None else 0o644,
        }
    content = {
        "schema_version": current.schema_version,
        "workspace_id": current.workspace_id,
        "root_fingerprint": current.root_fingerprint,
        "scanner_policy_version": current.scanner_policy_version,
        "entries": [entries[path] for path in sorted(entries)],
        "excluded_summary": current.excluded_summary,
        "limits": current.limits,
        "complete": current.complete,
        "incomplete_reasons": list(current.incomplete_reasons),
    }
    return content_hash(content)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "FileMutationEngine",
    "FileMutationError",
    "FileMutationErrorCode",
    "MutationJournalStatus",
    "calculate_expected_manifest_hash",
]
