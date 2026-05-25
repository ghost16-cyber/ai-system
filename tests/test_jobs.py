import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.jobs import JobQueue, LocalWorker, build_job_handlers
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


def test_queue_marks_running_job_cancel_requested_without_killing_worker(
    tmp_path: Path,
):
    queue = JobQueue(tmp_path / "jobs.db")
    queue.initialize()
    queued = queue.enqueue("long_running", {})

    claimed = queue.claim_next()
    cancelled = queue.request_cancel(queued.job_id)

    assert claimed is not None
    assert claimed.job_id == queued.job_id
    assert cancelled.status == "running"
    assert cancelled.cancel_requested_at is not None
    assert cancelled.finished_at is None


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


def test_analyze_project_runs_as_worker_job_without_storing_source(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "style.py").write_text(
        "if value == None:\n    print(value)\n",
        encoding="utf-8",
    )
    (workspace / "security.py").write_text(
        "value = eval(user_input)\n",
        encoding="utf-8",
    )
    ignored = workspace / ".venv"
    ignored.mkdir()
    (ignored / "ignored.py").write_text("eval(hidden)\n", encoding="utf-8")

    with TestClient(
        create_app(tmp_path / "project.db", workspace_root=workspace)
    ) as client:
        response = client.post("/analyze-project", json={"path": "."})

        assert response.status_code == 202
        queued = response.json()
        assert queued["status"] == "queued"
        assert queued["status_url"] == f"/jobs/{queued['job_id']}"

        worker = LocalWorker(
            client.app.state.job_queue,
            handlers=build_job_handlers(workspace),
        )
        assert worker.run_once() is True
        completed = client.get(queued["status_url"]).json()

    assert completed["status"] == "succeeded"
    result = completed["result"]
    assert result["python_files_analyzed"] == 2
    assert result["total_findings"] == 2
    assert result["findings_by_rule"] == {
        "bad_none_comparison": 1,
        "dangerous_eval": 1,
    }
    assert result["source_stored"] is False
    assert "ignored.py" not in json.dumps(result)
    assert "eval(user_input)" not in json.dumps(result)
    assert "value == None" not in json.dumps(result)


def test_analyze_project_rejects_escape_and_worker_revalidates_payload(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with TestClient(
        create_app(tmp_path / "project.db", workspace_root=workspace)
    ) as client:
        response = client.post("/analyze-project", json={"path": "../outside"})
        queued = client.app.state.job_queue.enqueue(
            "analyze_project", {"path": "../outside"}
        )
        worker = LocalWorker(
            client.app.state.job_queue,
            handlers=build_job_handlers(workspace),
        )
        worker.run_once()
        failed = client.get(f"/jobs/{queued.job_id}").json()

    assert response.status_code == 400
    assert failed["status"] == "failed"
    assert "workspace root" in failed["error"]


def test_analyze_project_empty_directory_succeeds_with_empty_summary(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with TestClient(
        create_app(tmp_path / "project.db", workspace_root=workspace)
    ) as client:
        queued = client.post("/analyze-project", json={"path": "."}).json()
        worker = LocalWorker(
            client.app.state.job_queue,
            handlers=build_job_handlers(workspace),
        )
        assert worker.run_once() is True
        completed = client.get(queued["status_url"]).json()

    assert completed["status"] == "succeeded"
    assert completed["result"] == {
        "path": ".",
        "files_discovered": 0,
        "python_files_analyzed": 0,
        "total_findings": 0,
        "findings_by_rule": {},
        "findings_by_severity": {},
        "files": [],
        "read_errors": [],
        "source_stored": False,
    }


def test_analyze_project_records_invalid_utf8_read_error_without_failing_job(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "bad_encoding.py").write_bytes(b"\xff\xfe\x00\x00")

    with TestClient(
        create_app(tmp_path / "project.db", workspace_root=workspace)
    ) as client:
        queued = client.post("/analyze-project", json={"path": "."}).json()
        worker = LocalWorker(
            client.app.state.job_queue,
            handlers=build_job_handlers(workspace),
        )
        assert worker.run_once() is True
        completed = client.get(queued["status_url"]).json()

    assert completed["status"] == "succeeded"
    result = completed["result"]
    assert result["python_files_analyzed"] == 0
    assert result["total_findings"] == 0
    assert result["read_errors"] == [
        {
            "path": "bad_encoding.py",
            "error": "Python file must be UTF-8 encoded.",
        }
    ]


def test_analyze_project_result_excludes_validated_replacement_text(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for index in range(25):
        (workspace / f"module_{index}.py").write_text(
            "if flag == True:\n    print(flag)\n",
            encoding="utf-8",
        )

    with TestClient(
        create_app(tmp_path / "project.db", workspace_root=workspace)
    ) as client:
        queued = client.post("/analyze-project", json={"path": "."}).json()
        worker = LocalWorker(
            client.app.state.job_queue,
            handlers=build_job_handlers(workspace),
        )
        assert worker.run_once() is True
        completed = client.get(queued["status_url"]).json()

    assert completed["status"] == "succeeded"
    result = completed["result"]
    result_json = json.dumps(result)

    assert result["python_files_analyzed"] == 25
    assert result["total_findings"] == 25
    assert result["findings_by_rule"] == {"redundant_boolean_comparison": 25}
    assert len(result["files"]) == 25
    assert "suggested_code" not in result_json
    assert "replacement" not in result_json
    assert "if flag:" not in result_json
    assert "if flag == True" not in result_json
