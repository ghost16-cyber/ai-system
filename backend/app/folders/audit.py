from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import re


def audit_event(
    repository,
    *,
    conversation_id: str,
    folder_access_id: str,
    operation: str,
    status: str,
    patch_id: str | None = None,
    job_id: str | None = None,
    command_plan_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    safe = _sanitize(metadata or {})
    repository.store_project_audit_event(
        {
            "event_id": uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "conversation_id": conversation_id,
            "folder_access_id": folder_access_id,
            "patch_id": patch_id,
            "job_id": job_id,
            "command_plan_id": command_plan_id,
            "operation": operation,
            "status": status,
            "metadata": safe,
        }
    )


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        blocked = {
            "content", "text", "excerpt", "stdout", "stderr", "approved_root",
            "workspace_path", "environment", "env", "secret", "token", "password",
            "credential", "api_key", "private_key",
        }
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in blocked
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:100]]
    if isinstance(value, str):
        bounded = value[:1000]
        if re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\)", bounded):
            return "[absolute path omitted]"
        if re.search(r"\bsk-[A-Za-z0-9_-]{12,}\b", bounded):
            return "[secret omitted]"
        return bounded
    return value
