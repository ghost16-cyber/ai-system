from __future__ import annotations

from pathlib import Path

from backend.app.database.migrations import apply_schema_migrations
from backend.app.runtime.caches import CacheRegistry, VersionedReplayAwareCache
from backend.app.runtime.persistence import RuntimePersistence


def test_cache_hit_and_miss_are_tracked() -> None:
    cache = VersionedReplayAwareCache("embedding", maxsize=4)
    assert cache.get("key-1") is None
    assert cache.misses == 1
    cache.set("key-1", "value-1")
    assert cache.get("key-1") == "value-1"
    assert cache.hits == 1


def test_lru_eviction_is_deterministic_and_bounded() -> None:
    cache = VersionedReplayAwareCache("retrieval", maxsize=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # evicts "a" (least recently used)
    assert cache.size == 2
    assert cache.evictions == 1
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_version_bump_causes_deterministic_miss_not_stale_value() -> None:
    """Category: version-aware. A model/policy identity change must miss
    rather than silently returning a value computed under the old version."""
    cache = VersionedReplayAwareCache("embedding", maxsize=8)
    cache.set("chunk-1", [0.1, 0.2], version_tag="model-v1")
    assert cache.get("chunk-1", version_tag="model-v1") == [0.1, 0.2]
    assert cache.get("chunk-1", version_tag="model-v2") is None


def test_replay_scope_change_causes_deterministic_miss() -> None:
    """Category: replay-aware. A different replay/idempotency scope must
    never be served a value cached under a different scope."""
    cache = VersionedReplayAwareCache("retrieval", maxsize=8)
    cache.set("query-hash-1", "result-a", replay_key="request-1")
    assert cache.get("query-hash-1", replay_key="request-1") == "result-a"
    assert cache.get("query-hash-1", replay_key="request-2") is None


def test_provider_cache_delegates_to_existing_learned_model_cache(monkeypatch) -> None:
    """The provider cache must not be a second independent LRU -- it only
    reports on project_retrieval.learned's existing _MODEL_CACHE."""
    import backend.app.runtime.caches as caches_module

    monkeypatch.setattr(
        caches_module, "loaded_model_cache_keys",
        lambda: (("sentence_transformer", "bge-small", "rev1", "cpu"),),
    )
    registry = CacheRegistry()
    stats = registry.provider.statistics()
    assert stats.cache_id == "provider"
    assert stats.size == 1


def test_cache_registry_all_statistics_covers_six_named_caches() -> None:
    registry = CacheRegistry()
    stats = registry.all_statistics()
    cache_ids = {item.cache_id for item in stats}
    assert cache_ids == {
        "embedding", "rerank", "retrieval", "corpus_metadata", "evaluation", "provider",
    }


def test_record_snapshot_persists_statistics_for_every_cache(tmp_path: Path) -> None:
    database = tmp_path / "caches.db"
    apply_schema_migrations(database)
    persistence = RuntimePersistence(database)
    registry = CacheRegistry(persistence=persistence)
    registry.embedding.set("k", "v")
    registry.embedding.get("k")

    registry.record_snapshot()
    rows = persistence.latest_cache_statistics()
    assert len(rows) == 6
    embedding_row = next(row for row in rows if row["cache_id"] == "embedding")
    assert embedding_row["hits"] == 1
    assert embedding_row["size"] == 1
