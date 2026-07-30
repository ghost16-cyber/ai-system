from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_MODEL_AUDIT_PATH = Path("data/specialists/model_audit.jsonl")


def append_model_audit_event(
    *,
    action: str,
    model_id: str | None = None,
    specialist: str | None = None,
    details: dict[str, Any] | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    audit_path = Path(path or DEFAULT_MODEL_AUDIT_PATH)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "action": action,
        "model_id": model_id,
        "specialist": specialist,
        "details": details or {},
    }
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return {"logged": True, "path": str(audit_path), "event": event}


def load_model_audit_events(
    model_id: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    audit_path = Path(path or DEFAULT_MODEL_AUDIT_PATH)
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not audit_path.exists():
        return {"path": str(audit_path), "events": events, "errors": errors, "missing": True}

    with audit_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as error:
                errors.append({"line": line_number, "error": error.msg})
                continue
            if model_id is not None and event.get("model_id") != model_id:
                continue
            events.append(event)

    return {"path": str(audit_path), "events": events, "errors": errors, "missing": False}
