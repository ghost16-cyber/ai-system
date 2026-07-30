from __future__ import annotations

from enum import StrEnum
from typing import Any


ERROR_SCHEMA_VERSION = "astra.project-control.error.v1"


class ProjectControlErrorCode(StrEnum):
    PROJECT_NOT_FOUND = "project_not_found"
    CONVERSATION_MISMATCH = "conversation_mismatch"
    WORKSPACE_MISMATCH = "workspace_mismatch"
    REPOSITORY_ROOT_MISMATCH = "repository_root_mismatch"
    ACTOR_MISMATCH = "actor_mismatch"
    ILLEGAL_TRANSITION = "illegal_transition"
    STALE_STATE_VERSION = "stale_state_version"
    STALE_APPROVAL = "stale_approval"
    MISSING_APPROVAL = "missing_approval"
    PLAN_REVISION_MISMATCH = "plan_revision_mismatch"
    SCOPE_REVISION_MISMATCH = "scope_revision_mismatch"
    MANIFEST_MISMATCH = "manifest_mismatch"
    INCOMPLETE_MANIFEST = "incomplete_manifest"
    STALE_VERIFICATION = "stale_verification"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    MIGRATED_REAPPROVAL_REQUIRED = "migrated_reapproval_required"
    TERMINAL_PROJECT = "terminal_project"
    PERSISTENCE_CONFLICT = "persistence_conflict"
    CORRUPTED_STORED_STATE = "corrupted_stored_state"
    UNSUPPORTED_STORED_STATE = "unsupported_stored_state"
    INVALID_COMMAND = "invalid_command"
    HISTORICAL_RECORD_READ_ONLY = "historical_record_read_only"
    NON_CURRENT_ARTIFACT = "non_current_artifact"


class ProjectControlError(RuntimeError):
    """Versioned, bounded failure safe for API serialization."""

    def __init__(
        self,
        code: ProjectControlErrorCode | str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = ProjectControlErrorCode(code)
        self.message = " ".join(str(message).split())[:600]
        self.metadata = {
            str(key)[:80]: value
            for key, value in (metadata or {}).items()
            if isinstance(value, (str, int, float, bool, type(None)))
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ERROR_SCHEMA_VERSION,
            "code": self.code.value,
            "message": self.message,
            "metadata": self.metadata,
        }
