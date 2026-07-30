from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import TaskState


REDACTED_KEYS = {"content", "output", "old", "new"}


class TraceStore(Protocol):
    def append(self, state: TaskState) -> None:
        """Persist a completed task trace."""


class JsonlTraceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, state: TaskState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(to_public_trace(state), sort_keys=True) + "\n")


def to_public_trace(state: TaskState) -> dict:
    raw = state.model_dump(mode="json")
    return _redact(raw)


def _redact(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key in REDACTED_KEYS and isinstance(item, str):
                redacted[key] = {
                    "redacted": True,
                    "length": len(item),
                }
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
