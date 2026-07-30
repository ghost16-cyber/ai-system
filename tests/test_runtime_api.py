from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.runtime.routes import create_runtime_router
from tests.test_rag_integration import _fixture
from tests.test_runtime_manager import _runtime


def _client(tmp_path: Path):
    manager, _persistence, _database = _runtime(tmp_path)
    manager.initialize()
    app = FastAPI()
    app.include_router(create_runtime_router(manager))
    return TestClient(app), manager


def test_get_runtime_returns_snapshot(tmp_path: Path) -> None:
    client, _manager = _client(tmp_path)
    response = client.get("/runtime")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ready"
    assert "recent_transitions" in body


def test_get_runtime_health_returns_aggregated_report(tmp_path: Path) -> None:
    client, _manager = _client(tmp_path)
    response = client.get("/runtime/health")
    assert response.status_code == 200
    body = response.json()
    assert body["overall"] == "ready"
    assert len(body["subsystems"]) >= 5


def test_get_runtime_readiness_without_project_id(tmp_path: Path) -> None:
    client, _manager = _client(tmp_path)
    response = client.get("/runtime/readiness")
    assert response.status_code == 200
    assert response.json()["ready"] is True


def test_get_runtime_telemetry_returns_snapshot(tmp_path: Path) -> None:
    client, _manager = _client(tmp_path)
    response = client.get("/runtime/telemetry")
    assert response.status_code == 200
    assert "counters" in response.json()


def test_get_runtime_cache_returns_empty_when_unconfigured(tmp_path: Path) -> None:
    client, _manager = _client(tmp_path)
    response = client.get("/runtime/cache")
    assert response.status_code == 200
    assert response.json()["caches"] == []


def test_get_runtime_jobs_returns_zero_counts_when_unconfigured(tmp_path: Path) -> None:
    client, _manager = _client(tmp_path)
    response = client.get("/runtime/jobs")
    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 0
    assert body["claimed"] == 0


def test_get_runtime_corpus_requires_project_id(tmp_path: Path) -> None:
    client, _manager = _client(tmp_path)
    response = client.get("/runtime/corpus")
    assert response.status_code == 422


def test_get_runtime_corpus_404s_for_unknown_project(tmp_path: Path) -> None:
    manager, _persistence, database = _runtime(tmp_path)
    manager.initialize()
    from backend.app.runtime.corpus import CorpusManager
    from backend.app.runtime.background.queue import RuntimeJobQueue

    manager._corpus_manager = CorpusManager(manager._retrieval._service, RuntimeJobQueue(database))
    app = FastAPI()
    app.include_router(create_runtime_router(manager))
    client = TestClient(app)

    response = client.get("/runtime/corpus", params={"project_id": "does-not-exist"})
    assert response.status_code == 404


def test_get_runtime_corpus_returns_freshness_for_real_project(tmp_path: Path) -> None:
    """Uses a real canonical project + corpus manager (not the unconfigured
    stub) to prove the full read path end to end."""
    root, _source, database, control, artifacts, retrieval, binding = _fixture(tmp_path)
    from backend.app.runtime.corpus import CorpusManager
    from backend.app.runtime.background.queue import RuntimeJobQueue
    from tests.test_rag_integration import _ingest

    _ingest(retrieval, binding)
    manager, _persistence, _db = _runtime(tmp_path, name="unused.db")
    manager._retrieval._service = retrieval
    manager._corpus_manager = CorpusManager(retrieval, RuntimeJobQueue(database))

    app = FastAPI()
    app.include_router(create_runtime_router(manager))
    client = TestClient(app)
    response = client.get("/runtime/corpus", params={"project_id": binding["project_id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == binding["project_id"]
    assert body["freshness"]["fresh"] is True


def test_no_mutation_routes_are_exposed(tmp_path: Path) -> None:
    """RuntimeManager holds no retrieval/execution/mutation/approval
    authority, and the router must not expose any POST/PUT/PATCH/DELETE."""
    client, _manager = _client(tmp_path)
    for path in ("/runtime", "/runtime/health", "/runtime/readiness", "/runtime/cache", "/runtime/jobs"):
        for method in (client.post, client.put, client.patch, client.delete):
            response = method(path)
            assert response.status_code in (404, 405)
