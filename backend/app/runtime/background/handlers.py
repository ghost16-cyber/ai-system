from __future__ import annotations

from typing import Callable, Protocol

JobHandler = Callable[[str, dict], bool]
"""A handler receives (target_id, payload) and returns True on success."""


class HandlerRegistry(Protocol):
    def get(self, job_type: str) -> JobHandler | None: ...


class DictHandlerRegistry:
    """Maps background job types to handler callables. Handlers never
    reimplement chunking/hashing/eviction logic themselves -- each one is a
    thin call into an existing subsystem's own method (see corpus.py's
    reindex handler and caches.py's eviction handler for real examples)."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    def get(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)
