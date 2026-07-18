from __future__ import annotations

from enum import StrEnum
from typing import Any


WORKER_ERROR_VERSION = "astra.project-workers.error.v1"


class ProjectWorkerErrorCode(StrEnum):
    REQUEST_NOT_FOUND = "request_not_found"
    INVALID_REQUEST = "invalid_request"
    INVALID_BINDING = "invalid_binding"
    STALE_PROJECT_STATE = "stale_project_state"
    ATTEMPT_ALREADY_BOUND = "attempt_already_bound"
    ATTEMPT_NOT_ACTIVE = "attempt_not_active"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    NOT_CLAIMABLE = "not_claimable"
    LEASE_MISMATCH = "lease_mismatch"
    LEASE_EXPIRED = "lease_expired"
    TERMINAL_REQUEST = "terminal_request"
    CANCEL_NOT_REQUESTED = "cancel_not_requested"
    RESULT_TOO_LARGE = "result_too_large"
    PERSISTENCE_CONFLICT = "persistence_conflict"
    CORRUPTED_STORED_STATE = "corrupted_stored_state"
    UNSUPPORTED_STORED_STATE = "unsupported_stored_state"


class ProjectWorkerError(RuntimeError):
    def __init__(
        self,
        code: ProjectWorkerErrorCode | str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = ProjectWorkerErrorCode(code)
        self.message = " ".join(str(message).split())[:600]
        self.metadata = {
            str(key)[:80]: value
            for key, value in (metadata or {}).items()
            if isinstance(value, (str, int, float, bool, type(None)))
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORKER_ERROR_VERSION,
            "code": self.code.value,
            "message": self.message,
            "metadata": self.metadata,
        }


__all__ = ["ProjectWorkerError", "ProjectWorkerErrorCode"]
