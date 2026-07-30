from __future__ import annotations

import ast
from pathlib import Path

from backend.app.database.migrations import apply_schema_migrations
from backend.app.runtime.persistence import RuntimePersistence
from backend.app.runtime.telemetry import TelemetryRegistry


def test_counters_and_gauges_are_tracked_locally() -> None:
    registry = TelemetryRegistry()
    registry.increment("cache_hits")
    registry.increment("cache_hits")
    registry.increment("cache_misses", 3)
    registry.set_gauge("queue_depth", 4.0)

    snapshot = registry.snapshot()
    assert snapshot.counters["cache_hits"] == 2
    assert snapshot.counters["cache_misses"] == 3
    assert snapshot.gauges["queue_depth"] == 4.0


def test_flush_persists_a_local_snapshot_and_nothing_leaves_the_process(tmp_path: Path) -> None:
    """Category: telemetry is local-only. Asserts a snapshot lands in the
    local database, and (structurally) that the telemetry module makes no
    outbound network calls anywhere in its source."""
    database = tmp_path / "telemetry.db"
    apply_schema_migrations(database)
    persistence = RuntimePersistence(database)
    registry = TelemetryRegistry(persistence)
    registry.increment("provider_load", 1)
    registry.flush()

    row = persistence.latest_telemetry_snapshot()
    assert row is not None
    assert "provider_load" in row["snapshot_json"]

    source_path = Path("backend/app/runtime/telemetry.py")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    network_symbols = {"socket", "urlopen", "requests", "httpx", "aiohttp"}
    used_names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not (network_symbols & used_names)


def test_flush_without_persistence_is_a_safe_no_op() -> None:
    registry = TelemetryRegistry(persistence=None)
    registry.increment("x")
    registry.flush()  # must not raise
