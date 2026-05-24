from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.jobs import JobQueue, LocalWorker
from backend.app.main import create_app


def test_worker_processes_one_allowlisted_job(tmp_path: Path):
    queue = JobQueue(tmp_path / "jobs.db")
    queue.initialize()
    queued = queue.enqueue("count_files", {"path": "."})
    worker = LocalWorker(
        queue,
        {"count_files": lambda payload: {"path": payload["path"], "count": 3}},
    )

    assert worker.run_once() is True
    completed = queue.get(queued.job_id)

    assert completed.status == "succeeded"
    assert completed.result == {"path": ".", "count": 3}
    assert completed.started_at is not None
    assert completed.finished_at is not None
    assert worker.run_once() is False


def test_worker_records_unsupported_and_failed_jobs(tmp_path: Path):
    queue = JobQueue(tmp_path / "jobs.db")
    queue.initialize()
    unsupported = queue.enqueue("unknown", {})
    broken = queue.enqueue("broken", {})

    def fail(_: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("processing failed")

    worker = LocalWorker(queue, {"broken": fail})
    worker.run_once()
    worker.run_once()

    assert queue.get(unsupported.job_id).error == "Unsupported job type: unknown"
    failed = queue.get(broken.job_id)
    assert failed.status == "failed"
    assert failed.error == "RuntimeError: processing failed"


def test_queue_cancels_queued_job_and_recovers_interrupted_running_job(tmp_path: Path):
    queue = JobQueue(tmp_path / "jobs.db")
    queue.initialize()
    cancelled = queue.enqueue("queued", {})
    interrupted = queue.enqueue("running", {})

    cancelled_result = queue.request_cancel(cancelled.job_id)
    claimed = queue.claim_next()

    assert cancelled_result.status == "cancelled"
    assert cancelled_result.cancel_requested_at is not None
    assert claimed is not None
    assert claimed.job_id == interrupted.job_id
    assert queue.recover_interrupted() == 1
    recovered = queue.get(interrupted.job_id)
    assert recovered.status == "failed"
    assert "Worker stopped" in recovered.error


def test_jobs_api_lists_gets_and_cancels_internal_jobs(tmp_path: Path):
    database_path = tmp_path / "api.db"
    with TestClient(create_app(database_path)) as client:
        queue = client.app.state.job_queue
        queued = queue.enqueue("future_job", {"internal": True})

        listing = client.get("/jobs?status=queued")
        detail = client.get(f"/jobs/{queued.job_id}")
        cancelled = client.post(f"/jobs/{queued.job_id}/cancel")
        missing = client.get("/jobs/missing")

    assert listing.status_code == 200
    assert [item["job_id"] for item in listing.json()["items"]] == [queued.job_id]
    assert detail.json()["status"] == "queued"
    assert "payload" not in detail.json()
    assert cancelled.json()["status"] == "cancelled"
    assert missing.status_code == 404
