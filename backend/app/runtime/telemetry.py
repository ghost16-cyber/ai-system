from __future__ import annotations

import threading
from datetime import datetime, timezone
from uuid import uuid4

from backend.app.runtime.contracts import RuntimeTelemetrySnapshot
from backend.app.runtime.persistence import RuntimePersistence


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TelemetryRegistry:
    """Local-only counters/gauges. Never uploaded, never sent to any
    external endpoint -- `flush()` only ever writes to the local
    `runtime_telemetry_snapshots` table via RuntimePersistence."""

    def __init__(self, persistence: RuntimePersistence | None = None) -> None:
        self._persistence = persistence
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def snapshot(self) -> RuntimeTelemetrySnapshot:
        with self._lock:
            return RuntimeTelemetrySnapshot(
                counters=dict(self._counters),
                gauges=dict(self._gauges),
                generated_at=_now(),
            )

    def flush(self) -> None:
        if self._persistence is None:
            return
        snapshot = self.snapshot()
        self._persistence.record_telemetry_snapshot(
            snapshot_id=str(uuid4()),
            detail=snapshot.model_dump(mode="json"),
        )
