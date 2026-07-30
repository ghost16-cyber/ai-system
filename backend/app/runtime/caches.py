from __future__ import annotations

from typing import Any
from uuid import uuid4

from backend.app.core.cache import LRUCache
from backend.app.project_retrieval.learned import loaded_model_cache_keys
from backend.app.runtime.contracts import CacheStatistics
from backend.app.runtime.persistence import RuntimePersistence


class VersionedReplayAwareCache:
    """Wraps the existing `core/cache.py::LRUCache` (its bounded, deterministic
    eviction engine is reused as-is, not reimplemented) and composes the real
    lookup key as `(version_tag, replay_key, logical_key)`. A version bump
    (e.g. a model/embedding-policy identity change) or a replay-scope change
    therefore deterministically misses instead of returning a stale entry --
    no separate invalidation pass is needed.
    """

    def __init__(self, cache_id: str, *, maxsize: int = 256) -> None:
        self.cache_id = cache_id
        self._store = LRUCache(maxsize=maxsize)
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, logical_key: Any, *, version_tag: str | None = None, replay_key: Any = None) -> Any:
        composite = (version_tag, replay_key, logical_key)
        # Membership is checked directly (not via LRUCache.get's return value,
        # which is None for both "missing" and "legitimately cached None") so
        # hit/miss accounting is exact either way.
        if composite in self._store._store:
            self.hits += 1
            return self._store.get(composite)
        self.misses += 1
        return None

    def set(self, logical_key: Any, value: Any, *, version_tag: str | None = None, replay_key: Any = None) -> None:
        composite = (version_tag, replay_key, logical_key)
        was_full = len(self._store._store) >= self._store.maxsize and composite not in self._store._store
        self._store.set(composite, value)
        if was_full:
            self.evictions += 1

    @property
    def size(self) -> int:
        return len(self._store._store)

    def clear(self) -> None:
        """Deterministic recovery action for a corrupted/unreliable cache
        state: drop every entry. Never touches the durable stores this cache
        merely accelerates access to -- clearing it only forces the next
        lookups to miss and be recomputed/refetched from the real source."""
        self._store._store.clear()

    def statistics(self) -> CacheStatistics:
        return CacheStatistics(
            cache_id=self.cache_id,
            hits=self.hits,
            misses=self.misses,
            evictions=self.evictions,
            size=self.size,
        )


class ProviderModelCacheView:
    """Read-only statistics view over the already-existing learned-provider
    model LRU (`project_retrieval/learned.py::_MODEL_CACHE`). This is not a
    second cache -- it never stores anything itself, only reports on the one
    that already governs provider load/unload."""

    cache_id = "provider"

    def statistics(self) -> CacheStatistics:
        keys = loaded_model_cache_keys()
        return CacheStatistics(cache_id=self.cache_id, hits=0, misses=0, evictions=0, size=len(keys))


class CacheRegistry:
    """Owns the six named runtime caches. `embedding`, `rerank`, `retrieval`,
    `corpus_metadata`, and `evaluation` are VersionedReplayAwareCache
    instances; `provider` is a read-only view over the pre-existing learned
    model cache (see ProviderModelCacheView).
    """

    def __init__(self, *, persistence: RuntimePersistence | None = None, maxsize: int = 256) -> None:
        self._persistence = persistence
        self.embedding = VersionedReplayAwareCache("embedding", maxsize=maxsize)
        self.rerank = VersionedReplayAwareCache("rerank", maxsize=maxsize)
        self.retrieval = VersionedReplayAwareCache("retrieval", maxsize=maxsize)
        self.corpus_metadata = VersionedReplayAwareCache("corpus_metadata", maxsize=maxsize)
        self.evaluation = VersionedReplayAwareCache("evaluation", maxsize=maxsize)
        self.provider = ProviderModelCacheView()

    def all_statistics(self) -> tuple[CacheStatistics, ...]:
        return (
            self.embedding.statistics(),
            self.rerank.statistics(),
            self.retrieval.statistics(),
            self.corpus_metadata.statistics(),
            self.evaluation.statistics(),
            self.provider.statistics(),
        )

    def clear_all(self) -> None:
        for cache in (self.embedding, self.rerank, self.retrieval, self.corpus_metadata, self.evaluation):
            cache.clear()

    def record_snapshot(self) -> None:
        if self._persistence is None:
            return
        for stat in self.all_statistics():
            self._persistence.record_cache_statistics(
                stat_id=str(uuid4()),
                cache_id=stat.cache_id,
                hits=stat.hits,
                misses=stat.misses,
                evictions=stat.evictions,
                size=stat.size,
                version_tag=stat.version_tag,
                detail=stat.model_dump(mode="json"),
            )
