from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from backend.app.project_control.contracts import StrictModel, content_hash


FILE_MUTATION_SPEC_VERSION = "astra.project-workers.file-mutation-spec.v1"
FILE_MUTATION_RESULT_VERSION = "astra.project-workers.file-mutation-result.v1"


class FileMutationKind(StrEnum):
    PATCH = "patch"
    ROLLBACK = "rollback"


class FileOperationKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class FileOperationSpec(StrictModel):
    relative_path: str = Field(min_length=1, max_length=1000)
    operation: FileOperationKind
    preimage_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    result_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    new_content: str | None = Field(default=None, max_length=2_000_000)

    @model_validator(mode="after")
    def validate_operation(self) -> "FileOperationSpec":
        if self.operation == FileOperationKind.CREATE:
            if self.preimage_sha256 is not None or self.new_content is None:
                raise ValueError("Create operations require new content and no preimage hash.")
        elif self.operation == FileOperationKind.UPDATE:
            if self.preimage_sha256 is None or self.new_content is None:
                raise ValueError("Update operations require a preimage hash and new content.")
        elif self.operation == FileOperationKind.DELETE:
            if self.preimage_sha256 is None or self.new_content is not None:
                raise ValueError("Delete operations require a preimage hash and no new content.")
            if self.result_sha256 is not None:
                raise ValueError("Delete operations cannot declare a result hash.")
            return self
        expected = hashlib.sha256(self.new_content.encode("utf-8")).hexdigest()
        if self.result_sha256 != expected:
            raise ValueError("The declared result hash does not match the exact new content.")
        return self


class FileMutationSpec(StrictModel):
    schema_version: str = FILE_MUTATION_SPEC_VERSION
    file_mutation_id: str = Field(min_length=1, max_length=160)
    project_run_id: str = Field(min_length=1, max_length=160)
    execution_attempt_id: str = Field(min_length=1, max_length=160)
    mutation_kind: FileMutationKind
    authority_id: str = Field(min_length=1, max_length=160)
    repository_root: str = Field(min_length=1, max_length=4000)
    repository_root_fingerprint: str = Field(min_length=64, max_length=64)
    workspace_id: str = Field(min_length=1, max_length=160)
    plan_revision_id: str = Field(min_length=1, max_length=160)
    scope_revision_id: str = Field(min_length=1, max_length=160)
    manifest_hash: str = Field(min_length=64, max_length=64)
    expected_result_manifest_hash: str = Field(min_length=64, max_length=64)
    approved_paths: tuple[str, ...] = Field(min_length=1, max_length=100)
    operations: tuple[FileOperationSpec, ...] = Field(min_length=1, max_length=100)
    operation_sequence: tuple[str, ...] = Field(min_length=1, max_length=100)
    spec_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime

    @model_validator(mode="after")
    def validate_bindings(self) -> "FileMutationSpec":
        paths = tuple(operation.relative_path for operation in self.operations)
        if len(set(paths)) != len(paths):
            raise ValueError("A mutation cannot operate on the same file more than once.")
        if self.operation_sequence != paths:
            raise ValueError("The operation sequence must exactly match the ordered operations.")
        if not set(paths).issubset(set(self.approved_paths)):
            raise ValueError("Every mutation path must have exact approved authority.")
        expected = calculate_file_mutation_hash(self.model_dump(mode="json"))
        if self.spec_hash != expected:
            raise ValueError("The file mutation specification hash is invalid.")
        return self


class FileMutationResult(StrictModel):
    schema_version: str = FILE_MUTATION_RESULT_VERSION
    file_mutation_id: str
    project_run_id: str
    execution_attempt_id: str
    status: str
    resulting_manifest_hash: str | None = Field(default=None, min_length=64, max_length=64)
    resulting_file_hashes: dict[str, str | None]
    exact_replay: bool = False
    recovered: bool = False
    failure_classification: str | None = None
    evidence_hash: str = Field(min_length=64, max_length=64)


def calculate_file_mutation_hash(value: dict[str, Any]) -> str:
    bound = dict(value)
    bound.pop("spec_hash", None)
    return content_hash(bound)


def build_file_mutation_spec(
    *,
    project_run_id: str,
    execution_attempt_id: str,
    mutation_kind: FileMutationKind | str,
    authority_id: str,
    repository_root: str,
    repository_root_fingerprint: str,
    workspace_id: str,
    plan_revision_id: str,
    scope_revision_id: str,
    manifest_hash: str,
    expected_result_manifest_hash: str,
    approved_paths: tuple[str, ...] | list[str],
    operations: tuple[FileOperationSpec, ...] | list[FileOperationSpec],
    created_at: datetime | None = None,
) -> FileMutationSpec:
    ordered = tuple(operations)
    timestamp = created_at or datetime.now(timezone.utc)
    payload = {
        "schema_version": FILE_MUTATION_SPEC_VERSION,
        "project_run_id": project_run_id,
        "execution_attempt_id": execution_attempt_id,
        "mutation_kind": FileMutationKind(mutation_kind),
        "authority_id": authority_id,
        "repository_root": repository_root,
        "repository_root_fingerprint": repository_root_fingerprint,
        "workspace_id": workspace_id,
        "plan_revision_id": plan_revision_id,
        "scope_revision_id": scope_revision_id,
        "manifest_hash": manifest_hash,
        "expected_result_manifest_hash": expected_result_manifest_hash,
        "approved_paths": tuple(approved_paths),
        "operations": ordered,
        "operation_sequence": tuple(operation.relative_path for operation in ordered),
        "created_at": timestamp,
    }
    binding_hash = content_hash({
        "project_run_id": project_run_id,
        "execution_attempt_id": execution_attempt_id,
        "mutation_kind": FileMutationKind(mutation_kind).value,
        "authority_id": authority_id,
        "manifest_hash": manifest_hash,
        "expected_result_manifest_hash": expected_result_manifest_hash,
        "operations": [operation.model_dump(mode="json") for operation in ordered],
    })
    payload["file_mutation_id"] = f"mutation-{binding_hash[:24]}"
    serializable = {
        key: value.model_dump(mode="json") if isinstance(value, StrictModel) else value
        for key, value in payload.items()
    }
    serializable["operations"] = [operation.model_dump(mode="json") for operation in ordered]
    serializable["mutation_kind"] = FileMutationKind(mutation_kind).value
    serializable["created_at"] = timestamp.isoformat().replace("+00:00", "Z")
    payload["spec_hash"] = calculate_file_mutation_hash(serializable)
    return FileMutationSpec.model_validate(payload)


__all__ = [
    "FileMutationKind",
    "FileMutationResult",
    "FileMutationSpec",
    "FileOperationKind",
    "FileOperationSpec",
    "build_file_mutation_spec",
    "calculate_file_mutation_hash",
]
