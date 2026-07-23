from __future__ import annotations

import threading
from uuid import uuid4

from backend.app.runtime.background.handlers import DictHandlerRegistry
from backend.app.runtime.background.queue import RuntimeJobQueue

DEFAULT_LEASE_SECONDS = 60
DEFAULT_POLL_INTERVAL_SECONDS = 2.0


class RuntimeWorker:
    """Single in-process worker loop, matching the existing
    `threading.Thread(daemon=True)` pattern already used elsewhere in this
    codebase (`main.py:6604`, `project_workers/isolation.py:271`) rather than
    asyncio or a new separate process. Exposes a synchronous `run_once()` so
    tests can drive claim/dispatch/complete deterministically without
    starting the background thread at all.
    """

    def __init__(
        self,
        queue: RuntimeJobQueue,
        handlers: DictHandlerRegistry,
        *,
        worker_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._queue = queue
        self._handlers = handlers
        self.worker_id = worker_id or f"runtime-worker-{uuid4().hex[:8]}"
        self._lease_seconds = lease_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> bool:
        """Claim and process exactly one job, if any is available. Returns
        True if a job was processed (regardless of success/failure)."""
        job = self._queue.claim_next(worker_id=self.worker_id, lease_seconds=self._lease_seconds)
        if job is None:
            return False
        handler = self._handlers.get(job.job_type)
        if handler is None:
            self._queue.complete_job(job.job_id, worker_id=self.worker_id, succeeded=False)
            return True
        try:
            succeeded = handler(job.target_id, job.payload)
        except Exception:
            succeeded = False
        self._queue.complete_job(job.job_id, worker_id=self.worker_id, succeeded=succeeded)
        return True

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            processed = self.run_once()
            if not processed:
                self._stop_event.wait(self._poll_interval_seconds)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=self.worker_id)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
