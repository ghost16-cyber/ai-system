from __future__ import annotations

import time
from collections.abc import Callable

from .queue import JobQueue


JobHandler = Callable[[dict[str, object]], dict[str, object]]


class LocalWorker:
    """Run one allowlisted local job at a time from the SQLite queue."""

    def __init__(self, queue: JobQueue, handlers: dict[str, JobHandler]) -> None:
        self.queue = queue
        self.handlers = handlers

    def recover_interrupted(self) -> int:
        return self.queue.recover_interrupted()

    def run_once(self) -> bool:
        job = self.queue.claim_next()
        if job is None:
            return False

        handler = self.handlers.get(job.job_type)
        if handler is None:
            self.queue.fail(job.job_id, f"Unsupported job type: {job.job_type}")
            return True

        try:
            result = handler(job.payload)
        except Exception as error:
            self.queue.fail(job.job_id, f"{type(error).__name__}: {error}")
        else:
            self.queue.succeed(job.job_id, result)
        return True

    def run_forever(self, *, poll_seconds: float = 1.0) -> None:
        self.recover_interrupted()
        while True:
            if not self.run_once():
                time.sleep(poll_seconds)
