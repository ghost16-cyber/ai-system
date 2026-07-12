from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def audit_event(
    repository,
    *,
    conversation_id: str,
    folder_access_id: str,
    operation: str,
    status: str,
    patch_id: str | None = None,
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
            "operation": operation,
            "status": status,
            "metadata": safe,
        }
    )


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items() if str(key).lower() not in {"content", "text", "excerpt", "stdout", "stderr", "approved_root"}}
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:1000]
    return value
