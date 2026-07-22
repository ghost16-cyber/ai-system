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
                "project_action_replays",
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


def test_canonical_action_requires_and_echoes_exact_backend_bindings(tmp_path):
    _database, client = _client(tmp_path)
    created = client.post("/chat/projects", json=_request(tmp_path)).json()
    project = created["project"]
    action = next(item for item in created["next_permitted_actions"] if item["action"] == "approve_plan")
    request = {
        "schema_version": "astra.project-api.action.v1",
        "conversation_id": project["conversation_id"],
        "workspace_id": project["workspace_id"],
        "actor_id": project["actor_id"],
        "repository_root_fingerprint": project["repository_root_fingerprint"],
        "expected_state_version": action["expected_state_version"],
        "idempotency_key": "approve-api-plan",
        "plan_revision_id": action["plan_revision_id"],
        "scope_revision_id": action["scope_revision_id"],
        "manifest_hash": action["manifest_hash"],
        "artifact_id": action["artifact_id"],
        "artifact_type": action["artifact_type"],
        "artifact_hash": action["artifact_hash"],
        "artifact_binding_hash": action["artifact_binding_hash"],
        "payload": action["payload"],
    }
    approved = client.post(
        f"/chat/projects/{project['project_run_id']}/actions/approve_plan",
        json=request,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["project"]["lifecycle_state"] == "ready_for_work"
    assert [item["action"] for item in approved.json()["next_permitted_actions"]] == ["cancel_project"]


def test_canonical_action_fails_closed_for_stale_state_and_wrong_identity(tmp_path):
    _database, client = _client(tmp_path)
    created = client.post("/chat/projects", json=_request(tmp_path)).json()
    project = created["project"]
    action = next(item for item in created["next_permitted_actions"] if item["action"] == "approve_plan")
    base = {
        "schema_version": "astra.project-api.action.v1",
        "conversation_id": project["conversation_id"], "workspace_id": project["workspace_id"],
        "actor_id": project["actor_id"],
        "repository_root_fingerprint": project["repository_root_fingerprint"],
        "expected_state_version": action["expected_state_version"] - 1,
        "idempotency_key": "stale-api-plan", "plan_revision_id": action["plan_revision_id"],
        "scope_revision_id": action["scope_revision_id"], "manifest_hash": action["manifest_hash"],
        "artifact_id": action["artifact_id"], "artifact_type": action["artifact_type"],
        "artifact_hash": action["artifact_hash"], "artifact_binding_hash": action["artifact_binding_hash"], "payload": {},
    }
    stale = client.post(f"/chat/projects/{project['project_run_id']}/actions/approve_plan", json=base)
    assert stale.status_code == 409
    wrong_identity = client.post(
        f"/chat/projects/{project['project_run_id']}/actions/approve_plan",
        json={**base, "expected_state_version": action["expected_state_version"], "workspace_id": "other"},
    )
    assert wrong_identity.status_code == 409
