from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.database.migrations import apply_schema_migrations
from backend.app.runtime.background.handlers import DictHandlerRegistry
from backend.app.runtime.background.queue import (
    MAX_QUEUE_DEPTH,
    RuntimeJobQueue,
    RuntimeJobQueueError,
)
from backend.app.runtime.background.worker import RuntimeWorker
from backend.app.runtime.contracts import BackgroundJobStatus


def _queue(tmp_path: Path, name: str = "jobs.db") -> RuntimeJobQueue:
    database = tmp_path / name
    apply_schema_migrations(database)
    return RuntimeJobQueue(database)


def test_enqueue_dedupes_on_idempotency_key(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    first = queue.enqueue(job_type="corpus_reindex", target_id="p1", idempotency_key="k1", payload={"a": 1})
    again = queue.enqueue(job_type="corpus_reindex", target_id="p1", idempotency_key="k1", payload={"a": 1})
    assert again.job_id == first.job_id

    with pytest.raises(RuntimeJobQueueError, match="idempotency_payload_mismatch"):
        queue.enqueue(job_type="corpus_reindex", target_id="p1", idempotency_key="k1", payload={"a": 2})


def test_fifo_ordering_by_priority_then_created_at(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    low = queue.enqueue(job_type="cleanup", target_id="t-low", idempotency_key="low", payload={}, priority=200)
    high = queue.enqueue(job_type="cleanup", target_id="t-high", idempotency_key="high", payload={}, priority=1)

    claimed_first = queue.claim_next(worker_id="w1")
    assert claimed_first.job_id == high.job_id
    claimed_second = queue.claim_next(worker_id="w1")
    assert claimed_second.job_id == low.job_id


def test_no_parallel_mutation_of_same_target(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    first = queue.enqueue(job_type="corpus_reindex", target_id="project-1", idempotency_key="k1", payload={})
    second = queue.enqueue(job_type="corpus_reindex", target_id="project-1", idempotency_key="k2", payload={})
    other = queue.enqueue(job_type="corpus_reindex", target_id="project-2", idempotency_key="k3", payload={})

    claimed_a = queue.claim_next(worker_id="w1")
    assert claimed_a.job_id == first.job_id
    claimed_b = queue.claim_next(worker_id="w1")
    assert claimed_b.job_id == other.job_id
    # second is for project-1, which already has a claimed job -- must not be claimable yet.
    assert queue.claim_next(worker_id="w1") is None

    queue.complete_job(first.job_id, worker_id="w1", succeeded=True)
    claimed_after_completion = queue.claim_next(worker_id="w1")
    assert claimed_after_completion.job_id == second.job_id


def test_bounded_queue_rejects_beyond_max_depth(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    for index in range(MAX_QUEUE_DEPTH):
        queue.enqueue(job_type="cleanup", target_id=f"t-{index}", idempotency_key=f"bound-{index}", payload={})
    with pytest.raises(RuntimeJobQueueError, match="queue_depth_exceeded"):
        queue.enqueue(job_type="cleanup", target_id="t-over", idempotency_key="bound-over", payload={})


def test_complete_job_requires_matching_lease_owner(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    job = queue.enqueue(job_type="cleanup", target_id="t1", idempotency_key="k1", payload={})
    queue.claim_next(worker_id="w1")
    with pytest.raises(RuntimeJobQueueError, match="lease_mismatch"):
        queue.complete_job(job.job_id, worker_id="w2", succeeded=True)


def test_reload_recovery_requeues_jobs_with_expired_leases(tmp_path: Path) -> None:
    """Category: reload recovery. Simulate a worker crash mid-job by forcing
    a lease into the past directly, then confirm the queue (as it would on
    process restart, via claim_next -> recover_expired_jobs) requeues it."""
    database = tmp_path / "reload.db"
    apply_schema_migrations(database)
    queue = RuntimeJobQueue(database)
    job = queue.enqueue(job_type="corpus_reindex", target_id="project-1", idempotency_key="k1", payload={})
    queue.claim_next(worker_id="worker-that-crashed")

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE runtime_background_jobs SET lease_expires_at = ? WHERE job_id = ?",
            (expired, job.job_id),
        )
        connection.commit()

    # A fresh queue instance (simulating restart) recovers it via claim_next.
    restarted_queue = RuntimeJobQueue(database)
    recovered_count = restarted_queue.recover_expired_jobs()
    assert recovered_count == 1
    reclaimed = restarted_queue.claim_next(worker_id="new-worker")
    assert reclaimed.job_id == job.job_id
    assert reclaimed.status == BackgroundJobStatus.CLAIMED


def test_worker_run_once_dispatches_to_registered_handler(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    calls: list[tuple[str, dict]] = []

    def _handler(target_id: str, payload: dict) -> bool:
        calls.append((target_id, payload))
        return True

    handlers = DictHandlerRegistry()
    handlers.register("corpus_reindex", _handler)
    worker = RuntimeWorker(queue, handlers, worker_id="test-worker")

    queue.enqueue(job_type="corpus_reindex", target_id="project-1", idempotency_key="k1", payload={"files": 3})
    processed = worker.run_once()

    assert processed is True
    assert calls[0][0] == "project-1"
    summary = queue.status_summary()
    assert summary["queued"] == 0


def test_worker_run_once_fails_closed_for_unregistered_job_type(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    handlers = DictHandlerRegistry()
    worker = RuntimeWorker(queue, handlers, worker_id="test-worker")

    queue.enqueue(job_type="unknown_type", target_id="t1", idempotency_key="k1", payload={})
    processed = worker.run_once()

    assert processed is True
    summary = queue.status_summary()
    assert summary["recent"][0].status == BackgroundJobStatus.FAILED


def test_worker_transitions_claimed_to_running_before_invoking_the_handler(
    tmp_path: Path,
) -> None:
    queue = _queue(tmp_path)
    observed_status: list[BackgroundJobStatus] = []

    def _handler(target_id: str, payload: dict) -> bool:
        observed_status.append(queue.status_summary()["recent"][0].status)
        return True

    handlers = DictHandlerRegistry()
    handlers.register("corpus_reindex", _handler)
    worker = RuntimeWorker(queue, handlers, worker_id="test-worker")
    queue.enqueue(job_type="corpus_reindex", target_id="project-1", idempotency_key="k1", payload={})

    worker.run_once()

    assert observed_status == [BackgroundJobStatus.RUNNING]


def test_mark_running_requires_the_claiming_worker(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    job = queue.enqueue(job_type="cleanup", target_id="t1", idempotency_key="k1", payload={})
    queue.claim_next(worker_id="w1")

    with pytest.raises(RuntimeJobQueueError, match="lease_mismatch"):
        queue.mark_running(job.job_id, worker_id="w2")

    running = queue.mark_running(job.job_id, worker_id="w1")
    assert running.status == BackgroundJobStatus.RUNNING


def test_completed_job_persists_bounded_terminal_result(tmp_path: Path) -> None:
    queue = _queue(tmp_path)
    ok_job = queue.enqueue(job_type="cleanup", target_id="t-ok", idempotency_key="k-ok", payload={})
    queue.claim_next(worker_id="w1")
    completed = queue.complete_job(ok_job.job_id, worker_id="w1", succeeded=True)
    assert completed.result is not None
    assert completed.result.succeeded is True
    assert completed.result.error is None

    fail_job = queue.enqueue(job_type="cleanup", target_id="t-fail", idempotency_key="k-fail", payload={})
    queue.claim_next(worker_id="w1")
    failed = queue.complete_job(fail_job.job_id, worker_id="w1", succeeded=False, error="boom")
    assert failed.result is not None
    assert failed.result.succeeded is False
    assert failed.result.error == "boom"


def test_worker_captures_handler_exception_as_a_terminal_error(tmp_path: Path) -> None:
    queue = _queue(tmp_path)

    def _broken_handler(target_id: str, payload: dict) -> bool:
        raise RuntimeError("handler exploded")

    handlers = DictHandlerRegistry()
    handlers.register("cleanup", _broken_handler)
    worker = RuntimeWorker(queue, handlers, worker_id="test-worker")
    queue.enqueue(job_type="cleanup", target_id="t1", idempotency_key="k1", payload={})

    worker.run_once()

    recent = queue.status_summary()["recent"][0]
    assert recent.status == BackgroundJobStatus.FAILED
    assert recent.result is not None
    assert recent.result.succeeded is False
    assert "handler exploded" in recent.result.error


def test_worker_thread_start_and_stop_is_reload_safe(tmp_path: Path) -> None:
    """The thread itself is disposable -- the durable queue (not thread
    state) is what survives a restart. Starting/stopping must not lose or
    duplicate jobs."""
    queue = _queue(tmp_path)
    processed: list[str] = []

    def _handler(target_id: str, _payload: dict) -> bool:
        processed.append(target_id)
        return True

    handlers = DictHandlerRegistry()
    handlers.register("cleanup", _handler)
    worker = RuntimeWorker(queue, handlers, worker_id="thread-worker", poll_interval_seconds=0.05)

    queue.enqueue(job_type="cleanup", target_id="t1", idempotency_key="k1", payload={})
    worker.start()
    import time
    for _ in range(100):
        if processed:
            break
        time.sleep(0.05)
    worker.stop()

    assert processed == ["t1"]
    summary = queue.status_summary()
    assert summary["queued"] == 0
    assert summary["claimed"] == 0
