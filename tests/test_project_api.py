from __future__ import annotations

import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.project_api import create_project_router
from backend.app.project_artifacts import ProjectArtifactStore
from backend.app.project_control.project_service import CanonicalProjectService
from backend.app.project_control.service import ProjectControlPlane


def _client(tmp_path):
    database = tmp_path / "api.db"
    artifacts = ProjectArtifactStore(database)
    control = ProjectControlPlane(database, artifact_store=artifacts)
    control.initialize()
    artifacts.initialize()
    service = CanonicalProjectService(control, artifacts)
    app = FastAPI()
    app.include_router(create_project_router(service))
    return database, TestClient(app)


def _request(tmp_path):
    return {
        "schema_version": "astra.project-api.create.v1",
        "conversation_id": "conversation-api",
        "workspace_id": "folder-api",
        "repository_root": str(tmp_path),
        "repository_root_fingerprint": "fingerprint-api",
        "idempotency_key": "request-api",
        "folder_authority": {
            "status": "completed",
            "action_id": "folder-api",
            "conversation_id": "conversation-api",
            "workspace_id": "folder-api",
            "repository_root_fingerprint": "fingerprint-api",
        },
        "specification": {
            "specification_id": "spec-api",
            "specification_hash": "a" * 64,
            "revision": 1,
            "included_paths": ["package.json"],
        },
        "manifest": {"manifest_hash": "b" * 64, "complete": True, "revision": 1},
        "plan": {"revision": 1, "acceptance_criteria": [], "work_units": []},
    }


def _database_snapshot(database):
    with sqlite3.connect(database) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "project_runs",
                "project_events",
                "project_artifacts",
                "project_idempotency",
            )
        )


def test_project_api_create_read_list_and_artifacts_are_typed(tmp_path):
    _database, client = _client(tmp_path)
    created = client.post("/chat/projects", json=_request(tmp_path))
    assert created.status_code == 201
    payload = created.json()
    project_id = payload["project"]["project_run_id"]
    assert payload["schema_version"] == "astra.project-api.project.v1"
    assert len(payload["artifacts"]) == 3

    assert client.get(f"/chat/projects/{project_id}").status_code == 200
    collection = client.get("/chat/conversations/conversation-api/projects").json()
    assert collection["count"] == 1
    artifacts = client.get(f"/chat/projects/{project_id}/artifacts").json()
    assert {item["artifact_type"] for item in artifacts} == {"specification", "manifest", "plan"}


def test_project_get_endpoints_are_side_effect_free(tmp_path):
    database, client = _client(tmp_path)
    created = client.post("/chat/projects", json=_request(tmp_path)).json()
    project_id = created["project"]["project_run_id"]
    before = _database_snapshot(database)

    for _ in range(2):
        assert client.get(f"/chat/projects/{project_id}").status_code == 200
        assert client.get("/chat/conversations/conversation-api/projects").status_code == 200
        assert client.get(f"/chat/projects/{project_id}/artifacts").status_code == 200

    assert _database_snapshot(database) == before


def test_duplicate_api_create_returns_same_backend_identity_without_duplicates(tmp_path):
    database, client = _client(tmp_path)
    first = client.post("/chat/projects", json=_request(tmp_path))
    before = _database_snapshot(database)
    second = client.post("/chat/projects", json=_request(tmp_path))

    assert second.status_code == 201
    assert second.json()["project"]["project_run_id"] == first.json()["project"]["project_run_id"]
    assert _database_snapshot(database) == before
