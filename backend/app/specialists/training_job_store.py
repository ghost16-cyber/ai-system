from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_TRAINING_JOB_STORE_PATH = Path("data/specialists/training_jobs.json")
TRAINING_JOB_STATUSES = {"queued", "running", "completed", "failed", "rejected"}


def create_training_job(
    *,
    dataset_id: str | None,
    specialist_name: str,
    store_path: str | Path | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    record = {
        "training_job_id": f"specialist-train-{uuid4().hex[:12]}",
        "dataset_id": dataset_id,
        "specialist_name": specialist_name,
        "model_id": None,
        "started_at": None,
        "finished_at": None,
        "metrics": {},
        "status": "queued",
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }
    jobs = _load_jobs(store_path)
    jobs[record["training_job_id"]] = record
    _save_jobs(jobs, store_path)
    return record


def update_training_job(
    job_id: str,
    *,
    status: str | None = None,
    model_id: str | None = None,
    metrics: dict[str, Any] | None = None,
    error_message: str | None = None,
    store_path: str | Path | None = None,
) -> dict[str, Any] | None:
    jobs = _load_jobs(store_path)
    record = jobs.get(job_id)
    if record is None:
        return None
    now = _utc_now()
    if status:
        if status not in TRAINING_JOB_STATUSES:
            raise ValueError(f"Unknown training job status: {status}")
        record["status"] = status
        if status == "running" and record["started_at"] is None:
            record["started_at"] = now
        if status in {"completed", "failed", "rejected"}:
            record["finished_at"] = now
    if model_id is not None:
        record["model_id"] = model_id
    if metrics is not None:
        record["metrics"] = metrics
    if error_message is not None:
        record["error_message"] = error_message
    record["updated_at"] = now
    jobs[job_id] = record
    _save_jobs(jobs, store_path)
    return record


def list_training_jobs(store_path: str | Path | None = None) -> dict[str, Any]:
    jobs = _load_jobs(store_path)
    return {
        "path": str(Path(store_path or DEFAULT_TRAINING_JOB_STORE_PATH)),
        "jobs": list(sorted(jobs.values(), key=lambda item: item["created_at"])),
    }


def get_training_job(
    job_id: str,
    store_path: str | Path | None = None,
) -> dict[str, Any] | None:
    return _load_jobs(store_path).get(job_id)


def _load_jobs(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    store_path = Path(path or DEFAULT_TRAINING_JOB_STORE_PATH)
    if not store_path.exists():
        return {}
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(job_id): record
        for job_id, record in raw.items()
        if isinstance(record, dict)
    }


def _save_jobs(
    jobs: dict[str, dict[str, Any]],
    path: str | Path | None = None,
) -> None:
    store_path = Path(path or DEFAULT_TRAINING_JOB_STORE_PATH)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(jobs, indent=2, sort_keys=True), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
