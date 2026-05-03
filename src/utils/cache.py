# src/utils/cache.py
"""
Simple LRU cache for in‑memory objects.

Used to cache embeddings and LLM responses during a single process run.
"""

from collections import OrderedDict
from typing import Any, Optional


class LRUCache:
    """
    Fixed‑size Least‑Recently‑Used cache.

    Parameters
    ----------
    maxsize : int
        Maximum number of entries to keep.
    """

    def __init__(self, maxsize: int = 128):
        self.maxsize = maxsize
        self._store: OrderedDict[Any, Any] = OrderedDict()

    def get(self, key: Any) -> Optional[Any]:
        """Return the cached value or ``None`` if the key is missing."""
        if key in self._store:
            self._store.move_to_end(key)
            return self._store[key]
        return None

    def set(self, key: Any, value: Any) -> None:
        """Insert ``value`` under ``key`` and enforce ``maxsize``."""
        self._store[key] = value
        self._store.move_to_end(key)
        if len(self._store) > self.maxsize:
            self._store.popitem(last=False)