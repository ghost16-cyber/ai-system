from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.project_control.contracts import ExecutionAttemptType
from backend.app.project_workers import ProjectWorkerQueue


def test_runtime_heartbeat_is_durable_and_expires(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    queue = ProjectWorkerQueue(database)
    queue.initialize()
    now = datetime.now(timezone.utc)

    instance = queue.record_runtime_heartbeat(
        "worker-one",
        execution_backend="docker",
        supported_attempt_types=(
            ExecutionAttemptType.PATCH,
            ExecutionAttemptType.COMMAND,
        ),
        supported_toolchains=("python", "node"),
        now=now,
    )

    assert instance.status == "available"
    assert queue.list_active_runtime_instances(now=now) == [instance]
    assert queue.list_active_runtime_instances(now=now + timedelta(seconds=16)) == []
    queue.mark_runtime_stopped("worker-one", now=now + timedelta(seconds=1))
    assert queue.list_active_runtime_instances(now=now + timedelta(seconds=1)) == []


def test_runtime_capability_endpoint_reports_separate_worker_availability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ASTRA_PROJECT_EXECUTION_BACKEND", "disabled")
    app = create_app(tmp_path / "app.db", workspace_root=tmp_path)
    with TestClient(app) as client:
        unavailable = client.get("/chat/projects/runtime-capabilities")
        assert unavailable.status_code == 200
        assert unavailable.json()["worker_available"] is False
        assert unavailable.json()["host_execution_fallback"] is False

        app.state.project_worker_service.record_runtime_heartbeat(
            "worker-endpoint",
            execution_backend="disabled",
            supported_attempt_types=(ExecutionAttemptType.PATCH,),
            supported_toolchains=("python",),
        )
        available = client.get("/chat/projects/runtime-capabilities")
        assert available.status_code == 200
        payload = available.json()
        assert payload["worker_available"] is True
        assert payload["active_workers"][0]["worker_id"] == "worker-endpoint"
